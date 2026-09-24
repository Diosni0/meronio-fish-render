import asyncio
import json
import time
from collections import OrderedDict, deque
from typing import Any

import httpx


class SpeechError(Exception):
    def __init__(self, status_code: int, message: str):
        super().__init__(message)
        self.status_code = status_code
        self.message = message


class SpeechService:
    def __init__(self, client: httpx.AsyncClient, settings: Any):
        self.client = client
        self.settings = settings
        self._cache: OrderedDict[str, tuple[float, bytes]] = OrderedDict()
        self._inflight: dict[str, asyncio.Future[bytes]] = {}
        self._seen_events: OrderedDict[str, tuple[float, str]] = OrderedDict()
        self._requests: deque[float] = deque()
        self._pending = 0
        self._semaphore = asyncio.Semaphore(settings.max_concurrency)

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

    def reserve_event(self, event_id: str, instance_id: str = "") -> bool:
        if not event_id:
            return False
        self._prune_events()
        existing = self._seen_events.get(event_id)
        if existing is not None:
            self._seen_events.move_to_end(event_id)
            return False
        self._seen_events[event_id] = (time.monotonic(), instance_id)
        self._seen_events.move_to_end(event_id)
        return True

    def release_event(self, event_id: str, instance_id: str = "") -> None:
        if not event_id:
            return
        existing = self._seen_events.get(event_id)
        if existing is None:
            return
        _, owner = existing
        if not instance_id or owner == instance_id:
            self._seen_events.pop(event_id, None)

    def _prune_events(self) -> None:
        cutoff = time.monotonic() - 300
        while self._seen_events and next(iter(self._seen_events.values()))[0] < cutoff:
            self._seen_events.popitem(last=False)

    def _cache_key(self, text: str) -> str:
        payload = {
            "text": text,
            "model": self.settings.fish_model,
            "voice_id": self.settings.fish_voice_id,
            "speed": 1.2,
            "volume": 5,
            "sample_rate": 44100,
            "bitrate": 128,
            "format": "mp3",
        }
        return json.dumps(payload, ensure_ascii=False, sort_keys=True)

    def _cache_get(self, key: str) -> bytes | None:
        entry = self._cache.get(key)
        if entry is None:
            return None
        created_at, audio = entry
        if time.monotonic() - created_at > self.settings.cache_ttl_seconds:
            self._cache.pop(key, None)
            return None
        self._cache.move_to_end(key)
        return audio

    def _cache_set(self, key: str, audio: bytes) -> None:
        self._cache[key] = (time.monotonic(), audio)
        self._cache.move_to_end(key)
        while len(self._cache) > self.settings.cache_max_entries:
            self._cache.popitem(last=False)

    async def synthesize(self, text: str) -> tuple[bytes, str]:
        key = self._cache_key(text)
        cached = self._cache_get(key)
        if cached is not None:
            return cached, "HIT"

        existing = self._inflight.get(key)
        if existing is not None:
            return await asyncio.shield(existing), "HIT"

        if self._pending >= self.settings.max_pending:
            raise SpeechError(429, "La cola de síntesis está llena")

        loop = asyncio.get_running_loop()
        future: asyncio.Future[bytes] = loop.create_future()
        self._inflight[key] = future
        self._pending += 1
        try:
            async with self._semaphore:
                audio = await self._request_fish(text)
            self._cache_set(key, audio)
            if not future.done():
                future.set_result(audio)
            return audio, "MISS"
        except Exception as exc:
            if not future.done():
                future.set_exception(exc)
            raise
        finally:
            self._pending -= 1
            self._inflight.pop(key, None)

    async def _request_fish(self, text: str) -> bytes:
        if not self.settings.fish_api_key or not self.settings.fish_voice_id:
            raise SpeechError(503, "Fish Audio no está configurado")

        payload = {
            "text": text,
            "reference_id": self.settings.fish_voice_id,
            "format": "mp3",
            "sample_rate": 44100,
            "mp3_bitrate": 128,
            "latency": "normal",
            "normalize": False,
            "prosody": {
                "speed": 1.2,
                "volume": 5,
                "normalize_loudness": False,
            },
            "temperature": 0.65,
            "top_p": 0.65,
            "repetition_penalty": 1.25,
            "chunk_length": 240,
            "min_chunk_length": 80,
            "condition_on_previous_chunks": True,
            "max_new_tokens": 1024,
            "early_stop_threshold": 1.0,
        }
        headers = {
            "Authorization": f"Bearer {self.settings.fish_api_key}",
            "Content-Type": "application/json",
            "model": self.settings.fish_model,
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
