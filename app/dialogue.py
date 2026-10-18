import re

MULTI_SPEAKER_MODELS = {"s2-pro", "s2.1-pro", "s2.1-pro-free", "drama-3"}

TAG_PATTERN = re.compile(
    r"@\s*(meroño|merono|supin|colombiana)\s*@"
    r"|@\s*(meroño|merono|supin|colombiana)(?![A-Za-z0-9_])",
    re.IGNORECASE,
)
WHITESPACE = re.compile(r"\s+")


def has_dialogue_tags(text: str) -> bool:
    return bool(TAG_PATTERN.search(text or ""))


def strip_dialogue_tags(text: str) -> str:
    return WHITESPACE.sub(" ", TAG_PATTERN.sub("", text or "")).strip()


def prepare_dialogue(text: str, voices: dict, model: str) -> tuple[str, list[str] | str | None]:
    """Convierte `@nombre@` en diálogo multi-hablante de Fish Audio.

    Sin marcas devuelve el texto intacto y `None` (voz única actual). Cada
    `@nombre@` cambia el hablante hasta la siguiente marca; no hay etiqueta de
    cierre. Los índices de speaker se asignan solo a los personajes usados, en
    orden de aparición. Si solo habla un personaje se usa la ruta de voz única.
    """
    text = (text or "").strip()
    if not has_dialogue_tags(text):
        return text, None

    raw_segments: list[tuple[str, str]] = []
    current_name = "meroño"
    position = 0
    for match in TAG_PATTERN.finditer(text):
        content = text[position:match.start()].strip()
        if content:
            raw_segments.append((current_name, content))
        name = (match.group(1) or match.group(2)).lower()
        if name == "merono":
            name = "meroño"
        current_name = name
        position = match.end()

    tail = text[position:].strip()
    if tail:
        raw_segments.append((current_name, tail))

    if not raw_segments:
        return strip_dialogue_tags(text), None

    used_names: list[str] = []
    for name, _ in raw_segments:
        if name not in used_names:
            used_names.append(name)

    used_voices: list[str] = []
    for name in used_names:
        voice = voices.get(name, "")
        if not voice:
            return strip_dialogue_tags(text), None
        used_voices.append(voice)

    if len(used_names) == 1:
        return strip_dialogue_tags(text), used_voices[0]

    if model not in MULTI_SPEAKER_MODELS:
        return strip_dialogue_tags(text), None

    index_by_name = {name: index for index, name in enumerate(used_names)}
    merged: list[tuple[str, str]] = []
    for name, content in raw_segments:
        if merged and merged[-1][0] == name:
            merged[-1] = (name, merged[-1][1] + " " + content)
        else:
            merged.append((name, content))

    dialogue = " ".join(
        f"<|speaker:{index_by_name[name]}|>{content}" for name, content in merged
    )
    return dialogue, used_voices
