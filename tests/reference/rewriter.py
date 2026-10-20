"""Historical v1 rest-bearing parser; never shipped in better_speech."""

from collections.abc import Mapping, Sequence
from copy import deepcopy

from better_speech.curriculum import js_trim
from better_speech.rewriter import _json


def parse_plan(raw: str) -> dict | None:
    """V1 parsePlan parity only: normalize known fields, reject bad shapes."""
    try:
        parsed = _json(raw)
    except (ValueError, TypeError, AttributeError):
        return None
    if not isinstance(parsed, dict) or not isinstance(parsed.get("plan"), list) or not parsed["plan"]:
        return None
    items: list[dict] = []
    for item in parsed["plan"]:
        if not isinstance(item, dict):
            return None
        if isinstance(item.get("rest"), str):
            items.append({"rest": item["rest"]})
        elif isinstance(item.get("phraseId"), str):
            normalized = {"phraseId": item["phraseId"], "echo": item.get("echo") is True}
            if item.get("mode") in ("open", "close"):
                normalized["mode"] = item["mode"]
            items.append(normalized)
        else:
            return None
    return {"plan": items}


def assemble_from_plan(plan: Mapping, inventory: Sequence[Mapping]) -> dict | None:
    """V1 pure assembly: exact inventory strings; unknown ID drops locally.

    Only accepts parse_plan's normalized shape. Rest is legacy parity behavior,
    not permission for the E1 helper to rewrite original source.
    """
    by_id = {entry["id"]: entry for entry in inventory}
    segments: list[dict] = []
    sprinkles = 0
    for item in plan["plan"]:
        if "rest" in item:
            if js_trim(item["rest"]):
                segments.append({"rest": item["rest"]})
            continue
        entry = by_id.get(item["phraseId"])
        if entry is None:
            continue
        segment = {"l2": deepcopy(entry.get("units") or entry["l2"]), "l1": entry["l1"]}
        for field in ("literal", "l2Tiered"):
            if field in entry:
                segment[field] = deepcopy(entry[field])
        if "mode" in item:
            segment["mode"] = item["mode"]
        segments.append(segment)
        if item.get("echo"):
            segments.append(deepcopy(segment))
        sprinkles += 1
    return {"segments": segments} if sprinkles else None
