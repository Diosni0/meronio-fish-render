from app.dialogue import prepare_dialogue, strip_dialogue_labels

VOICES = {
    "meroño": "v1",
    "supin": "v2",
    "colombiana": "v3",
}


def test_without_labels_keeps_single_voice() -> None:
    text, reference_ids = prepare_dialogue("hola mundo", VOICES, "drama-3")
    assert text == "hola mundo"
    assert reference_ids is None


def test_dialogue_converts_labels_to_speaker_tags() -> None:
    text, reference_ids = prepare_dialogue(
        "meroño: hola supin: qué tal colombiana: hola mi amor",
        VOICES,
        "drama-3",
    )
    assert reference_ids == ["v1", "v2", "v3"]
    assert text == "<|speaker:0|>hola <|speaker:1|>qué tal <|speaker:2|>hola mi amor"


def test_leading_text_uses_default_speaker() -> None:
    text, reference_ids = prepare_dialogue("intro supin: hola", VOICES, "drama-3")
    assert reference_ids == ["v1", "v2", "v3"]
    assert text == "<|speaker:0|>intro <|speaker:1|>hola"


def test_accepts_merono_without_enye_and_any_case() -> None:
    text, reference_ids = prepare_dialogue("MERONO: hola SUPIN: hey", VOICES, "drama-3")
    assert reference_ids == ["v1", "v2", "v3"]
    assert text == "<|speaker:0|>hola <|speaker:1|>hey"


def test_unclosed_label_keeps_rest_in_that_voice() -> None:
    text, reference_ids = prepare_dialogue(
        "meroño: hola supin: esto sigue y sigue",
        VOICES,
        "drama-3",
    )
    assert reference_ids == ["v1", "v2", "v3"]
    assert text == "<|speaker:0|>hola <|speaker:1|>esto sigue y sigue"


def test_merges_adjacent_same_speaker_segments() -> None:
    text, _ = prepare_dialogue(
        "meroño: hola meroño: otra vez supin: ey",
        VOICES,
        "drama-3",
    )
    assert text == "<|speaker:0|>hola otra vez <|speaker:1|>ey"


def test_missing_voice_falls_back_to_single_voice_without_labels() -> None:
    text, reference_ids = prepare_dialogue(
        "meroño: hola supin: hey",
        {"meroño": "v1", "supin": "", "colombiana": ""},
        "drama-3",
    )
    assert reference_ids is None
    assert text == "hola hey"


def test_unsupported_model_falls_back_to_single_voice() -> None:
    text, reference_ids = prepare_dialogue(
        "meroño: hola supin: hey",
        VOICES,
        "s1",
    )
    assert reference_ids is None
    assert text == "hola hey"


def test_labels_only_are_removed() -> None:
    assert strip_dialogue_labels("meroño: supin: colombiana:") == ""
