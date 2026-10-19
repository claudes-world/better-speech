"""Optional ID-only preparation under existing shared helper/deadline authority.

Caller supplies curated inventory and pinned exposure/time/seed. Source boundaries
are derived locally; the shared helper chooses semantic fit. This module does not choose
providers, persist jobs, synthesize, send, or count exposures. Model transport is
the same bounded subscription call seam as heteronym preparation.
"""

from __future__ import annotations

import json
import math
import re
from collections.abc import Callable, Mapping, Sequence
from dataclasses import asdict, dataclass

from better_speech.boundaries import paragraph_boundaries
from better_speech.curriculum import js_trim, phrase_identity
from better_speech.rewriter import parse_placements
from tests.reference.selection import MAX_TOTAL_BY_INTENSITY, Slot, cap_placements, select_focus_ids

from .model_calls import ROUTES, ModelAttempt
from .stages import StageContext, StageInput, StageResult

VERSION = "polyglot-preparation-2"
PROMPT_VERSION = "inventory-slots-2"


def _parse_response(raw: str):
    """Shared helper transport shapes, followed by strict placement schema.

    Envelope normalization never drops authored prose/extra item fields.
    """
    if not isinstance(raw, str) or len(raw) > 32768:
        return None
    try:
        direct = parse_placements(raw)
        if direct is not None:
            return direct
        text = js_trim(raw)
        fenced = re.fullmatch(r"```(?:json)?([\s\S]*?)```", text)
        if fenced:
            text = js_trim(fenced.group(1))
        try:
            value = json.loads(text)
        except ValueError:
            value = [json.loads(line) for line in text.splitlines() if js_trim(line)]
        if isinstance(value, dict):
            if set(value) in ({"items"}, {"results"}):
                value = next(iter(value.values()))
            elif set(value) == {"phraseId", "slotId"}:
                value = [value]
            else:
                return None
        if not isinstance(value, list):
            return None
        return parse_placements(json.dumps({"placements": value}, allow_nan=False))
    except (ValueError, TypeError, RecursionError):
        return None


@dataclass(frozen=True)
class TrustedSlot:
    id: str
    kind: str
    offset: int
    position: str  # start/end role position, supplied by trusted caller


def structural_slots(source: str) -> tuple[TrustedSlot, ...]:
    """Stable paragraph boundaries, never offsets authored by a model.

    Treat closed and unclosed fenced blocks as protected. This is boundary
    detection, not a classifier of conversational meaning.
    """
    starts, ends = paragraph_boundaries(source)
    slots = []
    for kind, offsets, position in (("opener", starts, "start"), ("closer", ends, "end")):
        for offset in sorted(offsets):
            slots.append(TrustedSlot(f"{kind}:{offset}", kind, offset, position))
    if len(source) in ends:
        slots.append(TrustedSlot("closing_lesson", "closing_lesson", len(source), "end"))
    return tuple(slots)


@dataclass(frozen=True)
class SelectedPlacement:
    phrase_id: str
    slot_id: str
    offset: int
    position: str


@dataclass(frozen=True)
class PlanSpan:
    kind: str  # source/lesson; lesson spans have zero width
    start: int
    end: int
    phrase_id: str = ""
    slot_id: str = ""


@dataclass(frozen=True)
class PolyglotPreparation:
    source: str
    placements: tuple[SelectedPlacement, ...]
    spans: tuple[PlanSpan, ...]
    attempts: tuple[ModelAttempt, ...]
    snapshot_json: str
    candidate_ids: tuple[str, ...] = ()


def _spans(source: str, placements: tuple[SelectedPlacement, ...]) -> tuple[PlanSpan, ...]:
    spans, cursor = [], 0
    for p in placements:
        if p.offset > cursor:
            spans.append(PlanSpan("source", cursor, p.offset))
        spans.append(PlanSpan("lesson", p.offset, p.offset, p.phrase_id, p.slot_id))
        cursor = p.offset
    if cursor < len(source):
        spans.append(PlanSpan("source", cursor, len(source)))
    return tuple(spans)


