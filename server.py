"""
Meroño TTS — backend mínimo para Render.

Un solo servicio FastAPI:
  GET  /health                 chequeo de salud (lo usa Render)
  GET  / , /widget             overlay para StreamElements (con la URL pública inyectada)
  POST /streamelements/redeem  texto -> audio MP3 (Fish Audio)
  /images/*                    avatares de la burbuja

Sin dedup 204 ni roles: siempre devuelve audio (caché o síntesis).
Quien evita repetir un cheer es cada widget (localStorage, 300 s).
"""

import asyncio
import logging
import os
import re
import sys
import time
import traceback
from collections import OrderedDict
from contextlib import asynccontextmanager

import httpx
from dotenv import load_dotenv
from fastapi import FastAPI, Form, HTTPException, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import Response
from fastapi.staticfiles import StaticFiles

load_dotenv()

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s.%(msecs)03dZ [%(levelname)s] %(message)s",
    datefmt="%Y-%m-%dT%H:%M:%S",
    handlers=[logging.StreamHandler(sys.stdout)],
)
logging.Formatter.converter = time.gmtime
_log = logging.getLogger("merono-tts")

API_VERSION = "1.0.1"

FISH_API_KEY = os.getenv("FISH_API_KEY", "")
FISH_VOICE_ID = os.getenv("FISH_VOICE_ID", "")
FISH_API_URL = "https://api.fish.audio/v1/tts"
MODEL = os.getenv("FISH_MODEL", "s2.1-pro-free")
# URL pública del servicio (https://tu-app.onrender.com). Si se deja vacía
# se deduce de la petición (cabeceras X-Forwarded-* que pone Render).
PUBLIC_BASE_URL = os.getenv("PUBLIC_BASE_URL", "").rstrip("/")

RATE_LIMIT_MAX = int(os.getenv("REDEEM_RATE_LIMIT", "20"))
RATE_LIMIT_WINDOW = int(os.getenv("REDEEM_RATE_WINDOW", "60"))
AUDIO_CACHE_TTL = int(os.getenv("AUDIO_CACHE_TTL", "1800"))

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
WIDGET_PATH = os.path.join(BASE_DIR, "widget.html")
IMAGES_DIR = os.path.join(BASE_DIR, "images")

# Ajustes de voz que funcionaban en producción (antes en settings.json).
SETTINGS = {
    "prosody_speed": 1.2,
    "prosody_volume": 5,
    "prosody_normalize_loudness": False,
    "temperature": 0.65,
    "top_p": 0.65,
    "repetition_penalty": 1.25,
    "chunk_length": 240,
    "min_chunk_length": 80,
    "condition_on_previous_chunks": True,
    "latency": "normal",
    "normalize": False,
    "format": "mp3",
    "sample_rate": 44100,
    "mp3_bitrate": 128,
    "max_new_tokens": 1024,
    "early_stop_threshold": 1.0,
}


@asynccontextmanager
async def lifespan(_app: FastAPI):
    _app.state.http = httpx.AsyncClient(timeout=60.0)
    try:
        yield
    finally:
        await _app.state.http.aclose()


app = FastAPI(title="Merono TTS", version=API_VERSION, lifespan=lifespan)
app.add_middleware(
    CORSMiddleware, allow_origins=["*"], allow_methods=["*"], allow_headers=["*"]
)
os.makedirs(IMAGES_DIR, exist_ok=True)
app.mount("/images", StaticFiles(directory=IMAGES_DIR), name="images")


# ── Limpieza de texto ────────────────────────────────────────────────

BLACKLIST = [
    "moro", "morito", "maricón", "maricon", "marica",
    "gitano", "gitanico",
    "amariconar", "amariconado", "amariconao", "amariconamiento",
]

