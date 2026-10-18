from app.text import clean_username, contains_han, normalize_text, repair_mojibake


def test_normalizes_cheer_command_and_emotes() -> None:
    value = normalize_text(" !tts  hola   alimen1Spin ", 100, "alimen1")
    assert value == "hola"


def test_detects_han_after_ascii_text() -> None:
    assert contains_han("hello 你好")


def test_does_not_detect_latin_text() -> None:
    assert not contains_han("hola mundo")


def test_repairs_mojibake_accents() -> None:
    assert repair_mojibake("meroÃ±ito @meroÃ±o@") == "meroñito @meroño@"


def test_normalize_repairs_mojibake_before_filters() -> None:
    assert normalize_text("meroÃ±ito @meroÃ±o@", 100, "alimen1") == "meroñito @meroño@"


def test_clean_username_keeps_spanish_letters() -> None:
    assert clean_username("meroÃ±o") == "meroño"
