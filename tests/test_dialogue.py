from app.dialogue import prepare_dialogue, strip_dialogue_tags
from app.text import repair_mojibake

VOICES = {
    "meroño": "v1",
    "supin": "v2",
    "colombiana": "v3",
}


def test_without_tags_keeps_single_voice() -> None:
    text, reference = prepare_dialogue("hola mundo", VOICES, "drama-3")
    assert text == "hola mundo"
    assert reference is None


def test_tags_switch_speakers_inline() -> None:
    text, reference = prepare_dialogue(
        "@meroño@ hola @supin@ qué tal @colombiana@ hola mi amor",
        VOICES,
        "drama-3",
    )
    assert reference == ["v1", "v2", "v3"]
    assert text == "<|speaker:0|>hola <|speaker:1|>qué tal <|speaker:2|>hola mi amor"


def test_text_before_first_tag_uses_default_speaker() -> None:
    text, reference = prepare_dialogue("intro @supin@ hola", VOICES, "drama-3")
    assert reference == ["v1", "v2"]
    assert text == "<|speaker:0|>intro <|speaker:1|>hola"


def test_accepts_merono_without_enye_and_any_case() -> None:
    text, reference = prepare_dialogue("@MERONO@ hola @SUPIN@ hey", VOICES, "drama-3")
    assert reference == ["v1", "v2"]
    assert text == "<|speaker:0|>hola <|speaker:1|>hey"


def test_last_tag_keeps_voice_until_end() -> None:
    text, reference = prepare_dialogue(
        "@meroño@ hola @supin@ esto sigue y sigue",
        VOICES,
        "drama-3",
    )
    assert reference == ["v1", "v2"]
    assert text == "<|speaker:0|>hola <|speaker:1|>esto sigue y sigue"


def test_merges_adjacent_same_speaker_segments() -> None:
    text, reference = prepare_dialogue(
        "@meroño@ hola @meroño@ otra vez @supin@ ey",
        VOICES,
        "drama-3",
    )
    assert reference == ["v1", "v2"]
    assert text == "<|speaker:0|>hola otra vez <|speaker:1|>ey"


def test_single_colombiana_uses_single_voice() -> None:
    text, reference = prepare_dialogue("@colombiana@ hola", VOICES, "drama-3")
    assert text == "hola"
    assert reference == "v3"


def test_single_colombiana_also_works_without_closing_at() -> None:
    text, reference = prepare_dialogue("@colombiana hola", VOICES, "drama-3")
    assert text == "hola"
    assert reference == "v3"


def test_multiple_tags_without_closing_at() -> None:
    text, reference = prepare_dialogue(
        "@supin hola @colombiana hey",
        VOICES,
        "drama-3",
    )
    assert reference == ["v2", "v3"]
    assert text == "<|speaker:0|>hola <|speaker:1|>hey"


def test_does_not_match_plural_word() -> None:
    text, reference = prepare_dialogue("@colombianas hola", VOICES, "drama-3")
    assert text == "@colombianas hola"
    assert reference is None


def test_mojibake_tag_is_repaired_before_parsing() -> None:
    text = repair_mojibake("@meroÃ±o@ hola @colombiana@ hey")
    dialogue, reference = prepare_dialogue(text, VOICES, "drama-3")
    assert reference == ["v1", "v3"]
    assert dialogue == "<|speaker:0|>hola <|speaker:1|>hey"


def test_single_supin_uses_single_voice_even_on_unsupported_model() -> None:
    text, reference = prepare_dialogue("@supin@ hola", VOICES, "s1")
    assert text == "hola"
    assert reference == "v2"


def test_only_new_characters_are_remapped_from_zero() -> None:
    text, reference = prepare_dialogue(
        "@supin@ hola @colombiana@ hey",
        VOICES,
        "drama-3",
    )
    assert reference == ["v2", "v3"]
    assert text == "<|speaker:0|>hola <|speaker:1|>hey"


def test_missing_voice_falls_back_to_single_voice_without_tags() -> None:
    text, reference = prepare_dialogue(
        "@meroño@ hola @supin@ hey",
        {"meroño": "v1", "supin": "", "colombiana": ""},
        "drama-3",
    )
    assert reference is None
    assert text == "hola hey"


def test_unsupported_model_falls_back_to_single_voice() -> None:
    text, reference = prepare_dialogue(
        "@meroño@ hola @supin@ hey",
        VOICES,
        "s1",
    )
    assert reference is None
    assert text == "hola hey"


def test_tags_only_are_removed() -> None:
    assert strip_dialogue_tags("@meroño@ @supin@ @colombiana@") == ""


def test_unknown_tags_are_left_as_text() -> None:
    text, reference = prepare_dialogue("@pepe@ hola", VOICES, "drama-3")
    assert text == "@pepe@ hola"
    assert reference is None
