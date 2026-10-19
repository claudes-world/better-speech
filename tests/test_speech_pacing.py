import json
from pathlib import Path

import pytest

from better_speech.pacing import DEFAULT, compile_lesson, resolve_profile

COFFEE = {
    "phrase": "Cho mình một cà phê sữa đá, nha",
    "units": ["Cho mình", "một", "cà phê sữa đá", "nha"],
    "vocabulary": [{"term": "sữa"}, {"term": "đá"}],
    "structure_pauses": [{"before": "một"}],
}

REFERENCE = json.loads((Path(__file__).parent / "fixtures/enrichment/pacing-layers.json").read_text())


@pytest.mark.parametrize("case", REFERENCE["cases"], ids=lambda case: case["id"])
def test_unchanged_reference_compiler_renderings(case):
    assert compile_lesson(REFERENCE["record"], profile=case["profile"]).rendered == case["expected"]


@pytest.mark.parametrize("slot", ["start_1", "start_2", "end_1", "end_2"])
@pytest.mark.parametrize("layer", ["p0", "p1", "p2_0", "p2_1"])
def test_each_layer_resolves_independently_in_each_slot(slot, layer):
    config = {
        "default": {layer: "global"},
        "slots": {slot: {layer: "slot"}},
        "voices": {"voice": {"default": {layer: "voice"}, "slots": {slot: {layer: "voice-slot"}}}},
    }
    assert resolve_profile(config, slot=slot, voice="voice")[layer] == "voice-slot"
    config["voices"]["voice"]["slots"][slot][layer] = None
    assert resolve_profile(config, slot=slot, voice="voice")[layer] == "voice"
    config["voices"]["voice"]["default"][layer] = None
    assert resolve_profile(config, slot=slot, voice="voice")[layer] == "slot"
    config["slots"][slot][layer] = ""
    assert resolve_profile(config, slot=slot, voice="voice")[layer] == ""


def test_locked_reference_and_source_unchanged():
    result = compile_lesson(COFFEE, profile=DEFAULT)
    assert result.rendered == "Cho mình, một, cà phê sữa đá – nha"
    assert "⟨p0,p2_0⟩một" in result.pacing
    assert COFFEE["phrase"] == "Cho mình một cà phê sữa đá, nha"


def test_all_four_layers_precedence_and_empty_fallthrough():
    record = {
        "phrase": "a, b",
        "units": ["a", "b"],
        "structure_pauses": [{"before": "b"}],
        "vocabulary": [{"term": "b"}],
    }
    profile = {"p0": "0", "p1": "1", "p2_0": "2", "p2_1": "3"}
    for layer, expected in [("p2_0", "a2b"), ("p2_1", "a3b"), ("p1", "a1b"), ("p0", "a0b")]:
        assert compile_lesson(record, profile=profile).rendered == expected
        profile[layer] = ""
    assert compile_lesson(record, profile=profile).rendered == "a b"


def test_sentence_punctuation_wins_and_no_terminal_or_initial_marks():
    record = {
        "phrase": "Mắc quá! Bớt chút đi.",
        "units": ["Mắc quá", "Bớt chút đi"],
        "structure_pauses": [{"before": "Mắc"}, {"before": "Bớt"}],
    }
    assert compile_lesson(record, profile={key: "X" for key in DEFAULT}).rendered == record["phrase"]


def test_repeated_multiword_span_and_emphasis_false():
    record = {
        "phrase": "cà phê và cà phê sữa",
        "units": ["cà phê và cà phê sữa"],
        "vocabulary": [{"term": "cà phê", "occurrence": 2}, {"term": "sữa", "emphasis": False}],
    }
    assert compile_lesson(record, profile={"p2_1": " – "}).rendered == "cà phê và – cà phê sữa"
    record["structure_pauses"] = [{"before": "phê", "occurrence": 2}]
    assert compile_lesson(record, profile={"p2_0": ", "}).rendered == "cà phê và cà, phê sữa"


def test_settings_global_slot_voice_default_voice_slot_and_null_inherit():
    config = {
        "default": {"p0": "global", "p1": "global1"},
        "slots": {"end_2": {"p0": "slot", "p2_0": "slot2"}},
        "voices": {
            "v": {"default": {"p0": "voice", "p1": None}, "slots": {"end_2": {"p0": "", "p2_0": None}}}
        },
    }
    assert resolve_profile(config, slot="end_2", voice="v") == {
        "p0": "",
        "p1": "global1",
        "p2_0": "slot2",
        "p2_1": "",
    }
    assert resolve_profile({}, slot="start_1", voice="v") == DEFAULT
    with pytest.raises(ValueError):
        resolve_profile({}, slot="natural", voice="v")


@pytest.mark.parametrize(
    "patch",
    [
        {"units": ["Cho mình"]},
        {"structure_pauses": [{"before": "missing"}]},
        {"vocabulary": [{"term": "cà phê", "occurrence": 0}]},
    ],
)
def test_invalid_alignment_rejected_for_plain_fallback(patch):
    with pytest.raises(ValueError):
        compile_lesson({**COFFEE, **patch}, profile=DEFAULT)
