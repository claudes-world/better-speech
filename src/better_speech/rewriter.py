"""Strict selection-only response parser. No model-authored prose is accepted."""

from __future__ import annotations

import json
import re
from dataclasses import dataclass

from .curriculum import js_trim


@dataclass(frozen=True)
class Placement:
    phrase_id: str
    slot_id: str


def _reject_constant(value: str) -> None:
    raise ValueError(f"invalid JSON constant: {value}")


def _json(raw: str) -> object:
    fenced = re.fullmatch(r"```(?:json)?([\s\S]*?)```", js_trim(raw))
    return json.loads(js_trim(fenced.group(1)) if fenced else raw, parse_constant=_reject_constant)


def parse_placements(raw: str) -> tuple[Placement, ...] | None:
    """A2 owner delta: model returns only bounded inventory IDs and slot IDs.

    An empty list adds no contextual placements. None is invalid output; the
    compiler preserves its reserved closing lesson. The selected adapter owns
    transport timeouts. This parser never invokes adapters or a fallback chain.
    The compiler rejects unknown IDs against the offered inventory and slots.
    """
    try:
        if not isinstance(raw, str) or len(raw) > 32768:
            return None
        parsed = _json(raw)
    except (ValueError, TypeError, AttributeError):
        return None
    if not isinstance(parsed, dict) or set(parsed) != {"placements"}:
        return None
    rows = parsed["placements"]
    if not isinstance(rows, list) or len(rows) > 40:
        return None
    result: list[Placement] = []
    for row in rows:
        if not isinstance(row, dict) or set(row) != {"phraseId", "slotId"}:
            return None
        if any(
            not isinstance(row[key], str) or not js_trim(row[key]) or len(row[key]) > 256
            for key in ("phraseId", "slotId")
        ):
            return None
        result.append(Placement(row["phraseId"], row["slotId"]))
    return tuple(result)
