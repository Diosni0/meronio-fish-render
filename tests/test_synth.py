import json

import pytest

from app.synth import DEFAULT_SYNTH, SpeechConfigStore


def test_model_comes_from_env_default() -> None:
    store = SpeechConfigStore(default_model="drama-3", voice_id="voice")
    assert store.snapshot()["model"] == "drama-3"
    assert store.snapshot()["temperature"] == DEFAULT_SYNTH["temperature"]


def test_update_switches_to_drama_3_and_persists(tmp_path) -> None:
    path = tmp_path / "settings.json"
    store = SpeechConfigStore(default_model="s2.1-pro-free", voice_id="voice", path=str(path))
    store.update({"model": "drama-3", "prosody_speed": 1.3, "temperature": 0.5})

    reread = SpeechConfigStore(default_model="s2.1-pro-free", voice_id="voice", path=str(path))
    assert reread.snapshot()["model"] == "drama-3"
    assert reread.snapshot()["prosody_speed"] == 1.3
    assert reread.snapshot()["temperature"] == 0.5
    assert json.loads(path.read_text(encoding="utf-8"))["model"] == "drama-3"


def test_invalid_model_is_rejected() -> None:
    store = SpeechConfigStore()
    with pytest.raises(ValueError):
        store.update({"model": "drama-4"})


def test_out_of_range_value_is_rejected() -> None:
    store = SpeechConfigStore()
    with pytest.raises(ValueError):
        store.update({"prosody_speed": 9})


def test_reset_restores_default_model() -> None:
    store = SpeechConfigStore(default_model="s2.1-pro-free")
    store.update({"model": "drama-3"})
    assert store.reset()["model"] == "s2.1-pro-free"


def test_fingerprint_changes_with_model() -> None:
    store = SpeechConfigStore(default_model="s2.1-pro-free", voice_id="voice")
    before = store.fingerprint()
    store.update({"model": "drama-3"})
    assert store.fingerprint() != before
