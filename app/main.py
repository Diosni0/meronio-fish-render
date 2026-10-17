import hmac
import logging
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


class SpeechPayload(BaseModel):
    text: str = Field(default="", max_length=2000)
    username: str = Field(default="Anónimo", max_length=100)
    amount: int = Field(default=0, ge=0, le=100000000)
    event_id: str = Field(default="", max_length=200)
    channel: str = Field(default="", max_length=100)
    test: bool = False


class LifecyclePayload(BaseModel):
    live: bool


@asynccontextmanager
async def lifespan(application: FastAPI):
    application.state.http = httpx.AsyncClient(timeout=httpx.Timeout(60.0, connect=10.0))
    application.state.config = SpeechConfigStore(
        default_model=settings.fish_model,
        voice_id=settings.fish_voice_id,
        path=settings.settings_path,
    )
    application.state.speech = SpeechService(
        application.state.http,
        settings,
        application.state.config,
    )
    yield
    await application.state.http.aclose()


app = FastAPI(title="Meroño TTS", version="2.1.0", lifespan=lifespan)
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


@app.get("/health")
def health(request: Request) -> dict[str, object]:
    return {
        "status": "ok",
        "version": "2.1.0",
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


@app.post("/api/v1/speech")
async def speech(
    payload: SpeechPayload,
    request: Request,
    x_widget_token: str | None = Header(default=None),
    x_widget_instance: str = Header(default=""),
) -> Response:
    _authenticate(x_widget_token)
    service = _service(request)
    instance = (x_widget_instance or "unknown")[:12]
    event_reserved = False
    try:
        service.check_rate_limit()
        service.check_channel(payload.channel)
        event_reserved = service.reserve_event(payload.event_id, x_widget_instance)
        text = normalize_text(payload.text, settings.max_text_length, settings.channel_emote_prefix)
        username = clean_username(payload.username)
        if not text:
            text = fallback_text(username, payload.amount)
        audio, cache_status, media_type = await service.synthesize(text)
    except SpeechError as error:
        if event_reserved and payload.event_id:
            service.release_event(payload.event_id, x_widget_instance)
        service.record_event("error", status=error.status_code, instance=instance)
        logger.warning("speech %s instance=%s event=%s", error.status_code, instance, payload.event_id or "-")
        raise _payload_error(error) from error
    except Exception:
        if event_reserved and payload.event_id:
            service.release_event(payload.event_id, x_widget_instance)
        service.record_event("error", status=500, instance=instance)
        logger.exception("Unexpected synthesis error instance=%s", instance)
        raise HTTPException(status_code=500, detail="Error interno de síntesis") from None
    service.record_event(
        "speech",
        username=username,
        text=text[:200],
        amount=payload.amount,
        instance=instance,
        cache=cache_status,
    )
    logger.info("speech 200 instance=%s event=%s cache=%s bytes=%s", instance, payload.event_id or "-", cache_status, len(audio))
    return Response(
        content=audio,
        media_type=media_type,
        headers={
            "X-Cache": cache_status,
            "X-Audio-Bytes": str(len(audio)),
            "X-Widget-Instance": x_widget_instance,
            "Cache-Control": "no-store",
        },
    )


@app.post("/api/v1/lifecycle")
def lifecycle(payload: LifecyclePayload, request: Request, x_widget_token: str | None = Header(default=None)) -> dict[str, str]:
    _authenticate(x_widget_token)
    state = "live" if payload.live else "offline"
    _service(request).record_event("lifecycle", state=state)
    logger.info("Widget lifecycle=%s", state)
    return {"status": state}
