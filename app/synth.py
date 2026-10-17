import json
import logging
import os

logger = logging.getLogger("merono-tts.synth")

MODELS = ("s2.1-pro-free", "s2.1-pro", "s2-pro", "drama-3")
FORMATS = ("mp3", "wav", "pcm", "opus")
LATENCIES = ("low", "normal", "balanced")
SAMPLE_RATES = (8000, 16000, 22050, 24000, 32000, 44100, 48000)
MP3_BITRATES = (64, 128, 192)

MEDIA_TYPES = {
    "mp3": "audio/mpeg",
    "wav": "audio/wav",
    "pcm": "audio/pcm",
    "opus": "audio/ogg",
}

DEFAULT_SYNTH = {
    "model": "s2.1-pro-free",
    "prosody_speed": 1.2,
    "prosody_volume": 5.0,
    "prosody_normalize_loudness": False,
    "temperature": 0.65,
    "top_p": 0.65,
    "repetition_penalty": 1.25,
    "chunk_length": 240,
    "min_chunk_length": 80,
    "max_new_tokens": 1024,
    "early_stop_threshold": 1.0,
    "normalize": False,
    "condition_on_previous_chunks": True,
    "latency": "normal",
    "format": "mp3",
    "sample_rate": 44100,
    "mp3_bitrate": 128,
}

_RANGES = {
    "prosody_speed": (0.5, 2.0),
    "prosody_volume": (-20.0, 20.0),
    "temperature": (0.0, 1.0),
    "top_p": (0.0, 1.0),
    "repetition_penalty": (1.0, 2.0),
    "early_stop_threshold": (0.0, 1.0),
    "chunk_length": (100, 300),
    "min_chunk_length": (0, 100),
    "max_new_tokens": (256, 2048),
}

_CHOICES = {
    "model": MODELS,
    "format": FORMATS,
    "latency": LATENCIES,
    "sample_rate": SAMPLE_RATES,
    "mp3_bitrate": MP3_BITRATES,
}

_FLAGS = {
    "prosody_normalize_loudness",
    "normalize",
    "condition_on_previous_chunks",
}


def _coerce(key: str, value):
    default = DEFAULT_SYNTH[key]
    if key in _FLAGS:
        if isinstance(value, str):
            return value.strip().lower() in {"true", "1", "yes", "on", "si", "sí"}
        return bool(value)
    if isinstance(default, int):
        value = int(value)
    elif isinstance(default, float):
        value = float(value)
    else:
        value = str(value).strip()

    if key in _CHOICES:
        if value not in _CHOICES[key]:
            raise ValueError(f"Valor no válido para '{key}': {value!r}")
        return value

    low, high = _RANGES[key]
    if not low <= value <= high:
        raise ValueError(f"'{key}' debe estar entre {low} y {high}")
    return value


def media_type_for(audio_format: str) -> str:
    return MEDIA_TYPES.get(audio_format, "audio/mpeg")


class SpeechConfigStore:
    """Ajustes de síntesis mutables desde el dashboard.

    El modelo inicial sale de FISH_MODEL; el resto usa los valores por defecto
    de la app. Los cambios se guardan en un JSON para sobrevivir a reinicios
    mientras el disco sea persistente (en Render free el disco es efímero).
    """

    def __init__(self, default_model: str = "", voice_id: str = "", path: str = ""):
        self._values = dict(DEFAULT_SYNTH)
        self._voice_id = voice_id
        self._path = path
        self._default_model = DEFAULT_SYNTH["model"]
        if default_model:
            try:
                self._default_model = _coerce("model", default_model)
            except (ValueError, TypeError):
                logger.warning("FISH_MODEL no válido: %r", default_model)
        self._values["model"] = self._default_model
        self._load()

    @property
    def voice_id(self) -> str:
        return self._voice_id

    def snapshot(self) -> dict:
        return dict(self._values)

    def fingerprint(self) -> str:
        return json.dumps(
            {"voice_id": self._voice_id, "settings": self._values},
            sort_keys=True,
            ensure_ascii=False,
        )

    def update(self, patch: dict) -> dict:
        if not isinstance(patch, dict):
            raise ValueError("Se esperaba un objeto JSON")
        validated = {}
        for key, value in patch.items():
            if key not in DEFAULT_SYNTH:
                continue
            validated[key] = _coerce(key, value)
        updated = dict(self._values)
        updated.update(validated)
        self._values = updated
        self._save()
        return self.snapshot()

    def reset(self) -> dict:
        self._values = dict(DEFAULT_SYNTH)
        self._values["model"] = self._default_model
        self._save()
        return self.snapshot()

    def _load(self) -> None:
        if not self._path or not os.path.exists(self._path):
            return
        try:
            with open(self._path, encoding="utf-8") as handle:
                stored = json.load(handle)
        except (OSError, ValueError):
            logger.warning("No se pudo leer %s; se usan los valores por defecto", self._path)
            return
        if not isinstance(stored, dict):
            return
        for key, value in stored.items():
            if key not in DEFAULT_SYNTH:
                continue
            try:
                self._values[key] = _coerce(key, value)
            except (ValueError, TypeError):
                logger.warning("Ajuste persistido no válido: %s=%r", key, value)

    def _save(self) -> None:
        if not self._path:
            return
        directory = os.path.dirname(os.path.abspath(self._path))
        os.makedirs(directory, exist_ok=True)
        tmp = self._path + ".tmp"
        try:
            with open(tmp, "w", encoding="utf-8") as handle:
                json.dump(self._values, handle, indent=2, ensure_ascii=False)
            os.replace(tmp, self._path)
        except OSError:
            logger.exception("No se pudieron guardar los ajustes en %s", self._path)
