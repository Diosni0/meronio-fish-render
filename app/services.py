import asyncio
import json
import time
from collections import OrderedDict, deque
from typing import Any

import httpx

from .synth import SpeechConfigStore, media_type_for


class SpeechError(Exception):
    def __init__(self, status_code: int, message: str):
        super().__init__(message)
        self.status_code = status_code
        self.message = message


class SpeechService:
    def __init__(self, client: httpx.AsyncClient, settings: Any, config: SpeechConfigStore | None = None):
        self.client = client
        self.settings = settings
        self.config = config or SpeechConfigStore(
            default_model=settings.fish_model,
            voice_id=settings.fish_voice_id,
        )
        self._cache: OrderedDict[str, tuple[float, bytes, str]] = OrderedDict()
        self._inflight: dict[str, asyncio.Future[tuple[bytes, str]]] = {}
        self._seen_events: OrderedDict[str, tuple[float, str]] = OrderedDict()
        self._requests: deque[float] = deque()
        self._pending = 0
        self._semaphore = asyncio.Semaphore(settings.max_concurrency)
        self._events: deque[dict] = deque(maxlen=100)
        self._counts: dict[str, int] = {}
        self._instances: dict[str, dict] = {}
        self._started = time.monotonic()

    def check_rate_limit(self) -> None:
        now = time.monotonic()
        while self._requests and now - self._requests[0] > 60:
            self._requests.popleft()
        if len(self._requests) >= self.settings.requests_per_minute:
            raise SpeechError(429, "Límite de solicitudes alcanzado")
        self._requests.append(now)

    def check_channel(self, channel: str) -> None:
        if channel.strip().lower() != self.settings.allowed_channel:
            raise SpeechError(403, "Canal no permitido")

    def reserve_event(self, keys: str | list[str], instance_id: str = "") -> bool:
        if isinstance(keys, str):
            keys = [keys]
        keys = [key for key in keys if key]
        if not keys:
            return False
        self._prune_events()
        now = time.monotonic()
        if any(key in self._seen_events for key in keys):
            for key in keys:
                if key in self._seen_events:
                    self._seen_events.move_to_end(key)
            return False
        for key in keys:
            self._seen_events[key] = (now, instance_id)
            self._seen_events.move_to_end(key)
        return True

    def release_event(self, keys: str | list[str], instance_id: str = "") -> None:
        if isinstance(keys, str):
            keys = [keys]
        for key in keys:
            existing = self._seen_events.get(key)
            if existing is None:
                continue
            _, owner = existing
            if not instance_id or owner == instance_id:
                self._seen_events.pop(key, None)

    def touch_instance(self, instance_id: str, role: str, state: str = "") -> None:
        if not instance_id:
            return
        now = time.monotonic()
        self._instances[instance_id] = {
            "role": role or "unknown",
            "state": state,
            "seen": now,
        }
        if len(self._instances) > 200:
            cutoff = now - 600
            for key in [key for key, value in self._instances.items() if value["seen"] < cutoff]:
                self._instances.pop(key, None)

    def _prune_events(self) -> None:
        cutoff = time.monotonic() - 300
        while self._seen_events and next(iter(self._seen_events.values()))[0] < cutoff:
            self._seen_events.popitem(last=False)

    def record_event(self, event_type: str, **data: Any) -> None:
        self._counts[event_type] = self._counts.get(event_type, 0) + 1
        self._events.append(
            {
                "time": time.time(),
                "iso_time": time.strftime("%Y-%m-%d %H:%M:%S"),
                "event_type": event_type,
                "data": data,
            }
        )

    def events(self, limit: int = 20) -> list[dict]:
        limit = max(1, min(limit, 100))
        return list(self._events)[-limit:]

    def stats(self) -> dict:
        now = time.monotonic()
        cutoff = now - 600
        active = [value for value in self._instances.values() if value["seen"] >= cutoff]
        return {
            "model": self.config.snapshot()["model"],
            "cache": {
                "size": len(self._cache),
                "max_size": self.settings.cache_max_entries,
            },
            "instances": {
                "stream": sum(1 for value in active if value["role"] == "stream"),
                "preview": sum(1 for value in active if value["role"] == "preview"),
                "total": len(active),
            },
            "counts": dict(self._counts),
            "uptime_seconds": int(now - self._started),
        }

    def _cache_key(self, text: str, config: dict) -> str:
        payload = {
            "text": text,
            "voice_id": self.config.voice_id,
            "settings": config,
        }
        return json.dumps(payload, ensure_ascii=False, sort_keys=True)

    def _cache_get(self, key: str) -> tuple[bytes, str] | None:
        entry = self._cache.get(key)
        if entry is None:
            return None
        created_at, audio, media_type = entry
        if time.monotonic() - created_at > self.settings.cache_ttl_seconds:
            self._cache.pop(key, None)
            return None
        self._cache.move_to_end(key)
        return audio, media_type

    def _cache_set(self, key: str, audio: bytes, media_type: str) -> None:
        self._cache[key] = (time.monotonic(), audio, media_type)
        self._cache.move_to_end(key)
        while len(self._cache) > self.settings.cache_max_entries:
            self._cache.popitem(last=False)

    async def synthesize(self, text: str) -> tuple[bytes, str, str]:
        config = self.config.snapshot()
        key = self._cache_key(text, config)
        cached = self._cache_get(key)
        if cached is not None:
            self.record_event("cache_hit", text=text)
            return cached[0], "HIT", cached[1]

        existing = self._inflight.get(key)
        if existing is not None:
            self.record_event("cache_hit", text=text, coalesced=True)
            audio, media_type = await asyncio.shield(existing)
            return audio, "HIT", media_type

        if self._pending >= self.settings.max_pending:
            raise SpeechError(429, "La cola de síntesis está llena")

        loop = asyncio.get_running_loop()
        future: asyncio.Future[tuple[bytes, str]] = loop.create_future()
        self._inflight[key] = future
        self._pending += 1
        try:
            async with self._semaphore:
                audio = await self._request_fish(text, config)
            media_type = media_type_for(config["format"])
            self._cache_set(key, audio, media_type)
            if not future.done():
                future.set_result((audio, media_type))
            return audio, "MISS", media_type
        except Exception as exc:
            if not future.done():
                future.set_exception(exc)
            raise
        finally:
            self._pending -= 1
            self._inflight.pop(key, None)

    async def _request_fish(self, text: str, config: dict) -> bytes:
        if not self.settings.fish_api_key or not self.config.voice_id:
            raise SpeechError(503, "Fish Audio no está configurado")

        payload = {
            "text": text,
            "reference_id": self.config.voice_id,
            "format": config["format"],
            "sample_rate": config["sample_rate"],
            "mp3_bitrate": config["mp3_bitrate"],
            "latency": config["latency"],
            "normalize": config["normalize"],
            "prosody": {
                "speed": config["prosody_speed"],
                "volume": config["prosody_volume"],
                "normalize_loudness": config["prosody_normalize_loudness"],
            },
            "temperature": config["temperature"],
            "top_p": config["top_p"],
            "repetition_penalty": config["repetition_penalty"],
            "chunk_length": config["chunk_length"],
            "min_chunk_length": config["min_chunk_length"],
            "condition_on_previous_chunks": config["condition_on_previous_chunks"],
            "max_new_tokens": config["max_new_tokens"],
            "early_stop_threshold": config["early_stop_threshold"],
        }
        headers = {
            "Authorization": f"Bearer {self.settings.fish_api_key}",
            "Content-Type": "application/json",
            "model": config["model"],
        }
        last_error = "Fish Audio no respondió"
        for attempt in range(3):
            try:
                response = await self.client.post(
                    "https://api.fish.audio/v1/tts",
                    headers=headers,
                    json=payload,
                )
            except httpx.TimeoutException as exc:
                last_error = "Fish Audio tardó demasiado"
                if attempt == 2:
                    raise SpeechError(504, last_error) from exc
            except httpx.HTTPError as exc:
                last_error = "No se pudo contactar con Fish Audio"
                if attempt == 2:
                    raise SpeechError(502, last_error) from exc
            else:
                if response.status_code == 200 and response.content:
                    return response.content
                if response.status_code in {408, 429} or response.status_code >= 500:
                    last_error = f"Fish Audio respondió {response.status_code}"
                    if attempt < 2:
                        await asyncio.sleep(0.5 * (2**attempt))
                        continue
                raise SpeechError(502, last_error)
        raise SpeechError(502, last_error)
