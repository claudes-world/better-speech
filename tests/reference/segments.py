"""Legacy lesson-segment interchange, not authority for rewriting source.

V1 5b61b0f polyglot-segments.ts, with its optional annotation degradation.
The E1 planner assembles immutable source spans itself; accepting a legacy
``rest`` field here never grants a model permission to replace those spans.
Validation diagnostics are returned, not logged on the delivery path.
"""

from __future__ import annotations

import json
import re
from collections.abc import Mapping

from better_speech.curriculum import canonical_phrase, js_collapse_whitespace, js_trim, strip_tiers

MAX_SEGMENTS = 40
MAX_L2_UNITS = 12
MAX_FIELD_CHARS = 4000


def _reject_json_constant(value):
    raise ValueError(f"not a JSON constant: {value}")


def _js_length(value: str) -> int:
    # Preserve the old UTF-16 limit, without ever using it to cut source.
    return len(value.encode("utf-16-le", errors="surrogatepass")) // 2


def _plain(value: str) -> str:
    if "⟨" not in value and "⟩" not in value:
        return value
    # Faithful V1 display guard. strip_tiers separately handles half-markers
    # while checking annotation identity; do not silently change the golden.
    return js_collapse_whitespace(re.sub("[⟨⟩]", "", re.sub(r"⟨[0-9]⟩", " ", value)))


def _text(value, path, errors):
    if not isinstance(value, str):
        kind = (
            "array"
            if isinstance(value, list)
            else "object"
            if value is None or isinstance(value, dict)
            else "boolean"
            if isinstance(value, bool)
            else "number"
            if isinstance(value, (int, float))
            else "undefined"
        )
        errors.append(f"{path}: expected string, got {kind}")
        return None
    length = _js_length(value)
    if length > MAX_FIELD_CHARS:
        errors.append(f"{path}: {length} chars exceeds MAX_FIELD_CHARS ({MAX_FIELD_CHARS})")
        return None
    return value


def _l2(value, path, errors):
    if isinstance(value, str):
        if not js_trim(value):
            errors.append(f"{path}: empty")
            return None
        return _text(value, path, errors)
    if isinstance(value, list):
        if not value:
            errors.append(f"{path}: empty array")
            return None
        if len(value) > MAX_L2_UNITS:
            errors.append(f"{path}: {len(value)} units exceeds MAX_L2_UNITS ({MAX_L2_UNITS})")
            return None
        units = []
        for index, unit in enumerate(value):
            text = _text(unit, f"{path}[{index}]", errors)
            if text is not None:
                if not js_trim(text):
                    errors.append(f"{path}[{index}]: empty")
                else:
                    units.append(text)
        return units if len(units) == len(value) else None
    errors.append(f"{path}: expected string or string[]")
    return None


def _tiered(value, l2):
    if l2 is None or isinstance(value, list) != isinstance(l2, list):
        return None
    units = value if isinstance(value, list) else [value]
    plain = l2 if isinstance(l2, list) else [l2]
    if len(units) != len(plain):
        return None
    for unit, expected in zip(units, plain):
        if (
            not isinstance(unit, str)
            or _js_length(unit) > MAX_FIELD_CHARS
            or canonical_phrase(strip_tiers(unit)) != canonical_phrase(expected)
        ):
            return None
    return list(units) if isinstance(value, list) else units[0]


def validate_segments(value: object) -> dict:
    """Accept parsed JSON or JSON text; optional tier errors drop only tiers."""
    if isinstance(value, str):
        try:
            value = json.loads(value, parse_constant=_reject_json_constant)
        except (ValueError, RecursionError):
            return {"ok": False, "errors": ["not valid JSON"]}
    if not isinstance(value, dict):
        return {"ok": False, "errors": ["root: expected an object"]}
    raw_segments = value.get("segments")
    if not isinstance(raw_segments, list):
        return {"ok": False, "errors": ["segments: expected an array"]}
    if not raw_segments:
        return {"ok": False, "errors": ["segments: empty"]}
    if len(raw_segments) > MAX_SEGMENTS:
        return {
            "ok": False,
            "errors": [f"segments: {len(raw_segments)} exceeds MAX_SEGMENTS ({MAX_SEGMENTS})"],
        }
    errors, output = [], []
    for index, raw in enumerate(raw_segments):
        path = f"segments[{index}]"
        if not isinstance(raw, dict):
            errors.append(f"{path}: expected an object")
            continue
        segment = {}
        if "mode" in raw:
            if raw["mode"] not in ("open", "close"):
                try:
                    description = json.dumps(raw["mode"], ensure_ascii=False)
                except (ValueError, TypeError, RecursionError):
                    description = "[non-JSON value]"
                errors.append(f'{path}.mode: expected "open" or "close", got {description}')
            else:
                segment["mode"] = raw["mode"]
        if "l2" in raw:
            l2 = _l2(raw["l2"], f"{path}.l2", errors)
            if l2 is not None:
                stripped = [_plain(unit) for unit in l2] if isinstance(l2, list) else _plain(l2)
                if any(
                    not js_trim(unit) for unit in (stripped if isinstance(stripped, list) else [stripped])
                ):
                    errors.append(f"{path}.l2: empty after stripping pause sentinels")
                else:
                    segment["l2"] = stripped
        for field in ("l1", "rest", "literal"):
            if field in raw:
                text = _text(raw[field], f"{path}.{field}", errors)
                if text is not None:
                    segment[field] = _plain(text)
        if "l2Tiered" in raw:
            tiered = _tiered(raw["l2Tiered"], segment.get("l2"))
            if tiered is not None:
                segment["l2Tiered"] = tiered
        if "l2" not in segment and not segment.get("l1") and not segment.get("rest"):
            errors.append(f"{path}: no speakable content (needs at least one of l2, l1, rest)")
        if "l2" in segment and not segment.get("l1"):
            errors.append(f"{path}: l2 present without an l1 gloss")
        if segment.get("literal") and "l2" not in segment:
            errors.append(f"{path}: literal present without l2")
        output.append(segment)
    return {"ok": False, "errors": errors} if errors else {"ok": True, "value": {"segments": output}}


def l2_units(segment: Mapping, *, tiered: bool = False) -> list[str]:
    value = segment.get("l2Tiered" if tiered else "l2")
    return [] if value is None else list(value) if isinstance(value, list) else [value]


def l2_text(segment: Mapping, joiner: str = " ") -> str:
    return joiner.join(l2_units(segment))


def extract_phrases(document: Mapping) -> list[dict]:
    seen, result = set(), []
    for segment in document["segments"]:
        text = l2_text(segment)
        key = canonical_phrase(text)
        if not key or key in seen:
            continue
        seen.add(key)
        phrase = {"l2": text}
        if "l1" in segment:
            phrase["l1"] = segment["l1"]
        result.append(phrase)
    return result
