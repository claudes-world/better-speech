"""Current curriculum and pure validation; historical imports live in tests/reference."""

from __future__ import annotations

import json
import re
import unicodedata
from collections.abc import Mapping, Sequence
from copy import deepcopy
from importlib.resources import files

_JS_WHITESPACE = "\u0009\u000a\u000b\u000c\u000d\u0020\u00a0\u1680\u2000\u2001\u2002\u2003\u2004\u2005\u2006\u2007\u2008\u2009\u200a\u2028\u2029\u202f\u205f\u3000\ufeff"
_JS_WHITESPACE_RUN = re.compile("[" + _JS_WHITESPACE + "]+")


def js_trim(text: str) -> str:
    """ECMAScript trim, including BOM but not Python's U+0085/U+001C."""
    return text.strip(_JS_WHITESPACE)


def js_collapse_whitespace(text: str) -> str:
    return js_trim(_JS_WHITESPACE_RUN.sub(" ", text))


def canonical_phrase(text: str) -> str:
    """Legacy ledger identity: NFC, case and punctuation preserved."""
    return unicodedata.normalize("NFC", text)


def strip_tiers(text: str) -> str:
    text = re.sub(r"⟨[0-9]⟩|⟨[0-9]|[0-9]⟩", " ", text)
    return js_collapse_whitespace(re.sub("[⟨⟩]", "", text))


def validate_inventory(entries: Sequence[Mapping]) -> None:
    ids: set[str] = set()
    for entry in entries:
        if not isinstance(entry, Mapping):
            raise ValueError("inventory entry must be an object")
        for name in ("id", "l2", "l1"):
            if not isinstance(entry.get(name), str) or not js_trim(entry[name]):
                raise ValueError(f"inventory {name} missing")
        if entry["id"] in ids:
            raise ValueError("duplicate inventory id")
        ids.add(entry["id"])
        units = entry.get("units")
        if units is not None and (
            not isinstance(units, list)
            or not units
            or any(not isinstance(u, str) or not js_trim(u) for u in units)
        ):
            raise ValueError("invalid curriculum units")
        tiered = entry.get("l2Tiered")
        if tiered is not None:
            base = units or entry["l2"]
            if isinstance(base, str):
                valid = isinstance(tiered, str) and canonical_phrase(strip_tiers(tiered)) == canonical_phrase(
                    base
                )
            else:
                valid = (
                    isinstance(tiered, list)
                    and len(tiered) == len(base)
                    and all(
                        isinstance(t, str) and canonical_phrase(strip_tiers(t)) == canonical_phrase(b)
                        for t, b in zip(tiered, base)
                    )
                )
            if not valid:
                raise ValueError("curriculum tier annotation drift")
        if "l1_literal" in entry:
            grouped_literal(entry)


def identity_aliases(aliases: Mapping[str, str]) -> dict[str, str]:
    """Validate an explicit display-spelling → original-ledger-key map.

    One hop only: reject chains/cycles instead of choosing a new authority.
    Multiple spellings may deliberately resolve to one original identity.
    """
    result: dict[str, str] = {}
    for alias, original in aliases.items():
        if not isinstance(alias, str) or not isinstance(original, str) or not alias or not original:
            raise ValueError("invalid phrase alias")
        key, value = canonical_phrase(alias), canonical_phrase(original)
        if key in result and result[key] != value:
            raise ValueError("conflicting canonical phrase alias")
        result[key] = value
    if any(value in result and result[value] != value for value in result.values()):
        raise ValueError("phrase aliases must point directly to original keys")
    return result


def phrase_identity(text: str, aliases: Mapping[str, str] | None = None) -> str:
    key = canonical_phrase(text)
    return identity_aliases(aliases or {}).get(key, key)


def _tokens(text: str) -> list[str]:
    # Punctuation is display, never identity. No case-folding Vietnamese tokens.
    return re.findall(r"\w+", canonical_phrase(text))


def grouped_literal(entry: Mapping) -> tuple[str, ...] | None:
    """Aligned pairs cover every unit in order, with no dropped particles.

    Missing pairs return None, preserving the explicit legacy-literal fallback.
    Pairs crossing a unit boundary are rejected, not guessed into a group.
    """
    if "l1_literal" not in entry:
        return None
    pairs = entry["l1_literal"]
    units = entry.get("units") or [entry.get("phrase", entry.get("l2", ""))]
    if not isinstance(pairs, list) or not pairs or not isinstance(units, list):
        raise ValueError("aligned literal pairs missing")
    if any(
        not isinstance(p, (list, tuple))
        or len(p) != 2
        or any(not isinstance(s, str) or not js_trim(s) for s in p)
        for p in pairs
    ):
        raise ValueError("invalid aligned literal pair")
    phrase = entry.get("phrase", entry.get("l2", ""))
    if [t for u in units for t in _tokens(u)] != _tokens(phrase):
        raise ValueError("units do not cover phrase")
    groups: list[str] = []
    index = 0
    for unit in units:
        words = _tokens(unit)
        used: list[str] = []
        glosses: list[str] = []
        while len(used) < len(words) and index < len(pairs):
            target, gloss = pairs[index]
            tokens = _tokens(target)
            if not tokens:
                raise ValueError("empty aligned literal target")
            used.extend(tokens)
            glosses.append(gloss)
            index += 1
        if used != words:
            raise ValueError("aligned literal does not cover unit")
        groups.append(" — ".join(glosses))
    if index != len(pairs):
        raise ValueError("extra aligned literal pairs")
    return tuple(groups)


def runtime_curriculum(profile, *, additions=()) -> tuple[dict, ...]:
    """Current sample plus explicit metadata and the learner's durable additions.

    Optional failures are handled by compile, rather than silently replacing a
    malformed learner queue with an unrelated inventory.
    """
    from pathlib import Path

    if profile.curriculum_path:
        raw = json.loads(Path(profile.curriculum_path).read_text(encoding="utf-8"))
    else:
        raw = json.loads(files(__package__).joinpath("data/curriculum.vi.json").read_text(encoding="utf-8"))
    rows = deepcopy(raw["focusPhrases"])
    validate_inventory(rows)
    metadata = {} if profile.curriculum_path else json.loads(
        files(__package__).joinpath("data/curriculum.vi.metadata.json").read_text(encoding="utf-8")
    )
    additions = deepcopy(list(additions))
    validate_inventory(additions)
    # NFC l2 deduplicates shipped phrases; learner edits (including landed) win.
    by_key = {canonical_phrase(r["l2"]): r for r in rows}
    for row in additions:
        key = canonical_phrase(row["l2"])
        row["learner_phrase"] = True
        by_key[key] = {**by_key.get(key, {}), **row}
    result = []
    for original in by_key.values():
        row = {**metadata.get(original["id"], {}), **original}
        row.setdefault("phrase", row["l2"])
        row.setdefault("translation", row["l1"])
        row.setdefault("units", [row["phrase"]])
        if isinstance(row.get("l2Tiered"), str) and len(row["units"]) == 1:
            row["l2Tiered"] = [row["l2Tiered"]]
        row.setdefault("lesson_type", "learning_sentence")
        row.setdefault("vocabulary", [])
        row.setdefault("structure_pauses", [])
        # Source phrases, not unit joins, own punctuation and SRS identity.
        row["ledger_identity"] = canonical_phrase(row["phrase"])
        result.append(row)
    return tuple(result)
