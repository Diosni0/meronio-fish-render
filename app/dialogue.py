import re

MULTI_SPEAKER_MODELS = {"s2-pro", "s2.1-pro", "s2.1-pro-free", "drama-3"}

CHARACTER_INDEX = {
    "meroño": 0,
    "merono": 0,
    "supin": 1,
    "colombiana": 2,
}

TAG_PATTERN = re.compile(r"@\s*(meroño|merono|supin|colombiana)\s*@", re.IGNORECASE)
WHITESPACE = re.compile(r"\s+")


def has_dialogue_tags(text: str) -> bool:
    return bool(TAG_PATTERN.search(text or ""))


def strip_dialogue_tags(text: str) -> str:
    return WHITESPACE.sub(" ", TAG_PATTERN.sub("", text or "")).strip()


def prepare_dialogue(text: str, voices: dict, model: str) -> tuple[str, list[str] | None]:
    """Convierte `@nombre@` en diálogo multi-hablante de Fish Audio.

    Sin marcas devuelve el texto intacto y `None` (voz única actual). Cada
    `@nombre@` cambia el hablante hasta la siguiente marca; no hay etiqueta de
    cierre. Si el modelo no lo soporta o faltan voces, se eliminan las marcas y
    se usa la voz única.
    """
    text = (text or "").strip()
    if not has_dialogue_tags(text):
        return text, None

    ordered = [
        voices.get("meroño", ""),
        voices.get("supin", ""),
        voices.get("colombiana", ""),
    ]
    if model not in MULTI_SPEAKER_MODELS or not all(ordered):
        return strip_dialogue_tags(text), None

    segments: list[tuple[int, str]] = []
    current_speaker = 0
    position = 0
    for match in TAG_PATTERN.finditer(text):
        content = text[position:match.start()].strip()
        if content:
            segments.append((current_speaker, content))
        current_speaker = CHARACTER_INDEX[match.group(1).lower()]
        position = match.end()

    tail = text[position:].strip()
    if tail:
        segments.append((current_speaker, tail))

    if not segments:
        return strip_dialogue_tags(text), None

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
