from types import SimpleNamespace

import httpx
import pytest

from app.services import SpeechError, SpeechService


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


def test_duplicate_event_remains_reserved_after_rejection() -> None:
    service = make_service()
    service.reserve_event("event-1")
    with pytest.raises(SpeechError):
        service.reserve_event("event-1")
    service.release_event("event-1")
    service.reserve_event("event-1")


def test_same_instance_reclaims_event_for_retry() -> None:
    service = make_service()
    service.reserve_event("event-2", "instance-a")
    service.reserve_event("event-2", "instance-a")


def test_other_instance_gets_duplicate_for_same_event() -> None:
    service = make_service()
    service.reserve_event("event-3", "instance-a")
    with pytest.raises(SpeechError):
        service.reserve_event("event-3", "instance-b")
