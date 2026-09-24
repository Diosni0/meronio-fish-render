from app.config import Settings


def test_custom_cors_origin_keeps_streamelements_defaults(monkeypatch) -> None:
    monkeypatch.setenv("CORS_ORIGINS", "https://example.com")
    settings = Settings.from_env()
    assert "https://example.com" in settings.origins
    assert "null" in settings.origins
    assert "https://streamelements.com" in settings.origins
    assert "https://www.streamelements.com" in settings.origins


def test_blank_cors_value_keeps_streamelements_defaults(monkeypatch) -> None:
    monkeypatch.setenv("CORS_ORIGINS", "")
    settings = Settings.from_env()
    assert "null" in settings.origins
    assert "https://streamelements.com" in settings.origins
    assert "https://www.streamelements.com" in settings.origins
