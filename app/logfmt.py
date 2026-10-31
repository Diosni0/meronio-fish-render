import re

_SIMPLE = re.compile(r"^[A-Za-z0-9_.:/@+\-]+$")


def _render_value(value: object) -> str:
    if isinstance(value, bool):
        return "1" if value else "0"
    if isinstance(value, (int, float)):
        return str(value)
    if isinstance(value, (list, tuple)):
        return "[" + ",".join(_render_value(item) for item in value) + "]"
    if value is None:
        return ""
    text = str(value)
    if not text:
        return ""
    if _SIMPLE.match(text):
        return text
    return repr(text)


def log_line(tag: str, action: str, **fields: object) -> str:
    parts = [f"[{tag.upper()}]", action]
    for key, value in fields.items():
        rendered = _render_value(value)
        if rendered == "":
            continue
        parts.append(f"{key}={rendered}")
    return " ".join(parts)
