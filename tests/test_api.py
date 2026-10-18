from dataclasses import replace

from fastapi.testclient import TestClient

import app.main as main
from app.config import Settings
from app.services import SpeechService


async def fake_synthesize(self, text, supports_clips=False):
    return b"audio", "MISS", "audio/mpeg"


def speech_payload(**overrides):
    payload = {
        "text": "hola",
        "username": "user",
        "amount": 100,
        "event_id": "evt-1",
        "channel": "alimentacionchino",
        "role": "stream",
        "instance_id": "instance-a",
    }
    payload.update(overrides)
    return payload


def make_client(tmp_path, monkeypatch) -> TestClient:
    configured = replace(
        main.settings,
        fish_api_key="key",
        fish_voice_id="voice",
        widget_token="widget",
        admin_token="admin",
        settings_path=str(tmp_path / "settings.json"),
    )
    monkeypatch.setattr(main, "settings", configured)
    return TestClient(main.app)


def test_dashboard_and_settings_are_served(tmp_path, monkeypatch) -> None:
    with make_client(tmp_path, monkeypatch) as client:
        assert client.get("/dashboard").status_code == 200
        assert client.get("/widget").status_code == 200
        payload = client.get("/api/settings").json()
        assert "drama-3" in payload["models"]
        assert payload["settings"]["model"] == main.settings.fish_model


def test_settings_require_admin_token(tmp_path, monkeypatch) -> None:
    with make_client(tmp_path, monkeypatch) as client:
        assert client.post("/api/settings", json={"model": "drama-3"}).status_code == 401
        response = client.post(
            "/api/settings",
            json={"model": "drama-3", "prosody_speed": 1.3},
            headers={"X-Admin-Token": "admin"},
        )
        assert response.status_code == 200
        assert response.json()["settings"]["model"] == "drama-3"
        assert client.get("/health").json()["model"] == "drama-3"


def test_invalid_settings_are_rejected(tmp_path, monkeypatch) -> None:
    with make_client(tmp_path, monkeypatch) as client:
        response = client.post(
            "/api/settings",
            json={"temperature": 9},
            headers={"X-Admin-Token": "admin"},
        )
        assert response.status_code == 400


def test_second_stream_instance_gets_409(tmp_path, monkeypatch) -> None:
    monkeypatch.setattr(SpeechService, "synthesize", fake_synthesize)
    headers = {"X-Widget-Token": "widget"}
    with make_client(tmp_path, monkeypatch) as client:
        first = client.post("/api/v1/speech", json=speech_payload(), headers=headers)
        second = client.post(
            "/api/v1/speech",
            json=speech_payload(instance_id="instance-b"),
            headers=headers,
        )
        preview = client.post(
            "/api/v1/speech",
            json=speech_payload(role="preview", instance_id="instance-c"),
            headers=headers,
        )
        assert first.status_code == 200
        assert second.status_code == 409
        assert preview.status_code == 200
        stats = client.get("/api/stats").json()
        assert stats["instances"]["stream"] == 2
        assert stats["instances"]["preview"] == 1
        assert stats["counts"]["duplicate"] == 1


def test_content_dedup_without_event_id(tmp_path, monkeypatch) -> None:
    monkeypatch.setattr(SpeechService, "synthesize", fake_synthesize)
    headers = {"X-Widget-Token": "widget"}
    with make_client(tmp_path, monkeypatch) as client:
        first = client.post(
            "/api/v1/speech",
            json=speech_payload(event_id="", instance_id="instance-a"),
            headers=headers,
        )
        second = client.post(
            "/api/v1/speech",
            json=speech_payload(event_id="", instance_id="instance-b"),
            headers=headers,
        )
        assert first.status_code == 200
        assert second.status_code == 409


def test_playback_lock_api_serializes_instances(tmp_path, monkeypatch) -> None:
    headers = {"X-Widget-Token": "widget"}
    with make_client(tmp_path, monkeypatch) as client:
        acquire = "/api/v1/playback/acquire"
        release = "/api/v1/playback/release"
        first = client.post(
            acquire,
            json={"instance_id": "a", "role": "stream", "event_id": "e1", "duration_ms": 10000},
            headers=headers,
        )
        second = client.post(
            acquire,
            json={"instance_id": "b", "role": "stream", "event_id": "e2", "duration_ms": 10000},
            headers=headers,
        )
        assert first.status_code == 200
        assert first.json()["granted"] is True
        assert second.status_code == 200
        assert second.json()["granted"] is False
        assert second.json()["retry_after_ms"] > 0

        client.post(
            release,
            json={"instance_id": "a", "role": "stream", "event_id": "e1"},
            headers=headers,
        )
        third = client.post(
            acquire,
            json={"instance_id": "b", "role": "stream", "event_id": "e2", "duration_ms": 10000},
            headers=headers,
        )
        assert third.json()["granted"] is True
