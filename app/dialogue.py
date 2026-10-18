import re

MULTI_SPEAKER_MODELS = {"s2-pro", "s2.1-pro", "s2.1-pro-free", "drama-3"}

CHARACTER_INDEX = {
    "meroño": 0,
    "merono": 0,
    "supin": 1,
    "colombiana": 2,
}

LABEL_PATTERN = re.compile(r"(?i)\b(meroño|merono|supin|colombiana)\s*:")
WHITESPACE = re.compile(r"\s+")


def has_dialogue_labels(text: str) -> bool:
    return bool(LABEL_PATTERN.search(text or ""))


def strip_dialogue_labels(text: str) -> str:
    return WHITESPACE.sub(" ", LABEL_PATTERN.sub("", text or "")).strip()


def prepare_dialogue(text: str, voices: dict, model: str) -> tuple[str, list[str] | None]:
    """Convierte `nombre: texto` en diálogo multi-hablante de Fish Audio.

    Sin etiquetas devuelve el texto intacto y `None` (voz única actual). Si hay
    etiquetas y el modelo lo soporta, devuelve el texto con `<|speaker:N|>` y el
    array de reference_id en el orden meroño, supin, colombiana.
    """
    text = (text or "").strip()
    if not has_dialogue_labels(text):
        return text, None

    ordered = [
        voices.get("meroño", ""),
        voices.get("supin", ""),
        voices.get("colombiana", ""),
    ]
    if model not in MULTI_SPEAKER_MODELS or not all(ordered):
        return strip_dialogue_labels(text), None

    parts = LABEL_PATTERN.split(text)
    segments: list[tuple[int, str]] = []

    leading = parts[0].strip()
    if leading:
        segments.append((0, leading))

    for index in range(1, len(parts) - 1, 2):
        name = parts[index].lower()
        content = parts[index + 1].strip()
        if not content:
            continue
        segments.append((CHARACTER_INDEX[name], content))

    if not segments:
        return strip_dialogue_labels(text), None

    merged: list[tuple[int, str]] = []
    for speaker, content in segments:
        if merged and merged[-1][0] == speaker:
            merged[-1] = (speaker, merged[-1][1] + " " + content)
        else:
            merged.append((speaker, content))

    dialogue = " ".join(
        f"<|speaker:{speaker}|>{content}" for speaker, content in merged
    )
    return dialogue, ordered
