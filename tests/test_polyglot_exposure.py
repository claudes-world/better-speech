import json
import unicodedata
from pathlib import Path

import pytest

from better_speech.exposure import INTERVALS, extract_phrases, record_exposures
from tests.reference.exposure import (
    import_exposures,
    list_due,
    list_phrases,
    select_phrases,
    set_level,
    stats,
)

GOLDEN = json.loads((Path(__file__).parent / "fixtures/enrichment/srs.json").read_text())
T1, T2, T3 = [f"2026-08-11T{hour}:00:00.000Z" for hour in ("10", "11", "12")]


@pytest.mark.parametrize("case", GOLDEN["cases"], ids=lambda c: c["name"])
def test_original_srs_golden(case):
    records = {}
    for ts in case["times"]:
        records, _ = record_exposures(records, [GOLDEN["phrase"]], now=ts)
    rec = records["Chào bạn"]
    assert (rec["exposureCount"], rec["intervalIndex"], rec["dueAt"]) == (
        case["count"],
        case["index"],
        case["due"],
    )
    assert rec["level"] == 1


def test_original_repetition_identity_gloss_and_copy():
    nfd = unicodedata.normalize("NFD", "Chào bạn")
    records, summary = record_exposures(
        {}, [{"l2": "Chào bạn", "l1": "Hello friend"}, {"l2": nfd}], now=T1, lane_id="coach"
    )
    assert summary == {"added": ["Chào bạn"], "updated": []}
    updated, summary = record_exposures(records, [{"l2": nfd, "l1": "Hi friend"}], now=T2, lane_id="dev")
    assert records["Chào bạn"]["exposureCount"] == 1
    assert updated["Chào bạn"]["exposureCount"] == 2
    assert updated["Chào bạn"]["l1"] == "Hi friend"
    assert updated["Chào bạn"]["lanes"] == {"coach": 1, "dev": 1}
    assert summary == {"added": [], "updated": ["Chào bạn"]}
    assert extract_phrases([{"l2": ["Chúc bạn", "vui vẻ"], "l1": "Wishing you joy"}]) == (
        {"l2": "Chúc bạn vui vẻ", "l1": "Wishing you joy"},
    )
    different, _ = record_exposures(updated, [{"l2": "chào bạn"}], now=T2)
    assert len(different) == 2


def test_original_invalid_clock_and_interval_cap():
    records, _ = record_exposures({}, [GOLDEN["phrase"]], now=T1)
    unchanged, summary = record_exposures(records, [{"l2": "new"}], now="invalid")
    assert unchanged == records
    assert summary == {"added": [], "updated": []}
    assert list_due(records, now="invalid") == []
    assert select_phrases(records, now="invalid") == {"due": [], "newSlots": 0}
    for _ in range(len(INTERVALS) + 2):
        records, _ = record_exposures(records, [GOLDEN["phrase"]], now=records["Chào bạn"]["dueAt"])
    assert records["Chào bạn"]["intervalIndex"] == 3
    assert records["Chào bạn"]["level"] == 1


def test_original_due_lane_order_and_slots():
    records = {}
    for phrase, ts, lane in (
        ("Cảm ơn", T1, "coach"),
        ("Xong rồi", T2, "dev"),
        ("Chào bạn", "2026-08-16T10:00:00.000Z", "coach"),
    ):
        records, _ = record_exposures(records, [{"l2": phrase}], now=ts, lane_id=lane)
    now = "2026-08-13T10:00:00.000Z"
    assert [p["l2"] for p in list_due(records, now=now)] == ["Cảm ơn", "Xong rồi"]
    assert [p["l2"] for p in list_due(records, now=now, lane_id="coach")] == ["Cảm ơn"]
    assert select_phrases(records, now=now, max_new=3, max_total=4)["newSlots"] == 2
    assert select_phrases(records, now=now, max_total=1)["newSlots"] == 0
    assert select_phrases({}, now=now, max_total=10)["newSlots"] == 2


