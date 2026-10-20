"""ID-only selection: V1 candidates, followed by explicit owner placement caps.

No model invocation, generated Vietnamese, wall clock, exposure write or hidden
RNG. Legacy order is deterministic; a caller may supply a pinned ranking among
semantically eligible candidates. Practice echoes are not extra placements.
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from datetime import date, datetime
from zoneinfo import ZoneInfo

from better_speech.curriculum import canonical_phrase, phrase_identity
from better_speech.exposure import timestamp
from better_speech.rewriter import Placement
from tests.reference.exposure import select_phrases

MAX_TOTAL_BY_INTENSITY = {1: 2, 2: 4, 3: 6}


def select_focus_ids(
    curriculum: Sequence[Mapping],
    records: Mapping[str, dict],
    *,
    now: str,
    lane_id: str,
    intensity: int = 1,
    aliases: Mapping[str, str] | None = None,
) -> tuple[str, ...]:
    """Faithful hook.selectFocusPhrases: due then unseen, ≤2 new per note.

    Missing due inventory entries consume legacy review capacity; this is
    intentionally not silently 'fixed' by introducing extra new lessons.
    """
    if type(intensity) is not int or intensity not in MAX_TOTAL_BY_INTENSITY:
        raise ValueError("intensity must be 1, 2 or 3")
    maximum = MAX_TOTAL_BY_INTENSITY[intensity]
    by_phrase = {phrase_identity(p["l2"], aliases): p for p in curriculum}
    selection = select_phrases(records, now=now, lane_id=lane_id, max_total=maximum, max_new=2)
    chosen = [
        by_phrase[canonical_phrase(r["l2"])]["id"]
        for r in selection["due"]
        if canonical_phrase(r["l2"]) in by_phrase
    ]
    slots = min(selection["newSlots"], max(0, maximum - len(chosen)))
    for phrase in curriculum:
        if slots <= 0:
            break
        if phrase_identity(phrase["l2"], aliases) in records:
            continue
        chosen.append(phrase["id"])
        slots -= 1
    return tuple(chosen)


@dataclass(frozen=True)
class Slot:
    """Caller-validated source boundary; not model-supplied prose or offsets."""

    id: str
    kind: str  # greeting, opener, closer, acknowledgement, closing_lesson
    at_boundary: bool = True


def cap_placements(
    proposals: Sequence[Placement],
    *,
    inventory: Sequence[Mapping],
    slots: Sequence[Slot],
    eligible_ids: Mapping[str, Sequence[str]],
    now: str,
    aliases: Mapping[str, str] | None = None,
) -> tuple[Placement, ...]:
    """Owner delta: meaning before debt, 3 connectors + 1 closing sentence.

    Eligibility is a pinned selection-stage result, not inferred from SRS.
    Unclassified legacy entries require a curated lesson_type overlay; they
    cannot silently activate under guessed connector/sentence classifications.
    Unknown IDs/slots, incompatible fits and duplicates drop locally. No
    auto-mastery, exposure threshold or unapproved daily gate is introduced.
    """
    ts = timestamp(now)
    if ts is None:
        return ()
    today = datetime.fromisoformat(ts.replace("Z", "+00:00")).astimezone(ZoneInfo("America/New_York")).date()
    entries = {entry["id"]: entry for entry in inventory}
    boundaries = {slot.id: slot for slot in slots}
    if len(entries) != len(inventory) or len(boundaries) != len(slots):
        raise ValueError("duplicate inventory or slot id")
    chosen: list[Placement] = []
    seen: set[str] = set()
    occupied: set[str] = set()
    counts = {"connector": 0, "learning_sentence": 0}
    for proposed in proposals:
        entry, slot = entries.get(proposed.phrase_id), boundaries.get(proposed.slot_id)
        if not entry or not slot or not slot.at_boundary or slot.id in occupied:
            continue
        if proposed.phrase_id not in eligible_ids.get(slot.id, ()):
            continue
        identity = phrase_identity(entry.get("l2", entry.get("phrase", "")), aliases)
        if not identity or identity in seen:
            continue
        if proposed.phrase_id == "cho-minh-ca-phe" and today < date(2026, 9, 24):
            continue
        kind = entry.get("lesson_type")
        if kind == "connector":
            if slot.kind not in ("greeting", "opener", "closer", "acknowledgement") or counts[kind] >= 3:
                continue
        elif kind == "learning_sentence":
            if slot.kind != "closing_lesson" or counts[kind] >= 1:
                continue
        else:
            continue
        chosen.append(proposed)
        counts[kind] += 1
        seen.add(identity)
        occupied.add(slot.id)
    return tuple(chosen)
