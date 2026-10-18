import hmac
import logging
import re
import sys
from contextlib import asynccontextmanager
from pathlib import Path

import httpx
from fastapi import FastAPI, Header, HTTPException, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse, JSONResponse, RedirectResponse, Response
from pydantic import BaseModel, Field
from starlette.staticfiles import StaticFiles

from .config import Settings
from .dialogue import strip_dialogue_tags
from .services import SpeechError, SpeechService
from .synth import MODELS, SpeechConfigStore
from .text import clean_username, fallback_text, normalize_text

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(name)s] %(message)s",
    stream=sys.stdout,
)
logger = logging.getLogger("merono-tts")
settings = Settings.from_env()

BASE_DIR = Path(__file__).resolve().parent.parent
DASHBOARD_PATH = BASE_DIR / "dashboard.html"
WIDGET_PATH = BASE_DIR / "widget.html"

_MOJIBAKE_PAIR = re.compile(r"[ÃÂ][\x80-\xBF]")


def _repair_text(value: str) -> str:
    def fix(match: re.Match) -> str:
        try:
            return match.group(0).encode("latin-1").decode("utf-8")
        except (UnicodeEncodeError, UnicodeDecodeError):
            return match.group(0)

    return _MOJIBAKE_PAIR.sub(fix, value or "")


class SpeechPayload(BaseModel):
    text: str = Field(default="", max_length=2000)
    username: str = Field(default="Anónimo", max_length=100)
    amount: int = Field(default=0, ge=0, le=100000000)
    event_id: str = Field(default="", max_length=200)
    channel: str = Field(default="", max_length=100)
    role: str = Field(default="", max_length=20)
    instance_id: str = Field(default="", max_length=80)
    supports_clips: bool = False
    test: bool = False


class LifecyclePayload(BaseModel):
    live: bool
    role: str = Field(default="", max_length=20)
    instance_id: str = Field(default="", max_length=80)


class PlaybackPayload(BaseModel):
    instance_id: str = Field(default="", max_length=80)
    role: str = Field(default="", max_length=20)
    event_id: str = Field(default="", max_length=200)
    duration_ms: int = Field(default=0, ge=0, le=600000)


@asynccontextmanager
async def lifespan(application: FastAPI):
    application.state.http = httpx.AsyncClient(timeout=httpx.Timeout(60.0, connect=10.0))
    application.state.config = SpeechConfigStore(
        default_model=settings.fish_model,
        voice_id=settings.fish_voice_id,
        voice_merono=settings.fish_voice_merono,
        voice_supin=settings.fish_voice_supin,
        voice_colombiana=settings.fish_voice_colombiana,
        path=settings.settings_path,
    )
    application.state.speech = SpeechService(
        application.state.http,
        settings,
        application.state.config,
    )
    yield
    await application.state.http.aclose()


app = FastAPI(title="Meroño TTS", version="2.2.0", lifespan=lifespan)
app.add_middleware(
    CORSMiddleware,
    allow_origins=list(settings.origins),
    allow_origin_regex=r"https://([a-z0-9-]+\.)*streamelements\.com",
    allow_credentials=False,
    allow_methods=["GET", "POST"],
    allow_headers=["Content-Type", "X-Widget-Token", "X-Widget-Instance", "X-Admin-Token"],
)
app.mount("/images", StaticFiles(directory="images"), name="images")


def _service(request: Request) -> SpeechService:
    return request.app.state.speech


def _config(request: Request) -> SpeechConfigStore:
    return request.app.state.config


def _detect_role(request: Request, explicit: str = "") -> str:
    """Separa la reproducción del directo (stream) de una vista previa.

    El widget envía su rol detectado. Si no llega, se infiere del User-Agent de
    OBS y, en caso contrario, se asume preview. La dedup del servidor usa el rol:
    solo una instancia de cada rol reproduce cada evento.
    """
    if explicit:
        return explicit.strip().lower()[:20]
    user_agent = request.headers.get("user-agent", "")
    if "OBS/" in user_agent or "obs-browser" in user_agent:
        return "stream"
    return "preview"


