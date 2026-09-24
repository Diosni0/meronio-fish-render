from app.text import contains_han, normalize_text


def test_normalizes_cheer_command_and_emotes() -> None:
    value = normalize_text(" !tts  hola   alimen1Spin ", 100, "alimen1")
    assert value == "hola"


def test_detects_han_after_ascii_text() -> None:
    assert contains_han("hello 你好")


def test_does_not_detect_latin_text() -> None:
    assert not contains_han("hola mundo")
