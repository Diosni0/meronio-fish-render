import os
from dataclasses import dataclass


def _int_env(name: str, default: int, minimum: int, maximum: int) -> int:
    try:
        value = int(os.getenv(name, str(default)))
    except ValueError:
        value = default
    return max(minimum, min(maximum, value))


def _list_env(name: str, default: str) -> list[str]:
    configured = [item.strip() for item in os.getenv(name, default).split(",") if item.strip()]
    defaults = [item.strip() for item in default.split(",") if item.strip()]
    return list(dict.fromkeys(configured + defaults))


@dataclass(frozen=True)
class Settings:
    fish_api_key: str
    fish_voice_id: str
    fish_model: str
    widget_token: str
    allowed_channel: str
    min_bits: int
    max_text_length: int
    cache_ttl_seconds: int
    cache_max_entries: int
    max_concurrency: int
    max_pending: int
    requests_per_minute: int
    origins: tuple[str, ...]
    channel_emote_prefix: str
    public_base_url: str

    @classmethod
    def from_env(cls) -> "Settings":
        origins = _list_env(
            "CORS_ORIGINS",
            "https://streamelements.com,https://www.streamelements.com",
        )
        return cls(
            fish_api_key=os.getenv("FISH_API_KEY", "").strip(),
            fish_voice_id=os.getenv("FISH_VOICE_ID", "").strip(),
            fish_model=os.getenv("FISH_MODEL", "s2.1-pro-free").strip(),
            widget_token=os.getenv("WIDGET_TOKEN", "").strip(),
            allowed_channel=os.getenv("ALLOWED_CHANNEL", "alimentacionchino").strip().lower(),
            min_bits=_int_env("MIN_BITS", 10, 1, 100000),
            max_text_length=_int_env("MAX_TEXT_LENGTH", 600, 80, 2000),
            cache_ttl_seconds=_int_env("AUDIO_CACHE_TTL", 1800, 30, 86400),
            cache_max_entries=_int_env("AUDIO_CACHE_MAX", 100, 1, 5000),
            max_concurrency=_int_env("TTS_MAX_CONCURRENCY", 2, 1, 8),
            max_pending=_int_env("TTS_MAX_PENDING", 8, 1, 100),
            requests_per_minute=_int_env("REQUESTS_PER_MINUTE", 30, 1, 600),
            origins=tuple(origins),
            channel_emote_prefix=os.getenv("CHANNEL_EMOTE_PREFIX", "alimen1").strip(),
            public_base_url=os.getenv("PUBLIC_BASE_URL", "").strip().rstrip("/"),
        )
