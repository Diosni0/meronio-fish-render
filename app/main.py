import hashlib
import hmac
import ipaddress
import json
import logging
import re
import sys
import threading
import time
from contextlib import asynccontextmanager
from pathlib import Path

import httpx
from fastapi import FastAPI, Header, HTTPException, Request
from fastapi.encoders import jsonable_encoder
from fastapi.exceptions import RequestValidationError
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse, JSONResponse, RedirectResponse, Response
from pydantic import BaseModel, Field
from starlette.staticfiles import StaticFiles

from .config import Settings
from .dialogue import strip_dialogue_tags
from .services import SpeechError, SpeechService
from .synth import MODELS, SpeechConfigStore
from .text import clean_username, fallback_text, normalize_text

settings = Settings.from_env()
logging.basicConfig(
    level=getattr(logging, settings.log_level, logging.INFO),
    format="%(asctime)s [%(name)s] %(message)s",
    stream=sys.stdout,
)
logger = logging.getLogger("merono-tts")

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


def _short_id(value: str, length: int = 12) -> str:
    return (value or "unknown")[:length]


def _text_hash(value: str) -> str:
    return hashlib.sha1(value.encode("utf-8")).hexdigest()[:10]


def _log_text(value: str, limit: int = 600) -> str:
    text = (value or "").replace("\r", " ").replace("\n", " ")
    return text if len(text) <= limit else text[:limit] + "..."


def _dedup_kind(key: str) -> str:
    parts = key.split(":", 2)
    return ":".join(parts[:2]) if len(parts) >= 2 else "unknown"


def _client_context(request: Request) -> str:
    """Contexto del cliente para diagnosticar instancias duplicadas.

    No se registra el Referer porque la URL del overlay de StreamElements
    contiene el token del overlay. El contexto que envía el widget incluye
    rol detectado, visibilidad, foco y si corre dentro de un iframe/OBS.
    """
    parts: list[str] = []
    ip = _client_ip(request)
    if ip:
        parts.append(f"ip={ip!r}")
        if _trusted_widget_request(request) and _public_ip(ip):
            isp = _isp_for(ip)
            if isp:
                parts.append(f"isp={isp!r}")
    user_agent = request.headers.get("user-agent", "")
    if user_agent:
        parts.append(f"ua={user_agent[:200]!r}")
    origin = request.headers.get("origin", "")
    if origin:
        parts.append(f"origin={origin!r}")
    widget_context = request.headers.get("x-widget-context", "")
    if widget_context:
        parts.append(f"client={widget_context[:500]}")
    return " ".join(parts)


def _client_ip(request: Request) -> str:
    forwarded = request.headers.get("x-forwarded-for", "")
    if forwarded:
        candidate = forwarded.split(",", 1)[0].strip()
        if candidate:
            return candidate
    return request.client.host if request.client else ""


def _public_ip(value: str) -> bool:
    try:
        address = ipaddress.ip_address(value)
    except ValueError:
        return False
    return not (
        address.is_private
        or address.is_loopback
        or address.is_link_local
        or address.is_multicast
        or address.is_reserved
        or address.is_unspecified
    )


def _trusted_widget_request(request: Request) -> bool:
    token = request.headers.get("x-widget-token", "")
    expected = getattr(settings, "widget_token", "") or ""
    if not token or not expected:
        return False
    try:
        return hmac.compare_digest(token, expected)
    except (TypeError, ValueError):
        return False


_ISP_CACHE: dict[str, tuple[float, str]] = {}
_ISP_PENDING: set[str] = set()
_ISP_LOCK = threading.Lock()
_ISP_TTL_SECONDS = 21600.0
_ISP_FAIL_TTL_SECONDS = 300.0


def _store_isp(ip: str, isp: str) -> None:
    with _ISP_LOCK:
        _ISP_CACHE[ip] = (time.monotonic(), isp)
        _ISP_PENDING.discard(ip)


def _fetch_isp(ip: str) -> None:
    isp = ""
    try:
        response = httpx.get(f"https://ipinfo.io/{ip}/json", timeout=3.0)
        data = response.json()
        isp = str(data.get("org") or data.get("isp") or "").strip()
    except Exception:
        isp = ""
    _store_isp(ip, isp)


