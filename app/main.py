import logging
from contextlib import asynccontextmanager

import httpx
from fastapi import FastAPI, Header, HTTPException, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse, Response
from pydantic import BaseModel, Field
from starlette.staticfiles import StaticFiles

from .config import Settings
from .services import SpeechError, SpeechService
from .text import clean_username, fallback_text, normalize_text

logger = logging.getLogger("merono-tts")
settings = Settings.from_env()


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
    application.state.speech = SpeechService(application.state.http, settings)
    yield
    await application.state.http.aclose()


app = FastAPI(title="Meroño TTS", version="2.0.0", lifespan=lifespan)
app.add_middleware(
    CORSMiddleware,
    allow_origins=list(settings.origins),
    allow_origin_regex=r"https://([a-z0-9-]+\.)*streamelements\.com",
    allow_credentials=False,
    allow_methods=["GET", "POST"],
    allow_headers=["Content-Type", "X-Widget-Token", "X-Widget-Instance"],
)
app.mount("/images", StaticFiles(directory="images"), name="images")


def _service(request: Request) -> SpeechService:
    return request.app.state.speech


def _authenticate(token: str | None) -> None:
    if not settings.widget_token:
        raise HTTPException(status_code=503, detail="WIDGET_TOKEN no configurado")
    if not token or token != settings.widget_token:
        raise HTTPException(status_code=401, detail="Widget no autorizado")


def _payload_error(error: SpeechError) -> HTTPException:
    return HTTPException(status_code=error.status_code, detail=error.message)


@app.get("/health")
def health() -> dict[str, object]:
    return {
        "status": "ok",
        "version": "2.0.0",
        "channel": settings.allowed_channel,
        "fish_configured": bool(settings.fish_api_key and settings.fish_voice_id),
        "widget_configured": bool(settings.widget_token),
    }


@app.get("/ready")
def ready() -> JSONResponse:
    if not settings.fish_api_key or not settings.fish_voice_id or not settings.widget_token:
        return JSONResponse(status_code=503, content={"status": "not_ready"})
    return JSONResponse(content={"status": "ready"})


@app.post("/api/v1/speech")
async def speech(
    payload: SpeechPayload,
    request: Request,
    x_widget_token: str | None = Header(default=None),
    x_widget_instance: str = Header(default=""),
) -> Response:
    _authenticate(x_widget_token)
    service = _service(request)
    event_reserved = False
    try:
        service.check_rate_limit()
        service.check_channel(payload.channel)
        event_reserved = service.reserve_event(payload.event_id, x_widget_instance)
        text = normalize_text(payload.text, settings.max_text_length, settings.channel_emote_prefix)
        username = clean_username(payload.username)
        if not text:
            text = fallback_text(username, payload.amount)
        audio, cache_status = await service.synthesize(text)
    except SpeechError as error:
        if event_reserved and payload.event_id:
            service.release_event(payload.event_id, x_widget_instance)
        raise _payload_error(error) from error
    except Exception:
        if event_reserved and payload.event_id:
            service.release_event(payload.event_id, x_widget_instance)
        logger.exception("Unexpected synthesis error")
        raise HTTPException(status_code=500, detail="Error interno de síntesis") from None
    return Response(
        content=audio,
        media_type="audio/mpeg",
        headers={
            "X-Cache": cache_status,
            "X-Audio-Bytes": str(len(audio)),
            "Cache-Control": "no-store",
        },
    )


@app.post("/api/v1/lifecycle")
def lifecycle(payload: LifecyclePayload, x_widget_token: str | None = Header(default=None)) -> dict[str, str]:
    _authenticate(x_widget_token)
    state = "live" if payload.live else "offline"
    logger.info("Widget lifecycle=%s", state)
    return {"status": state}
