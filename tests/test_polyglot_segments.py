import json
from pathlib import Path

import pytest

from better_speech.curriculum import canonical_phrase, strip_tiers
from tests.reference.curriculum import bundled_curriculum
from tests.reference.segments import (
    extract_phrases,
    l2_text,
    l2_units,
    validate_segments,
)

GOLDEN = json.loads((Path(__file__).parent / "fixtures/enrichment/segments.json").read_text())


@pytest.mark.parametrize(
    "case", GOLDEN["validation"] + GOLDEN["source_derived_regressions"], ids=lambda case: case["name"]
)
def test_validation_goldens(case):
    result = validate_segments(case["input"])
    if "output" in case:
        assert result == {"ok": True, "value": case["output"]}
    else:
        assert not result["ok"] and len(result["errors"]) >= case.get("minimum_errors", 1)
        assert case.get("error_contains", "") in " ".join(result["errors"])


def test_good_raw_and_parsed_document_and_helper_goldens():
    good = GOLDEN["good"]
    for value in (good, json.dumps(good)):
        assert validate_segments(value) == {"ok": True, "value": good}
    assert extract_phrases(good) == GOLDEN["good_phrases"]
    for case in GOLDEN["helpers"]["units"]:
        assert l2_units(case["input"]) == case["output"]
    for case in GOLDEN["helpers"]["text"]:
        assert l2_text(case["input"], case["joiner"]) == case["output"]
    dedupe = GOLDEN["helpers"]["dedupe"]
    assert extract_phrases(dedupe) == dedupe["output"]
    extension = GOLDEN["extension"]
    assert extract_phrases(extension["input"]) == extension["output"]
    for case in GOLDEN["validation"]:
        if "output" not in case:
            continue
        segment = validate_segments(case["input"])["value"]["segments"][0]
        tiered = segment.get("l2Tiered")
        assert l2_units(segment, tiered=True) == (
            [] if tiered is None else tiered if isinstance(tiered, list) else [tiered]
        )


def test_caps_hostile_inputs_and_utf16_field_boundary():
    assert not validate_segments({"segments": [{"rest": "x"}] * GOLDEN["bounds"]["segments"]})["ok"]
    assert not validate_segments({"segments": [{"l2": ["a"] * GOLDEN["bounds"]["units"], "l1": "x"}]})["ok"]
    for value in (None, object(), lambda: None, [1, 2]):
        assert validate_segments(value)["ok"] is False
    assert not validate_segments({"segments": [{"rest": "x", "mode": object()}]})["ok"]
    assert validate_segments({"segments": [{"rest": "🙂" * 2000}]})["ok"]
    assert not validate_segments({"segments": [{"rest": "🙂" * 2001}]})["ok"]


def test_bundled_curriculum_source_invariants():
    asset = bundled_curriculum()
    assert asset["version"] == 2
    annotated = 0
    for entry in asset["focusPhrases"]:
        for field in ("l2", "l1", "literal"):
            if isinstance(entry.get(field), str):
                assert not any(marker in entry[field] for marker in "⟨⟩")
        for unit in entry.get("units", []):
            assert not any(marker in unit for marker in "⟨⟩")
        if "l2Tiered" not in entry:
            continue
        annotated += 1
        plain = entry.get("units") or entry["l2"]
        tiered = entry["l2Tiered"]
        assert isinstance(plain, list) == isinstance(tiered, list)
        plain = plain if isinstance(plain, list) else [plain]
        tiered = tiered if isinstance(tiered, list) else [tiered]
        assert len(tiered) == len(plain)
        assert [canonical_phrase(strip_tiers(t)) for t in tiered] == [canonical_phrase(p) for p in plain]
    assert annotated > 30