def _authenticate(token: str | None) -> None:
    if not settings.widget_token:
        raise HTTPException(status_code=503, detail="WIDGET_TOKEN no configurado")
    if not token or not hmac.compare_digest(token.encode(), settings.widget_token.encode()):
        raise HTTPException(status_code=401, detail="Widget no autorizado")


def _admin_token() -> str:
    return settings.admin_token or settings.widget_token


def _require_admin(token: str | None) -> None:
    expected = _admin_token()
    if not expected:
        raise HTTPException(status_code=503, detail="ADMIN_TOKEN no configurado")
    if not token or not hmac.compare_digest(token.encode(), expected.encode()):
        raise HTTPException(status_code=401, detail="No autorizado")


def _settings_payload(request: Request) -> dict:
    return {
        "settings": _config(request).snapshot(),
        "models": list(MODELS),
        "voice_configured": bool(settings.fish_voice_id),
        "dialogue_configured": bool(
            settings.fish_voice_id
            and settings.fish_voice_supin
            and settings.fish_voice_colombiana
        ),
        "admin_required": bool(_admin_token()),
    }


def _payload_error(error: SpeechError) -> HTTPException:
    return HTTPException(status_code=error.status_code, detail=error.message)


@app.get("/")
def root() -> RedirectResponse:
    return RedirectResponse(url="/dashboard")


@app.get("/dashboard")
def dashboard() -> Response:
    if not DASHBOARD_PATH.exists():
        raise HTTPException(status_code=404, detail="dashboard.html no encontrado")
    return FileResponse(
        str(DASHBOARD_PATH),
        headers={"Cache-Control": "no-store, no-cache, must-revalidate"},
    )


@app.get("/widget")
def widget_source() -> Response:
    if not WIDGET_PATH.exists():
        raise HTTPException(status_code=404, detail="widget.html no encontrado")
    return FileResponse(
        str(WIDGET_PATH),
        media_type="text/html; charset=utf-8",
        headers={"Cache-Control": "no-store, no-cache, must-revalidate"},
    )


@app.get("/health")
def health(request: Request) -> dict[str, object]:
    return {
        "status": "ok",
        "version": "2.2.0",
        "channel": settings.allowed_channel,
        "model": _config(request).snapshot()["model"],
        "fish_configured": bool(settings.fish_api_key and settings.fish_voice_id),
        "widget_configured": bool(settings.widget_token),
    }


@app.get("/ready")
def ready() -> JSONResponse:
    if not settings.fish_api_key or not settings.fish_voice_id or not settings.widget_token:
        return JSONResponse(status_code=503, content={"status": "not_ready"})
    return JSONResponse(content={"status": "ready"})


@app.get("/api/settings")
def get_settings(request: Request) -> dict:
    return _settings_payload(request)


@app.post("/api/settings")
def update_settings(
    body: dict,
    request: Request,
    x_admin_token: str | None = Header(default=None),
) -> dict:
    _require_admin(x_admin_token)
    try:
        _config(request).update(body)
    except (ValueError, TypeError) as error:
        raise HTTPException(status_code=400, detail=str(error)) from error
    logger.info("Ajustes TTS actualizados: %s", _config(request).snapshot())
    return _settings_payload(request)


@app.post("/api/settings/reset")
def reset_settings(
    request: Request,
    x_admin_token: str | None = Header(default=None),
) -> dict:
    _require_admin(x_admin_token)
    _config(request).reset()
    logger.info("Ajustes TTS restaurados")
    return _settings_payload(request)


@app.get("/api/stats")
def get_stats(request: Request) -> dict:
    return _service(request).stats()


@app.get("/api/events")
def get_events(request: Request, limit: int = 20) -> list[dict]:
    return _service(request).events(limit)


@app.post("/api/v1/playback/acquire")
def acquire_playback(
    payload: PlaybackPayload,
    request: Request,
    x_widget_token: str | None = Header(default=None),
) -> dict:
    _authenticate(x_widget_token)
    role = _detect_role(request, payload.role)
    instance = (payload.instance_id or "unknown")[:80]
    granted, wait_ms = _service(request).acquire_playback(
        role, instance, payload.event_id, payload.duration_ms
    )
    return {
        "granted": granted,
        "retry_after_ms": 0 if granted else wait_ms,
        "role": role,
    }


