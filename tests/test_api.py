from dataclasses import replace

from fastapi.testclient import TestClient

import app.main as main
from app.config import Settings


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