def prepare(
    source: StageInput,
    context: StageContext,
    *,
    settings: Mapping,
    inventory: Sequence[Mapping],
    exposure: Mapping,
    lane_id: str,
    slots: Sequence[TrustedSlot] | None = None,
    eligible_ids: Mapping[str, Sequence[str]] | None = None,
    selection_time: str,
    seed: str | int,
    call: Callable[[str, dict, float], str],
    aliases: Mapping[str, str] | None = None,
) -> StageResult[PolyglotPreparation]:
    """Prepare deterministic source spans around capped ID selections.

    Optional slots/eligible_ids restrict structural pairs; they are not required
    semantic judgments. The helper may select none. Settings are an already-authorized resolved polyglot snapshot, not process
    environment. Exposure is a pinned lane-aware record mapping. The seed is
    retained for subsequent voice choice, never rerolled or consumed here.
    An empty valid response is success. Invalid output may use the next shared
    route only within the SAME deadline. Transport must enforce timeout and
    cancellation; after-return checks additionally discard late responses.
    """
    snapshot, identity, candidates = "{}", "", ()
    attempts: list[ModelAttempt] = []

    def result(status, reason, chosen=()):
        plan = PolyglotPreparation(
            source.source,
            tuple(chosen),
            _spans(source.source, tuple(chosen)),
            tuple(attempts),
            snapshot,
            candidates,
        )
        return StageResult(status, plan, identity, reason)

    try:
        if not isinstance(settings, Mapping) or not isinstance(exposure, Mapping):
            return result("skipped", "invalid_optional_inputs")
        # Copy everything before model calls; helpers cannot mutate authority.
        slots = structural_slots(source.source) if slots is None else slots
        snapshot = json.dumps(
            {
                "settings": dict(settings),
                "inventory": list(inventory),
                "exposure": dict(exposure),
                "lane_id": lane_id,
                "slots": [asdict(s) for s in slots],
                "eligible_ids": None if eligible_ids is None else dict(eligible_ids),
                "selection_time": selection_time,
                "seed": seed,
                "aliases": dict(aliases or {}),
            },
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
            allow_nan=False,
        )
        pinned = json.loads(snapshot)
        identity = source.cache_identity(
            "polyglot",
            VERSION,
            inputs={"snapshot": pinned, "prompt_version": PROMPT_VERSION, "routes": ROUTES},
        )
        cfg, inventory = pinned["settings"], pinned["inventory"]
        if cfg.get("enabled") is not True:
            return result("skipped", "disabled")
        if not js_trim(source.source):
            return result("skipped", "empty_source")
        if context.remaining() <= 0:
            return result("skipped", "deadline_or_cancelled")
        budget = cfg.get("rewriteBudgetMs", context.remaining() * 1000)
        if type(budget) not in (int, float) or not math.isfinite(budget) or budget < 0:
            return result("skipped", "invalid_optional_settings")
        bounded = StageContext(
            min(context.deadline, context.clock() + min(budget / 1000, 180)), context.clock, context.cancelled
        )
        intensity = cfg.get("intensity", 1)
        intensity = int(intensity) if type(intensity) in (int, float) and intensity in (1, 2, 3) else 1
        # Legacy inventory has l2/l1; locked records have phrase/translation.
        # This is a field mapping, never inferred translation/classification.
        curated = [
            dict(row, l2=row.get("phrase", row.get("l2")))
            for row in inventory
            if isinstance(row, dict)
            and row.get("lesson_type") in ("connector", "learning_sentence")
            and isinstance(row.get("id"), str)
            and isinstance(row.get("phrase", row.get("l2")), str)
            and isinstance(row.get("translation", row.get("l1")), str)
        ]
        boundaries = {s.offset for s in structural_slots(source.source)}
        trusted = [
            TrustedSlot(**s)
            for s in pinned["slots"]
            if type(s["offset"]) is int
            and 0 <= s["offset"] <= len(source.source)
            and s["position"] in ("start", "end")
            and s["offset"] in boundaries
            and (
                s["kind"] != "closing_lesson" or s["offset"] == len(source.source) and s["position"] == "end"
            )
        ]
        # Offer structural possibilities, not a debt-capped semantic shortlist.
        # The one shared helper call decides fit; legacy selection stays intact.
        allowed = {
            s.id: tuple(
                r["id"]
                for r in curated
                if (r["lesson_type"] == "learning_sentence") == (s.kind == "closing_lesson")
                and (pinned["eligible_ids"] is None or r["id"] in pinned["eligible_ids"].get(s.id, ()))
            )
            for s in trusted
        }
        candidates = tuple(r["id"] for r in curated if any(r["id"] in ids for ids in allowed.values()))
        if not any(allowed.values()):
            return result("skipped", "no_eligible_slots")
        if len({s.id for s in trusted}) != len(trusted) or len({r["id"] for r in curated}) != len(curated):
            return result("skipped", "duplicate_inventory_or_slot_id")
        offered = {p for values in allowed.values() for p in values}
        payload = {
            "task": "polyglot_placements",
            "schema_version": 1,
            "instruction": "Return only {placements:[{phraseId,slotId}]}. Empty placements is valid. Select offered pairs only when semantically fitting, not because due. A closing lesson is separate from source prose. Never add filler, rewrite source, or author Vietnamese.",
            "source": source.source,
            "inventory": [
                {
                    "id": r["id"],
                    "phrase": r["l2"],
                    "translation": r.get("translation", r.get("l1")),
                    "lesson_type": r["lesson_type"],
                }
                for r in curated
                if r["id"] in offered
            ],
            "slots": [
                {"id": s.id, "kind": s.kind, "offset": s.offset, "eligible_ids": allowed[s.id]}
                for s in trusted
                if allowed[s.id]
            ],
        }
        for route in ROUTES:
            timeout = min(90, bounded.remaining())
            if timeout <= 0:
                return result("skipped", "deadline_or_cancelled")
            started = bounded.clock()
            try:
                raw = call(route, json.loads(json.dumps(payload)), timeout)
            except Exception:
                attempts.append(ModelAttempt(route, candidates, timeout, "unavailable"))
                continue
            if bounded.remaining() <= 0:
                attempts.append(ModelAttempt(route, candidates, timeout, "expired"))
                return result("skipped", "deadline_or_cancelled")
            if bounded.clock() - started >= timeout:
                attempts.append(ModelAttempt(route, candidates, timeout, "attempt_expired"))
                continue
            proposals = _parse_response(raw)
            if proposals is None:
                attempts.append(ModelAttempt(route, candidates, timeout, "invalid_plan"))
                continue
            # Validate each proposed pair before review preferences can consume
            # capacity. Repeated, conflicting and class caps apply after ranking.
            fitting = [
                p
                for p in proposals
                if cap_placements(
                    [p],
                    inventory=curated,
                    slots=[Slot(s.id, s.kind) for s in trusted],
                    eligible_ids=allowed,
                    now=pinned["selection_time"],
                    aliases=pinned["aliases"],
                )
            ]
            fitting_ids = {p.phrase_id for p in fitting}
            fitting_rows = [r for r in curated if r["id"] in fitting_ids]
            identities = {phrase_identity(r["l2"], pinned["aliases"]) for r in fitting_rows}
            records = {
                k: v
                for k, v in pinned["exposure"].items()
                if phrase_identity(k, pinned["aliases"]) in identities
            }
            ranked = list(
                select_focus_ids(
                    fitting_rows,
                    records,
                    now=pinned["selection_time"],
                    lane_id=pinned["lane_id"],
                    intensity=intensity,
                    aliases=pinned["aliases"],
                )
            )
            # Already-seen, not-yet-due phrases may still fit naturally. Review
            # debt is a preference, not permission to speak a fitting phrase.
            ranked.extend(
                r["id"]
                for r in fitting_rows
                if r["id"] not in ranked and phrase_identity(r["l2"], pinned["aliases"]) in records
            )
            ranked = ranked[: MAX_TOTAL_BY_INTENSITY[intensity]]
            ordered = [p for identifier in ranked for p in fitting if p.phrase_id == identifier]
            capped = cap_placements(
                ordered,
                inventory=curated,
                slots=[Slot(s.id, s.kind) for s in trusted],
                eligible_ids=allowed,
                now=pinned["selection_time"],
                aliases=pinned["aliases"],
            )
            by_slot = {s.id: s for s in trusted}
            chosen = tuple(
                sorted(
                    (
                        SelectedPlacement(
                            p.phrase_id, p.slot_id, by_slot[p.slot_id].offset, by_slot[p.slot_id].position
                        )
                        for p in capped
                    ),
                    key=lambda p: p.offset,
                )
            )
            partial = len(capped) != len(proposals)
            attempts.append(ModelAttempt(route, candidates, timeout, "partial" if partial else "complete"))
            return result(
                "partial" if partial else "applied",
                "invalid_placements_omitted" if partial else (None if chosen else "no_lesson_selected"),
                chosen,
            )
        return result("skipped", "helper_unavailable_or_invalid")
    except (ValueError, TypeError, KeyError, AttributeError, OverflowError, RecursionError):
        return result("skipped", "invalid_optional_inputs")