def _isp_for(ip: str) -> str:
    now = time.monotonic()
    with _ISP_LOCK:
        entry = _ISP_CACHE.get(ip)
        if entry is not None:
            ttl = _ISP_TTL_SECONDS if entry[1] else _ISP_FAIL_TTL_SECONDS
            if now - entry[0] < ttl:
                return entry[1]
        if ip in _ISP_PENDING:
            return entry[1] if entry is not None else ""
        _ISP_PENDING.add(ip)
    threading.Thread(target=_fetch_isp, args=(ip,), daemon=True).start()
    return entry[1] if entry is not None else ""


def _client_context_data(request: Request) -> dict:
    try:
        data = json.loads(request.headers.get("x-widget-context", "") or "{}")
    except (TypeError, ValueError):
        return {}
    return data if isinstance(data, dict) else {}


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
    allow_headers=[
        "Content-Type",
        "X-Widget-Token",
        "X-Widget-Instance",
        "X-Widget-Context",
        "X-Widget-Version",
        "X-Admin-Token",
    ],
)
app.mount("/images", StaticFiles(directory="images"), name="images")


@app.exception_handler(RequestValidationError)
async def validation_error_handler(request: Request, exc: RequestValidationError) -> JSONResponse:
    details = [
        {"loc": error.get("loc"), "type": error.get("type"), "msg": error.get("msg")}
        for error in exc.errors()
    ]
    logger.warning(
        "validation 422 path=%s method=%s %s errors=%s",
        request.url.path,
        request.method,
        _client_context(request),
        details,
    )
    return JSONResponse(status_code=422, content={"detail": jsonable_encoder(exc.errors())})


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
        role = explicit.strip().lower()[:20]
        logger.debug("role detect source=explicit role=%s ua=%r", role, request.headers.get("user-agent", "")[:200])
        return role
    user_agent = request.headers.get("user-agent", "")
    if "OBS/" in user_agent or "obs-browser" in user_agent:
        logger.debug("role detect source=user-agent role=stream ua=%r", user_agent[:200])
        return "stream"
    logger.debug("role detect source=fallback role=preview ua=%r", user_agent[:200])
    return "preview"


def _authenticate(token: str | None, request: Request | None = None) -> None:
    if not settings.widget_token:
        raise HTTPException(status_code=503, detail="WIDGET_TOKEN no configurado")
    if not token or not hmac.compare_digest(token.encode(), settings.widget_token.encode()):
        logger.warning(
            "auth failure kind=widget token_present=%s path=%s %s",
            bool(token),
            request.url.path if request else "-",
            _client_context(request) if request else "",
        )
        raise HTTPException(status_code=401, detail="Widget no autorizado")


def _admin_token() -> str:
    return settings.admin_token or settings.widget_token