CHANNEL_EMOTES = [
    "alimen1Spin", "alimen1Chameleon", "alimen1Yeeeeah", "alimen1Wwww",
    "alimen1Cansado", "alimen1Wangbadan", "alimen1Karthus", "alimen1Karthus1",
    "alimen1Miralotu", "alimen1Jeje", "alimen1DJ", "alimen1Viejojosecontento",
    "alimen1Chorizon", "alimen1Cantando", "alimen1Baile", "alimen1Cani1",
    "alimen1Limpia", "alimen1Caducao", "alimen1Laosu", "alimen1Pobre",
    "alimen1Cocorataladrando", "alimen1Pinachan", "alimen1Xixilove1",
    "alimen1Xixipayaso", "alimen1Joseonfire", "alimen1Smile", "alimen1Amazing",
    "alimen1Eeewwwwww", "alimen1Mirada", "alimen1Bien", "alimen1Nihao",
    "alimen1Enfadado", "alimen1Dragon1", "alimen1Donutcaducao", "alimen1Litronas",
    "alimen1Forrao", "alimen1Neko", "alimen1Fiesta", "alimen1Winstontime",
    "alimen1GG1", "alimen1FFF", "alimen1Cotilla", "alimen1Palomo",
    "alimen1Viejojose", "alimen1Domado", "alimen1Cani", "alimen1EW",
    "alimen1Cocorata", "alimen1Pina", "alimen1Xixilove", "alimen1Xixiclown",
    "alimen1Sabroso", "alimen1Cigarrito", "alimen1Ecuatoliana", "alimen1GODblessme",
    "alimen1Rezar", "alimen1JoseDJ", "alimen1Lonely", "alimen1Godbless",
    "alimen1Joselove", "alimen1Jijiji", "alimen1Sisteryangbaila", "alimen1Huh",
    "alimen1OHNO", "alimen1Sisteryang", "alimen1Richman",
]

_BLACKLIST_PATTERNS = [
    (re.compile(re.escape(w), re.IGNORECASE), "*" * len(w)) for w in BLACKLIST
]
_EMOTE_PATTERN = re.compile(
    r"\b(?:" + "|".join(re.escape(e) for e in CHANNEL_EMOTES) + r")\b"
)
_WS_PATTERN = re.compile(r"\s+")
_CHEER_PATTERN = re.compile(r"\bcheer\s*\d+\b", re.IGNORECASE)


def prepare_text(raw: str) -> str:
    """Texto listo para sintetizar: sin cheer/emotes, censurado, máx 600."""
    text = _CHEER_PATTERN.sub("", raw or "").strip()
    text = _EMOTE_PATTERN.sub("", text)
    text = _WS_PATTERN.sub(" ", text).strip()
    if not text:
        text = "Chino espabila, que eres mongolo!"
    for pattern, replacement in _BLACKLIST_PATTERNS:
        text = pattern.sub(replacement, text)
    return text[:600]


# ── Caché de audio (LRU + TTL) + coalescing de peticiones en vuelo ───

_audio_cache: "OrderedDict[str, tuple]" = OrderedDict()
_CACHE_MAX = 100
_in_flight: dict = {}
_rate_hits: dict = {}


def _cache_get(key: str):
    entry = _audio_cache.get(key)
    if entry is None:
        return None
    audio, ts = entry
    if time.time() - ts > AUDIO_CACHE_TTL:
        del _audio_cache[key]
        return None
    _audio_cache.move_to_end(key)
    return audio


def _cache_set(key: str, audio: bytes):
    if key in _audio_cache:
        _audio_cache.move_to_end(key)
    elif len(_audio_cache) >= _CACHE_MAX:
        _audio_cache.popitem(last=False)
    _audio_cache[key] = (audio, time.time())


def _check_rate_limit(ip: str) -> bool:
    now = time.time()
    hits = [t for t in _rate_hits.get(ip, []) if now - t < RATE_LIMIT_WINDOW]
    _rate_hits[ip] = hits
    if len(hits) >= RATE_LIMIT_MAX:
        return False
    hits.append(now)
    return True


def _client_ip(request: Request) -> str:
    fwd = request.headers.get("x-forwarded-for", "")
    if fwd:
        return fwd.split(",")[0].strip()
    return request.client.host if request.client else "unknown"


def public_base_url(request: Request) -> str:
    """URL pública para inyectar en el widget (API + imágenes absolutas)."""
    if PUBLIC_BASE_URL:
        return PUBLIC_BASE_URL
    proto = request.headers.get("x-forwarded-proto", "").split(",")[0].strip()
    host = request.headers.get("x-forwarded-host", "").split(",")[0].strip()
    if not host:
        host = request.headers.get("host", "")
    if not proto:
        proto = request.url.scheme
    return f"{proto}://{host}".rstrip("/")


# ── Rutas ────────────────────────────────────────────────────────────

@app.get("/health")
def health():
    return {
        "status": "ok",
        "version": API_VERSION,
        "model": MODEL,
        "api_configured": bool(FISH_API_KEY),
        "voice_configured": bool(FISH_VOICE_ID),
    }


