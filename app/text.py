import re
import unicodedata

_HAN_RANGE = re.compile(r"[\u3400-\u4dbf\u4e00-\u9fff\uf900-\ufaff]")
_WHITESPACE = re.compile(r"\s+")
_CHEER_COMMAND = re.compile(r"^\s*!tts\b", re.IGNORECASE)
_CHEER_LABEL = re.compile(r"\bcheer\s*\d+\b", re.IGNORECASE)
_URL = re.compile(r"https?://\S+", re.IGNORECASE)
_MOJIBAKE_PAIR = re.compile(r"[ÃÂ][\x80-\xBF]")


def repair_mojibake(value: str) -> str:
    """Repara texto UTF-8 que llegó decodificado como Latin-1 (ej. Ã± -> ñ)."""
    text = value or ""

    def fix(match: re.Match) -> str:
        try:
            return match.group(0).encode("latin-1").decode("utf-8")
        except (UnicodeEncodeError, UnicodeDecodeError):
            return match.group(0)

    return _MOJIBAKE_PAIR.sub(fix, text)


def contains_han(value: str) -> bool:
    return bool(_HAN_RANGE.search(unicodedata.normalize("NFKC", value or "")))


def normalize_text(value: str, max_length: int, emote_prefix: str) -> str:
    text = repair_mojibake(value or "")
    text = unicodedata.normalize("NFKC", text)
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
    text = repair_mojibake(value or "")
    text = re.sub(r"[^A-Za-z0-9_\-\sÁÉÍÓÚÜÑáéíóúüñ]", "", text)
    return text.strip()[:80] or "Anónimo"
