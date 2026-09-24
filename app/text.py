import re
import unicodedata

_HAN_RANGE = re.compile(r"[\u3400-\u4dbf\u4e00-\u9fff\uf900-\ufaff]")
_WHITESPACE = re.compile(r"\s+")
_CHEER_COMMAND = re.compile(r"^\s*!tts\b", re.IGNORECASE)
_CHEER_LABEL = re.compile(r"\bcheer\s*\d+\b", re.IGNORECASE)
_URL = re.compile(r"https?://\S+", re.IGNORECASE)


def contains_han(value: str) -> bool:
    return bool(_HAN_RANGE.search(unicodedata.normalize("NFKC", value or "")))


def normalize_text(value: str, max_length: int, emote_prefix: str) -> str:
    text = unicodedata.normalize("NFKC", value or "")
    text = _CHEER_COMMAND.sub("", text)
    text = _CHEER_LABEL.sub("", text)
    text = _URL.sub("", text)
    if emote_prefix:
        prefix = re.escape(emote_prefix)
        text = re.sub(rf"(?<!\w){prefix}[A-Za-z0-9_]+\b", " ", text, flags=re.IGNORECASE)
    text = _WHITESPACE.sub(" ", text).strip()
    return text[:max_length]


def fallback_text(username: str, amount: int) -> str:
    safe_username = username.strip() or "Anónimo"
    return f"{safe_username} dona {amount} bits."


def clean_username(value: str) -> str:
    return re.sub(r"[^A-Za-z0-9_\-\s]", "", value or "").strip()[:80] or "Anónimo"
