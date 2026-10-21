"""Pure V1 SRS calculations. No database, file, delivery or clock authority.

Call record_exposures only behind the owning pipeline's idempotent delivery
acknowledgement. It intentionally does not grant permission to record a send.
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from copy import deepcopy
from datetime import datetime, timedelta, timezone

from .curriculum import phrase_identity

INTERVALS = (1, 3, 7, 21)


def timestamp(value: object) -> str | None:
    """Canonicalize explicit ISO clocks; never substitute the wall clock."""
    if not isinstance(value, str):
        return None
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
        # Legacy persisted timestamps are ISO; naive dates are UTC, not host TZ.
        if parsed.tzinfo is None:
            parsed = parsed.replace(tzinfo=timezone.utc)
        return parsed.astimezone(timezone.utc).isoformat(timespec="milliseconds").replace("+00:00", "Z")
    except (ValueError, OverflowError):
        return None


def _plus_days(value: str, days: int) -> str:
    result = datetime.fromisoformat(value.replace("Z", "+00:00")) + timedelta(days=days)
    return result.isoformat(timespec="milliseconds").replace("+00:00", "Z")


def extract_phrases(
    segments: Sequence[Mapping], *, aliases: Mapping[str, str] | None = None
) -> tuple[dict, ...]:
    found: dict[str, dict] = {}
    for segment in segments:
        text = segment.get("l2")
        if isinstance(text, list) and all(isinstance(unit, str) for unit in text):
            text = " ".join(text)
        if not isinstance(text, str) or not text:
            continue
        key = phrase_identity(text, aliases)
        if key not in found:
            found[key] = {"l2": key}
            if isinstance(segment.get("l1"), str):
                found[key]["l1"] = segment["l1"]
    return tuple(found.values())


def record_exposures(
    records: Mapping[str, dict],
    segments: Sequence[Mapping],
    *,
    now: str,
    lane_id: str = "",
    aliases: Mapping[str, str] | None = None,
) -> tuple[dict[str, dict], dict[str, list[str]]]:
    """Return a new snapshot and summary; same-phrase echoes count once."""
    result = deepcopy(dict(records))
    summary: dict[str, list[str]] = {"added": [], "updated": []}
    ts = timestamp(now)
    if ts is None:
        return result, summary
    for phrase in extract_phrases(segments, aliases=aliases):
        key = phrase["l2"]
        if key not in result:
            result[key] = {
                **phrase,
                "exposureCount": 1,
                "firstHeard": ts,
                "lastHeard": ts,
                "level": 1,
                "lanes": {lane_id: 1} if lane_id else {},
                "intervalIndex": 0,
                "dueAt": _plus_days(ts, 1),
            }
            summary["added"].append(key)
        else:
            record = result[key]
            record["exposureCount"] += 1
            record["lastHeard"] = ts
            if "l1" in phrase:
                record["l1"] = phrase["l1"]
            if lane_id:
                record["lanes"][lane_id] = record["lanes"].get(lane_id, 0) + 1
            if ts >= record["dueAt"]:
                record["intervalIndex"] = min(record["intervalIndex"] + 1, 3)
                record["dueAt"] = _plus_days(ts, INTERVALS[record["intervalIndex"]])
            summary["updated"].append(key)
    return result, summary


def anchor_floor(rows, records, days):
    """Cap effective due dates without changing the inherited interval scheduler."""
    result = deepcopy(records)
    for row in rows:
        record = result.get(row["ledger_identity"])
        if row.get("anchor") and record and record.get("lastHeard"):
            record["dueAt"] = min(record["dueAt"], _plus_days(record["lastHeard"], days))
    return result