@app.post("/api/v1/playback/release")
def release_playback(
    payload: PlaybackPayload,
    request: Request,
    x_widget_token: str | None = Header(default=None),
) -> dict[str, str]:
    _authenticate(x_widget_token)
    role = _detect_role(request, payload.role)
    instance = (payload.instance_id or "unknown")[:80]
    _service(request).release_playback(role, instance, payload.event_id)
    return {"status": "ok"}


@app.post("/api/v1/speech")
async def speech(
    payload: SpeechPayload,
    request: Request,
    x_widget_token: str | None = Header(default=None),
    x_widget_instance: str = Header(default=""),
) -> Response:
    _authenticate(x_widget_token)
    service = _service(request)
    instance = (payload.instance_id or x_widget_instance or "unknown")[:80]
    role = _detect_role(request, payload.role)
    service.touch_instance(instance, role)
    event_reserved = False
    dedup_keys: list[str] = []
    try:
        service.check_rate_limit()
        service.check_channel(payload.channel)
        text = normalize_text(_repair_text(payload.text), settings.max_text_length, settings.channel_emote_prefix)
        username = clean_username(_repair_text(payload.username))
        if not text or not strip_dialogue_tags(text):
            text = fallback_text(username, payload.amount)

        # Dos claves por evento: el id de StreamElements y la firma de contenido.
        # Así dos instancias que reciben el mismo cheer (o una reentrega de SE con
        # otro id) no reproducen el audio dos veces. El rol separa stream/preview.
        if payload.event_id.strip():
            dedup_keys.append(f"{role}:id:{payload.event_id.strip()}")
        dedup_keys.append(f"{role}:c:{username}:{payload.amount}:{text}")
        event_reserved = service.reserve_event(dedup_keys, instance)
        if not event_reserved:
            service.record_event(
                "duplicate",
                role=role,
                instance=instance,
                username=username,
                text=text[:200],
                amount=payload.amount,
            )
            logger.info(
                "speech 409 duplicate role=%s instance=%s event=%s",
                role, instance, payload.event_id or "-",
            )
            raise SpeechError(409, "Evento duplicado")

        audio, cache_status, media_type = await service.synthesize(
            text,
            supports_clips=payload.supports_clips,
        )
    except SpeechError as error:
        if event_reserved and dedup_keys:
            service.release_event(dedup_keys, instance)
        if error.status_code != 409:
            service.record_event("error", status=error.status_code, instance=instance, role=role)
        logger.warning(
            "speech %s role=%s instance=%s event=%s",
            error.status_code, role, instance, payload.event_id or "-",
        )
        raise _payload_error(error) from error
    except Exception:
        if event_reserved and dedup_keys:
            service.release_event(dedup_keys, instance)
        service.record_event("error", status=500, instance=instance, role=role)
        logger.exception("Unexpected synthesis error instance=%s", instance)
        raise HTTPException(status_code=500, detail="Error interno de síntesis") from None
    service.record_event(
        "speech",
        username=username,
        text=text[:200],
        amount=payload.amount,
        instance=instance,
        role=role,
        cache=cache_status,
    )
    logger.info(
        "speech 200 role=%s instance=%s event=%s cache=%s bytes=%s",
        role, instance, payload.event_id or "-", cache_status, len(audio),
    )
    return Response(
        content=audio,
        media_type=media_type,
        headers={
            "X-Cache": cache_status,
            "X-Audio-Bytes": str(len(audio)),
            "X-Widget-Instance": instance,
            "Cache-Control": "no-store",
        },
    )


@app.post("/api/v1/lifecycle")
def lifecycle(
    payload: LifecyclePayload,
    request: Request,
    x_widget_token: str | None = Header(default=None),
) -> dict[str, str]:
    _authenticate(x_widget_token)
    state = "live" if payload.live else "offline"
    instance = (payload.instance_id or "unknown")[:80]
    role = _detect_role(request, payload.role)
    service = _service(request)
    service.touch_instance(instance, role, state)
    service.record_event("lifecycle", state=state, instance=instance, role=role)
    logger.info("Widget lifecycle=%s role=%s instance=%s", state, role, instance)
    return {"status": state}
