import json
from pathlib import Path

import pytest

from better_speech.exposure import extract_phrases
from better_speech.rewriter import parse_placements
from tests.reference.rewriter import assemble_from_plan, parse_plan
from tests.reference.selection import Placement

GOLDEN = json.loads((Path(__file__).parent / "fixtures/enrichment/rewriter.json").read_text())


@pytest.mark.parametrize("fence", [False, True])
def test_original_assembly_golden(fence):
    raw = json.dumps(GOLDEN["plan"])
    if fence:
        raw = "```json\n" + raw + "\n```"
    parsed = parse_plan(raw)
    assert parsed is not None
    assembled = assemble_from_plan(parsed, GOLDEN["inventory"])
    assert assembled == GOLDEN["expected"]
    assert len(extract_phrases(assembled["segments"])) == 2
    assembled["segments"][0]["l2"].append("changed")
    assert assembled["segments"][1]["l2"] == ["ba", "mảnh"]
    assert GOLDEN["inventory"][0]["units"] == ["ba", "mảnh"]


@pytest.mark.parametrize("bad", GOLDEN["invalid"])
def test_original_invalid_plan_golden(bad):
    assert parse_plan(json.dumps(bad)) is None


def test_original_unknown_id_drop_and_model_text_cannot_override_inventory():
    raw = json.dumps(
        {
            "plan": [
                {"phraseId": "unknown"},
                {"rest": "Ok."},
                {"phraseId": "cung-luc", "l2": "cùng lùc", "l1": "evil"},
            ]
        }
    )
    actual = assemble_from_plan(parse_plan(raw), GOLDEN["inventory"])
    assert actual == {
        "segments": [{"rest": "Ok."}, {"l2": "cùng lúc", "l1": "same time", "literal": "same... moment"}]
    }
    assert assemble_from_plan(parse_plan('{"plan":[{"rest":"changed"}]}'), GOLDEN["inventory"]) is None


def test_original_blank_rest_echo_boolean_and_mode_rules():
    plan = parse_plan(
        json.dumps({"plan": [{"rest": "   "}, {"phraseId": "cung-luc", "echo": "true", "mode": "bad"}]})
    )
    assert assemble_from_plan(plan, GOLDEN["inventory"]) == {
        "segments": [{"l2": "cùng lúc", "l1": "same time", "literal": "same... moment"}]
    }


def test_owner_a2_accepts_only_ids_slots_and_valid_empty_choice():
    assert parse_placements('{"placements": []}') == ()
    raw = '```json\n{"placements":[{"phraseId":"cung-luc","slotId":"closing"}]}\n```'
    assert parse_placements(raw) == (Placement("cung-luc", "closing"),)
    # Unknown IDs are proposed, not synthesized; the inventory cap layer drops them.
    assert parse_placements('{"placements":[{"phraseId":"unknown","slotId":"x"}]}') == (
        Placement("unknown", "x"),
    )


@pytest.mark.parametrize(
    "row",
    [
        {"phraseId": "x", "slotId": "tail", "rest": "invented prose"},
        {"phraseId": "x", "slotId": "tail", "l2": "invented target language"},
        {"phraseId": "x", "slotId": "tail", "offset": 99},
        {"phraseId": "x", "slotId": "tail", "echo": True},
        {"phraseId": "", "slotId": "tail"},
        {"phraseId": "x", "slotId": 1},
        None,
    ],
)
def test_owner_a2_rejects_prose_offsets_echo_and_invalid_ids(row):
    assert parse_placements(json.dumps({"placements": [row]})) is None


def test_owner_a2_rejects_legacy_plan_and_unbounded_output():
    assert parse_placements(json.dumps(GOLDEN["plan"])) is None
    assert parse_placements(json.dumps({"placements": [{"phraseId": "x", "slotId": "tail"}] * 41})) is None
    assert parse_placements(" " * 32769) is None
    assert parse_placements('{"placements": [], "source": "replacement"}') is None


@pytest.mark.parametrize("constant", ["NaN", "Infinity", "-Infinity"])
def test_original_json_parse_rejects_non_json_constants_even_in_ignored_fields(constant):
    assert parse_plan('{"plan":[{"rest":"source"}],"ignored":' + constant + "}") is None


@pytest.mark.parametrize("space,accepted", [("\ufeff", True), ("\u0085", False), ("\u001c", False)])
def test_original_fence_whitespace_matches_ecmascript(space, accepted):
    raw = space + "```json" + space + '{"plan":[{"rest":"source"}]}' + space + "```" + space
    assert (parse_plan(raw) is not None) is accepted


@pytest.mark.parametrize("rest,retained", [("\ufeff", False), ("\u0085", True), ("\u001c", True)])
def test_original_rest_whitespace_matches_ecmascript(rest, retained):
    plan = parse_plan(json.dumps({"plan": [{"rest": rest}, {"phraseId": "cung-luc"}]}))
    segments = assemble_from_plan(plan, GOLDEN["inventory"])["segments"]
    assert any("rest" in segment for segment in segments) is retained


def test_unfenced_bom_is_not_silently_removed_before_json_parse():
    assert parse_plan('\ufeff{"plan":[{"rest":"source"}]}') is None