def _require_admin(token: str | None, request: Request | None = None) -> None:
    expected = _admin_token()
    if not expected:
        raise HTTPException(status_code=503, detail="ADMIN_TOKEN no configurado")
    if not token or not hmac.compare_digest(token.encode(), expected.encode()):
        logger.warning(
            "auth failure kind=admin token_present=%s path=%s %s",
            bool(token),
            request.url.path if request else "-",
            _client_context(request) if request else "",
        )
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
    _require_admin(x_admin_token, request)
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
    _require_admin(x_admin_token, request)
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
    _authenticate(x_widget_token, request)
    role = _detect_role(request, payload.role)
    instance = (payload.instance_id or "unknown")[:80]
    granted, wait_ms = _service(request).acquire_playback(
        role, instance, payload.event_id, payload.duration_ms
    )
    logger.info(
        "playback acquire granted=%s role=%s instance=%s event=%s duration_ms=%s wait_ms=%s %s",
        granted,
        role,
        _short_id(instance),
        payload.event_id or "-",
        payload.duration_ms,
        wait_ms,
        _client_context(request),
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
    _authenticate(x_widget_token, request)
    role = _detect_role(request, payload.role)
    instance = (payload.instance_id or "unknown")[:80]
    _service(request).release_playback(role, instance, payload.event_id)
    logger.info(
        "playback release role=%s instance=%s event=%s %s",
        role,
        _short_id(instance),
        payload.event_id or "-",
        _client_context(request),
    )
    return {"status": "ok"}


@app.post("/api/v1/speech")
async def speech(
    payload: SpeechPayload,
    request: Request,
    x_widget_token: str | None = Header(default=None),
    x_widget_instance: str = Header(default=""),
) -> Response:
    _authenticate(x_widget_token, request)
    service = _service(request)
    started = time.monotonic()
    instance = (payload.instance_id or x_widget_instance or "unknown")[:80]
    role = _detect_role(request, payload.role)
    if role == "stream" and _client_context_data(request).get("vis") == "hidden":
        service.record_event(
            "hidden_instance",
            role=role,
            instance=instance,
            event=payload.event_id,
        )
        logger.info(
            "speech 409 hidden-instance role=%s instance=%s event=%s user=%r text=%r %s",
            role,
            _short_id(instance),
            payload.event_id or "-",
            payload.username,
            _log_text(payload.text),
            _client_context(request),
        )
        raise HTTPException(status_code=409, detail="NOT_VISIBLE: Instancia oculta")
    previous = service.touch_instance(instance, role)
    if previous is not None and previous.get("role") != role:
        logger.warning(
            "instance role change instance=%s from=%s to=%s %s",
            _short_id(instance), previous.get("role"), role, _client_context(request)
        )
    event_reserved = False
    dedup_keys: list[str] = []
    text = ""
    username = ""
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
        event_reserved, conflict_key = service.reserve_event_detail(dedup_keys, instance)
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
                "speech 409 duplicate role=%s instance=%s event=%s conflict=%s user=%r text=%r raw_chars=%d chars=%s hash=%s test=%s %s",
                role,
                _short_id(instance),
                payload.event_id or "-",
                _dedup_kind(conflict_key),
                username,
                _log_text(text),
                len(payload.text),
                len(text),
                _text_hash(text),
                payload.test,
                _client_context(request),
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
                "speech %s role=%s instance=%s event=%s user=%r text=%r raw_chars=%d chars=%s hash=%s amount=%s test=%s elapsed_ms=%d %s",
                error.status_code,
                role,
                _short_id(instance),
                payload.event_id or "-",
                username,
                _log_text(text),
                len(payload.text),
                len(text),
                _text_hash(text) if text else "-",
                payload.amount,
                payload.test,
                int((time.monotonic() - started) * 1000),
                _client_context(request),
            )
        raise _payload_error(error) from error
    except Exception:
        if event_reserved and dedup_keys:
            service.release_event(dedup_keys, instance)
        service.record_event("error", status=500, instance=instance, role=role)
        logger.exception(
            "speech 500 role=%s instance=%s event=%s user=%r text=%r raw_chars=%d chars=%s elapsed_ms=%d %s",
            role,
            _short_id(instance),
            payload.event_id or "-",
            username,
            _log_text(text),
            len(payload.text),
            len(text),
            int((time.monotonic() - started) * 1000),
            _client_context(request),
        )
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
        "speech 200 role=%s instance=%s event=%s cache=%s bytes=%s user=%r text=%r raw_chars=%d chars=%s hash=%s amount=%s clips=%s test=%s elapsed_ms=%d %s",
        role,
        _short_id(instance),
        payload.event_id or "-",
        cache_status,
        len(audio),
        username,
        _log_text(text),
        len(payload.text),
        len(text),
        _text_hash(text),
        payload.amount,
        payload.supports_clips,
        payload.test,
        int((time.monotonic() - started) * 1000),
        _client_context(request),
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
    _authenticate(x_widget_token, request)
    state = "live" if payload.live else "offline"
    instance = (payload.instance_id or "unknown")[:80]
    role = _detect_role(request, payload.role)
    service = _service(request)
    if role == "stream" and _client_context_data(request).get("vis") == "hidden":
        logger.debug(
            "lifecycle ignored hidden role=%s instance=%s state=%s",
            role,
            _short_id(instance),
            state,
        )
        return {"status": state, "speaker": "false", "ignored": "hidden"}
    previous = service.touch_instance(instance, role, state)
    service.record_event("lifecycle", state=state, instance=instance, role=role)
    active_stream = service.active_instances("stream")
    logger.info(
        "Widget lifecycle=%s role=%s instance=%s previous_role=%s streams=%d previews=%d active_stream=%s %s",
        state,
        role,
        _short_id(instance),
        (previous or {}).get("role", "-"),
        len(active_stream),
        len(service.active_instances("preview")),
        [_short_id(value, 8) for value in active_stream],
        _client_context(request),
    )
    if state == "live" and role == "stream" and len(active_stream) > 1:
        logger.warning(
            "multiple stream instances active count=%d ids=%s: solo una reproduce y el resto puede perder eventos",
            len(active_stream),
            [_short_id(value) for value in active_stream],
        )
    return {"status": state}