@app.get("/")
@app.get("/widget")
def widget(request: Request):
    if not os.path.exists(WIDGET_PATH):
        raise HTTPException(500, "widget.html no encontrado")
    with open(WIDGET_PATH, encoding="utf-8") as f:
        html = f.read().replace("__BASE_URL__", public_base_url(request))
    return Response(
        content=html,
        media_type="text/html",
        headers={"Cache-Control": "no-store, no-cache, must-revalidate"},
    )


@app.post("/streamelements/redeem")
async def streamelements_redeem(
    request: Request,
    text: str = Form(None),
    username: str = Form(None),
    amount: str = Form(None),
    instance_id: str = Form(""),
    event_id: str = Form(""),
):
    ip = _client_ip(request)
    if not _check_rate_limit(ip):
        raise HTTPException(429, "Demasiadas peticiones, espera unos segundos")

    if not text:
        try:
            body = await request.json()
            text = body.get("text", "")
            username = username or body.get("username", "")
            amount = amount or str(body.get("amount", "0"))
            instance_id = instance_id or body.get("instance_id", "")
            event_id = event_id or body.get("event_id", "")
        except Exception:
            pass

    if not text:
        raise HTTPException(400, "Se requiere el parámetro 'text'")

    if not FISH_API_KEY:
        raise HTTPException(503, "FISH_API_KEY no configurada")
    if not FISH_VOICE_ID:
        raise HTTPException(503, "FISH_VOICE_ID no configurado")

    text = prepare_text(text)
    username = (username or "Anónimo").lower()
    amount = amount or "0"

    _log.info("[TTS] %s (%s bits) [%s chars] instancia=%s ip=%s: %s",
              username, amount, len(text), instance_id, ip, text[:200])

    # 1. Caché: el mismo texto no se vuelve a sintetizar.
    if _cache_get(text) is not None:
        return Response(
            content=_cache_get(text),
            media_type="audio/mpeg",
            headers={"X-Cache": "HIT", "Access-Control-Allow-Origin": "*"},
        )

    # 2. Si otro widget ya lo está sintetizando, esperar su resultado.
    if text in _in_flight:
        await _in_flight[text].wait()
        cached = _cache_get(text)
        if cached is not None:
            return Response(
                content=cached,
                media_type="audio/mpeg",
                headers={"X-Cache": "HIT", "Access-Control-Allow-Origin": "*"},
            )

    event = asyncio.Event()
    _in_flight[text] = event
    try:
        s = SETTINGS
        client = request.app.state.http
        response = await client.post(
            FISH_API_URL,
            headers={
                "Authorization": f"Bearer {FISH_API_KEY}",
                "Content-Type": "application/json",
                "model": MODEL,
            },
            json={
                "text": text,
                "reference_id": FISH_VOICE_ID,
                "format": s["format"],
                "sample_rate": s["sample_rate"],
                "mp3_bitrate": s["mp3_bitrate"],
                "latency": s["latency"],
                "normalize": s["normalize"],
                "prosody": {
                    "speed": s["prosody_speed"],
                    "volume": s["prosody_volume"],
                    "normalize_loudness": s["prosody_normalize_loudness"],
                },
                "temperature": s["temperature"],
                "top_p": s["top_p"],
                "repetition_penalty": s["repetition_penalty"],
                "chunk_length": s["chunk_length"],
                "min_chunk_length": s["min_chunk_length"],
                "condition_on_previous_chunks": s["condition_on_previous_chunks"],
                "max_new_tokens": s["max_new_tokens"],
                "early_stop_threshold": s["early_stop_threshold"],
            },
        )
        if response.status_code != 200:
            _log.error("[TTS] Error API %s: %s",
                       response.status_code, response.text[:200])
            raise HTTPException(response.status_code,
                                f"Fish Audio API error: {response.text[:200]}")
        audio = response.content
        _log.info("[TTS] Audio generado: %s bytes", len(audio))
        _cache_set(text, audio)
        return Response(
            content=audio,
            media_type="audio/mpeg",
            headers={"X-Cache": "MISS", "Access-Control-Allow-Origin": "*"},
        )
    except httpx.TimeoutException:
        raise HTTPException(504, "Timeout llamando a Fish Audio API")
    except HTTPException:
        raise
    except Exception as e:
        _log.error("Error inesperado: %s\n%s", e, traceback.format_exc())
        raise HTTPException(500, f"Error interno: {e}")
    finally:
        _in_flight.pop(text, None)
        event.set()


if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host="0.0.0.0", port=int(os.getenv("PORT", 8080)))