def test_original_import_repair_and_nfc_collision():
    nfc, nfd = "ba mảnh", unicodedata.normalize("NFD", "ba mảnh")
    raw = {
        "version": 2,
        "phrases": {
            "wrong-key": {
                "l2": nfc,
                "exposureCount": 5,
                "firstHeard": T2,
                "lastHeard": T3,
                "level": 1,
                "lanes": {"coach": 5},
                "intervalIndex": 2,
                "dueAt": T3,
            },
            nfd: {
                "l2": nfd,
                "exposureCount": 9,
                "firstHeard": T1,
                "lastHeard": T2,
                "level": 2,
                "lanes": {"coach": 4, "dev": 5},
                "intervalIndex": 1,
                "dueAt": T2,
            },
            "bad": None,
        },
    }
    records, diagnostics = import_exposures(raw)
    assert list(records) == [nfc]
    assert records[nfc] == {
        "l2": nfc,
        "exposureCount": 14,
        "firstHeard": T1,
        "lastHeard": T3,
        "level": 2,
        "lanes": {"coach": 9, "dev": 5},
        "intervalIndex": 1,
        "dueAt": T2,
    }
    assert any("collision" in item for item in diagnostics)
    assert any("skipped" in item for item in diagnostics)
    raw["phrases"] = {
        "x": {"l2": "cảm ơn", "firstHeard": T1, "exposureCount": 4, "lanes": {"coach": 3, "bad": "no"}}
    }
    repaired, _ = import_exposures(raw)
    assert repaired["cảm ơn"]["lastHeard"] == T1
    assert repaired["cảm ơn"]["dueAt"] == "2026-08-12T10:00:00.000Z"
    assert repaired["cảm ơn"]["lanes"] == {"coach": 3}
    assert import_exposures([]) == ({}, ("invalid ledger shape",))


@pytest.mark.parametrize("index,expected", [(999, 3), (-4, 0), (1.5, 0), (True, 0)])
def test_original_import_interval_clamp(index, expected):
    records, _ = import_exposures({"phrases": {"x": {"l2": "x", "lastHeard": T1, "intervalIndex": index}}})
    assert records["x"]["intervalIndex"] == expected


def test_original_manual_levels_listing_and_stats():
    records, _ = record_exposures({}, [{"l2": "Chào bạn"}], now=T1, lane_id="coach")
    records, _ = record_exposures(records, [{"l2": "Chào bạn"}, {"l2": "Xong rồi"}], now=T2, lane_id="dev")
    records, _ = record_exposures(records, [{"l2": "Cảm ơn"}], now=T3, lane_id="coach")
    promoted, changed = set_level(records, "Cảm ơn", 2)
    assert changed and records["Cảm ơn"]["level"] == 1
    assert set_level(records, "missing", 2)[1] is False
    assert [r["l2"] for r in list_phrases(promoted, sort_by="lastHeard")] == [
        "Cảm ơn",
        "Chào bạn",
        "Xong rồi",
    ]
    assert [r["l2"] for r in list_phrases(promoted, sort_by="firstHeard")] == [
        "Chào bạn",
        "Xong rồi",
        "Cảm ơn",
    ]
    assert len(list_phrases(promoted, lane_id="coach")) == 2
    assert len(list_phrases(promoted, level=2)) == 1
    assert stats(promoted) == {
        "totalPhrases": 3,
        "totalExposures": 4,
        "byLevel": {1: 2, 2: 1},
        "byLane": {"coach": 2, "dev": 2},
    }


def test_source_derived_integral_json_number_index():
    case = GOLDEN["source_derived_regressions"]["integral_index"]
    assert type(case["input"]) is float  # do not accidentally weaken the fixture
    records, _ = import_exposures(
        {"phrases": {"x": {"l2": "x", "lastHeard": T1, "intervalIndex": case["input"]}}}
    )
    assert records["x"]["intervalIndex"] == case["expected_index"]
    assert records["x"]["dueAt"] == case["expected_due"]


@pytest.mark.parametrize("case", GOLDEN["source_derived_regressions"]["manual_levels"])
def test_source_derived_manual_level_numeric_and_invalid_values(case):
    records, _ = record_exposures({}, [{"l2": "x"}], now=T1)
    result, changed = set_level(records, "x", case["input"])
    assert changed is case["changed"]
    assert result["x"]["level"] == case["expected"]
    assert type(result["x"]["level"]) is int
    assert records["x"]["level"] == 1
    if not changed:
        assert result == records and result is not records
    assert set_level(records, "missing", case["input"]) == (records, False)


@pytest.mark.parametrize("invalid", [float("nan"), float("inf"), [], {}, -1, 1.5])
def test_manual_invalid_level_never_raises(invalid):
    records, _ = record_exposures({}, [{"l2": "x"}], now=T1)
    assert set_level(records, "x", invalid) == (records, False)


def test_source_derived_stats_sparse_levels():
    assert stats({}) == GOLDEN["source_derived_regressions"]["empty_stats"]
    records, _ = record_exposures({}, [{"l2": "x"}], now=T1)
    assert stats(records)["byLevel"] == {1: 1}
    promoted, _ = set_level(records, "x", 2.0)
    assert stats(promoted)["byLevel"] == {2: 1}
