from types import SimpleNamespace

import httpx

from app.services import SpeechService


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
