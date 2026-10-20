"""Historical import/admin/selection helpers; not part of the runtime ledger."""

import math
from collections.abc import Mapping
from copy import deepcopy

from better_speech.curriculum import canonical_phrase
from better_speech.exposure import INTERVALS, _plus_days, timestamp

EPOCH = "1970-01-01T00:00:00.000Z"


def _number(value: object) -> bool:
    try:
        return type(value) in (int, float) and math.isfinite(value)
    except OverflowError:
        return False


def import_exposures(raw: object) -> tuple[dict[str, dict], tuple[str, ...]]:
    """Repair individual V1/V2 records, retaining conservative collision merge.

    Return diagnostics, not a filesystem corruption backup or a hidden reset.
    The caller retains original import bytes in its existing custody mechanism.
    """
    if not isinstance(raw, dict) or not isinstance(raw.get("phrases"), dict):
        return {}, ("invalid ledger shape",)
    records: dict[str, dict] = {}
    diagnostics: list[str] = []
    for ordinal, value in enumerate(raw["phrases"].values()):
        if not isinstance(value, dict) or not isinstance(value.get("l2"), str):
            diagnostics.append(f"record {ordinal}: invalid entry skipped")
            continue
        key = canonical_phrase(value["l2"])
        idx = value.get("intervalIndex")
        # JSON has one numeric type: JS Number.isInteger(2.0) is true.
        idx = min(int(idx), 3) if _number(idx) and idx >= 0 and idx == int(idx) else 0
        last = timestamp(value.get("lastHeard")) or timestamp(value.get("firstHeard")) or EPOCH
        first = timestamp(value.get("firstHeard")) or last
        try:
            due = timestamp(value.get("dueAt")) or _plus_days(last, INTERVALS[idx])
        except OverflowError:
            diagnostics.append(f"record {ordinal}: timestamp overflow skipped")
            continue
        lanes = value.get("lanes", {})
        lanes = {k: n for k, n in lanes.items() if _number(n)} if isinstance(lanes, dict) else {}
        record = {
            "l2": key,
            "exposureCount": value.get("exposureCount") if _number(value.get("exposureCount")) else 1,
            "firstHeard": first,
            "lastHeard": last,
            "level": 2 if type(value.get("level")) in (int, float) and value["level"] == 2 else 1,
            "lanes": lanes,
            "intervalIndex": idx,
            "dueAt": due,
        }
        if isinstance(value.get("l1"), str):
            record["l1"] = value["l1"]
        if value != record:
            diagnostics.append(f"record {ordinal}: normalized or repaired")
        if key in records:
            old = records[key]
            record["exposureCount"] += old["exposureCount"]
            for lane, count in old["lanes"].items():
                record["lanes"][lane] = record["lanes"].get(lane, 0) + count
            record["firstHeard"] = min(first, old["firstHeard"])
            record["lastHeard"] = max(last, old["lastHeard"])
            record["dueAt"] = min(due, old["dueAt"])
            record["intervalIndex"] = min(idx, old["intervalIndex"])
            record["level"] = max(record["level"], old["level"])
            if "l1" not in record and "l1" in old:
                record["l1"] = old["l1"]
            diagnostics.append(f"record {ordinal}: NFC collision merged")
        records[key] = record
    return records, tuple(diagnostics)


def list_phrases(
    records: Mapping[str, dict], *, lane_id: str = "", level: int | None = None, sort_by: str = "count"
) -> list[dict]:
    rows = [
        deepcopy(r)
        for r in records.values()
        if (not lane_id or r["lanes"].get(lane_id, 0) > 0) and (level is None or r["level"] == level)
    ]
    key = {"count": "exposureCount", "lastHeard": "lastHeard", "firstHeard": "firstHeard"}[sort_by]
    return sorted(rows, key=lambda r: r[key], reverse=sort_by != "firstHeard")


def list_due(records: Mapping[str, dict], *, now: str, lane_id: str = "") -> list[dict]:
    ts = timestamp(now)
    if ts is None:
        return []
    return sorted(
        (
            deepcopy(r)
            for r in records.values()
            if r["dueAt"] <= ts and (not lane_id or r["lanes"].get(lane_id, 0) > 0)
        ),
        key=lambda r: r["dueAt"],
    )


def select_phrases(
    records: Mapping[str, dict], *, now: str, lane_id: str = "", max_new: int = 2, max_total: int = 4
) -> dict:
    if timestamp(now) is None:
        return {"due": [], "newSlots": 0}
    due = list_due(records, now=now, lane_id=lane_id)[: max(0, max_total)]
    return {"due": due, "newSlots": max(0, min(max_new, max_total - len(due)))}


def set_level(records: Mapping[str, dict], phrase: str, level: object) -> tuple[dict[str, dict], bool]:
    result = deepcopy(dict(records))
    if not _number(level) or level not in (1, 2):
        return result, False
    key = canonical_phrase(phrase)
    if key not in result:
        return result, False
    result[key]["level"] = int(level)
    return result, True


def stats(records: Mapping[str, dict]) -> dict:
    result = {"totalPhrases": len(records), "totalExposures": 0, "byLevel": {}, "byLane": {}}
    for row in records.values():
        result["totalExposures"] += row["exposureCount"]
        result["byLevel"][row["level"]] = result["byLevel"].get(row["level"], 0) + 1
        for lane, count in row["lanes"].items():
            result["byLane"][lane] = result["byLane"].get(lane, 0) + count
    return result
