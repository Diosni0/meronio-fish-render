from types import SimpleNamespace
import asyncio
import json

import httpx

from app.services import SpeechService
from app.synth import SpeechConfigStore


def make_service() -> SpeechService:
    settings = SimpleNamespace(
        max_concurrency=1,
        max_pending=2,
        requests_per_minute=10,
        allowed_channel="alimentacionchino",
        fish_model="model",
        fish_voice_id="voice",
        fish_api_key="key",
        cache_ttl_seconds=60,
        cache_max_entries=2,
    )
    return SpeechService(httpx.AsyncClient(), settings)


def test_duplicate_event_is_coalesced_not_rejected() -> None:
    service = make_service()
    assert service.reserve_event("event-1") is True
    assert service.reserve_event("event-1") is False
    service.release_event("event-1")
    assert service.reserve_event("event-1") is True


def test_same_and_other_instances_share_reserved_event() -> None:
    service = make_service()
    assert service.reserve_event("event-2", "instance-a") is True
    assert service.reserve_event("event-2", "instance-a") is False
    assert service.reserve_event("event-2", "instance-b") is False


def test_role_namespaced_keys_are_independent() -> None:
    service = make_service()
    assert service.reserve_event(["stream:id:1", "stream:c:user:10:hola"], "instance-a") is True
    assert service.reserve_event(["preview:id:1", "preview:c:user:10:hola"], "instance-b") is True
    assert service.reserve_event(["stream:id:1", "stream:c:user:10:hola"], "instance-c") is False


def test_release_event_removes_all_keys() -> None:
    service = make_service()
    keys = ["stream:id:2", "stream:c:user:10:hola"]
    assert service.reserve_event(keys, "instance-a") is True
    service.release_event(keys, "instance-a")
    assert service.reserve_event(keys, "instance-b") is True


def test_touch_instance_reports_active_roles() -> None:
    service = make_service()
    service.touch_instance("instance-a", "stream")
    service.touch_instance("instance-b", "stream")
    service.touch_instance("instance-c", "preview")
    stats = service.stats()
    assert stats["instances"]["stream"] == 2
    assert stats["instances"]["preview"] == 1


def test_playback_lock_serializes_stream_instances() -> None:
    service = make_service()
    granted, _ = service.acquire_playback("stream", "instance-a", "event-1", 10000)
    assert granted is True
    granted, wait_ms = service.acquire_playback("stream", "instance-b", "event-2", 10000)
    assert granted is False
    assert wait_ms > 0
    service.release_playback("stream", "instance-a", "event-1")
    granted, _ = service.acquire_playback("stream", "instance-b", "event-2", 10000)
    assert granted is True


def test_playback_lock_separates_stream_and_preview() -> None:
    service = make_service()
    assert service.acquire_playback("stream", "instance-a", "event-1", 10000)[0] is True
    assert service.acquire_playback("preview", "instance-b", "event-1", 10000)[0] is True


def test_playback_lock_only_releases_for_owner() -> None:
    service = make_service()
    assert service.acquire_playback("stream", "instance-a", "event-1", 10000)[0] is True
    service.release_playback("stream", "instance-b", "event-1")
    assert service.acquire_playback("stream", "instance-b", "event-2", 10000)[0] is False
    service.release_playback("stream", "instance-a", "event-1")
    assert service.acquire_playback("stream", "instance-b", "event-2", 10000)[0] is True


def test_dialogue_payload_uses_reference_id_array() -> None:
    captured = {}

    def handler(request: httpx.Request) -> httpx.Response:
        captured["payload"] = json.loads(request.content)
        return httpx.Response(200, content=b"audio")

    async def run() -> None:
        async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
            settings = SimpleNamespace(
                max_concurrency=1,
                max_pending=2,
                requests_per_minute=10,
                allowed_channel="alimentacionchino",
                fish_model="drama-3",
                fish_voice_id="v1",
                fish_api_key="key",
                cache_ttl_seconds=60,
                cache_max_entries=2,
            )
            config = SpeechConfigStore(
                default_model="drama-3",
                voice_id="v1",
                voice_merono="v1",
                voice_supin="v2",
                voice_colombiana="v3",
            )
            service = SpeechService(client, settings, config)
            await service.synthesize("@meroño@ hola @supin@ hey")

    asyncio.run(run())
    assert captured["payload"]["reference_id"] == ["v1", "v2"]
    assert captured["payload"]["text"] == "<|speaker:0|>hola <|speaker:1|>hey"


def test_single_colombiana_sends_single_reference_id() -> None:
    captured = {}

    def handler(request: httpx.Request) -> httpx.Response:
        captured["payload"] = json.loads(request.content)
        return httpx.Response(200, content=b"audio")

    async def run() -> None:
        async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
            settings = SimpleNamespace(
                max_concurrency=1,
                max_pending=2,
                requests_per_minute=10,
                allowed_channel="alimentacionchino",
                fish_model="drama-3",
                fish_voice_id="v1",
                fish_api_key="key",
                cache_ttl_seconds=60,
                cache_max_entries=2,
            )
            config = SpeechConfigStore(
                default_model="drama-3",
                voice_id="v1",
                voice_merono="v1",
                voice_supin="v2",
                voice_colombiana="v3",
            )
            service = SpeechService(client, settings, config)
            await service.synthesize("@colombiana@ hola")

    asyncio.run(run())
    assert captured["payload"]["reference_id"] == "v3"
    assert captured["payload"]["text"] == "hola"
