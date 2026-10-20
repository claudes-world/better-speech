import json
from pathlib import Path

import pytest

from better_speech.exposure import record_exposures
from tests.reference.selection import Placement, Slot, cap_placements, select_focus_ids

GOLDEN = json.loads((Path(__file__).parent / "fixtures/enrichment/selection.json").read_text())


@pytest.mark.parametrize(
    "intensity,expected", [(1, "expected_light"), (2, "expected_dense"), (3, "expected_dense")]
)
def test_original_hook_selection_golden(intensity, expected):
    records = {}
    for item in GOLDEN["exposures"]:
        records, _ = record_exposures(records, [item], now=item["timestamp"], lane_id=item["lane"])
    assert select_focus_ids(
        GOLDEN["curriculum"], records, now=GOLDEN["now"], lane_id="lane-a", intensity=intensity
    ) == tuple(GOLDEN[expected])


def test_original_other_lane_not_due_and_invalid_clock_no_new():
    records, _ = record_exposures({}, [{"l2": "Mệt quá!"}], now="2026-08-20T00:00:00.000Z", lane_id="lane-b")
    assert select_focus_ids(GOLDEN["curriculum"], records, now=GOLDEN["now"], lane_id="lane-a") == tuple(
        GOLDEN["expected_other_lane"]
    )
    assert select_focus_ids(GOLDEN["curriculum"], records, now="invalid", lane_id="lane-a") == ()


def test_original_missing_due_inventory_consumes_capacity():
    records, _ = record_exposures(
        {}, [{"l2": "unknown one"}, {"l2": "unknown two"}], now="2026-08-20T00:00:00.000Z", lane_id="lane-a"
    )
    assert select_focus_ids(GOLDEN["curriculum"], records, now=GOLDEN["now"], lane_id="lane-a") == ()


def test_owner_explicit_alias_suppresses_new_and_resolves_due():
    records, _ = record_exposures({}, [{"l2": "Đi thôi"}], now="2026-08-20T00:00:00.000Z", lane_id="lane-a")
    assert select_focus_ids(
        GOLDEN["curriculum"], records, now=GOLDEN["now"], lane_id="lane-a", aliases={"Đi thôi!": "Đi thôi"}
    ) == ("di-thoi", "ngon-qua")


def test_owner_caps_semantics_boundaries_and_duplicates():
    inventory = [{"id": f"c{i}", "l2": f"connector {i}", "lesson_type": "connector"} for i in range(4)]
    inventory += [
        {"id": f"s{i}", "l2": f"sentence {i}", "lesson_type": "learning_sentence"} for i in range(2)
    ]
    slots = [Slot(f"p{i}", "opener") for i in range(4)] + [
        Slot("tail", "closing_lesson"),
        Slot("tail2", "closing_lesson"),
        Slot("mid", "opener", False),
    ]
    eligible = {slot.id: [e["id"] for e in inventory] for slot in slots}
    proposals = [Placement("unknown", "p0"), Placement("s0", "p0"), Placement("c0", "mid")]
    proposals += [Placement(f"c{i}", f"p{i}") for i in range(4)]
    proposals += [Placement("c0", "p3"), Placement("s0", "tail"), Placement("s1", "tail2")]
    actual = cap_placements(
        proposals, inventory=inventory, slots=slots, eligible_ids=eligible, now=GOLDEN["now"]
    )
    assert actual == tuple([Placement(f"c{i}", f"p{i}") for i in range(3)] + [Placement("s0", "tail")])
    assert (
        cap_placements(proposals, inventory=inventory, slots=slots, eligible_ids={}, now=GOLDEN["now"]) == ()
    )


@pytest.mark.parametrize(
    "now,allowed",
    [("2026-09-24T03:59:59.999Z", False), ("2026-09-24T04:00:00.000Z", True), ("invalid", False)],
)
def test_owner_coffee_deferred_until_september24_eastern(now, allowed):
    proposed = Placement("cho-minh-ca-phe", "tail")
    result = cap_placements(
        [proposed],
        inventory=[{"id": proposed.phrase_id, "l2": "curated source", "lesson_type": "learning_sentence"}],
        slots=[Slot("tail", "closing_lesson")],
        eligible_ids={"tail": [proposed.phrase_id]},
        now=now,
    )
    assert result == ((proposed,) if allowed else ())


def test_owner_unclassified_inventory_does_not_guess_lesson_type():
    assert (
        cap_placements(
            [Placement("di-thoi", "p0")],
            inventory=GOLDEN["curriculum"],
            slots=[Slot("p0", "opener")],
            eligible_ids={"p0": ["di-thoi"]},
            now=GOLDEN["now"],
        )
        == ()
    )


@pytest.mark.parametrize("intensity", [1, 2, 3])
def test_source_derived_six_due_candidates_distinguish_all_intensity_caps(intensity):
    case = GOLDEN["source_derived_regressions"]
    records, _ = record_exposures({}, case["curriculum"], now="2026-08-20T00:00:00.000Z", lane_id="lane-a")
    picked = select_focus_ids(
        case["curriculum"], records, now=GOLDEN["now"], lane_id="lane-a", intensity=intensity
    )
    assert picked == tuple(case["expected_by_intensity"][str(intensity)])
    assert len(picked) == intensity * 2
