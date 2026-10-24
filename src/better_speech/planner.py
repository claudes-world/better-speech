"""Pure multilingual sequencing with immutable render snapshots.

Source is normalized prose supplied by the owning stage, never model `rest`.
Placements must already pass the compiler's inventory and placement caps.
This module does no synthesis and never records a lesson as heard.
"""

from __future__ import annotations

import json
import math
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass, replace

from .boundaries import paragraph_boundaries
from .curriculum import canonical_phrase
from .pacing import compile_lesson, resolve_profile
from .types import Clip, Voice

DEFAULTS = {
    "repeat": True,
    "repeatSecondVoice": True,
    "pauseS": 0.6,
    "chunkPauseS": 0.4,
    "repeatPauseS": 1.0,
}


def _sequence(segment: Mapping, config: Mapping, l1: Callable, l2: Callable, silence: Callable) -> list:
    """Speak Vietnamese, its translation, then the optional Vietnamese echo."""
    result = []
    has_l2 = bool(segment.get("l2"))
    repeat = config["repeat"]
    if has_l2:
        result += l2(1)
        if segment.get("l1"):
            result += [silence(0.25), l1(segment["l1"], "translation")]
        if repeat:
            result += [silence(config["repeatPauseS"])]
            result += l2(2)
        result += [silence(0.25 if repeat else config["pauseS"])]
    elif segment.get("l1"):
        result += [l1(segment["l1"], "translation"), silence(0.25)]
    return result


@dataclass(frozen=True)
class LessonPlacement:
    lesson_id: str
    offset: int
    position: str  # start or end: controls pacing slots, not model prose
    lead: Voice
    echo: Voice | None
    level: int = 1
    mode: str = "open"


def _tts(text: str, display: str, voice: Voice, **fields) -> Clip:
    voice.validate()
    return Clip(
        text, "vi" if fields.get("purpose") == "lesson" else "en", voice.voice,
        voice.rate, 0, voice.provider, voice.model, voice.output_format,
        display, voice.settings_json, **fields,
    )


def _fold_pauses(clips):
    result = []
    for clip in clips:
        if clip.purpose == "silence" and result:
            result[-1] = replace(result[-1], pause_after_s=result[-1].pause_after_s + clip.pause_after_s)
        else:
            result.append(clip)
    return tuple(result)


def sequence_clips(
    source: str,
    *,
    inventory: Sequence[Mapping],
    placements: Sequence[LessonPlacement],
    main: Voice,
    settings: Mapping,
    protected_ranges: Sequence[tuple[int, int]] = (),
    opener_offsets: Sequence[int] = (0,),
) -> tuple[tuple[Clip, ...], str | None]:
    """Frozen source/speech separation after ID/slot semantic selection.

    Invalid optional lessons/oversized enriched plans fall back to COMPLETE
    monolingual source. No inherited 12k truncation. Every source step has an
    exact contiguous source range, even whitespace-only spans. Long-break unit
    fallback is explicit settings, never timestamp surgery.
    """
    invalid_settings = False
    try:
        if not isinstance(settings, Mapping):
            raise ValueError("settings must be an object")
        frozen = json.dumps(
            dict(settings), ensure_ascii=False, sort_keys=True, separators=(",", ":"), allow_nan=False
        )
    except (ValueError, TypeError, OverflowError):
        # Optional configuration cannot refuse complete main-voice source.
        # No invalid object is retained as supposedly immutable JSON evidence.
        frozen = "{}"
        invalid_settings = True
    cfg = {**DEFAULTS, **json.loads(frozen)}
    for key in ("repeat", "repeatSecondVoice"):
        if type(cfg[key]) is not bool:
            cfg[key] = DEFAULTS[key]
    _, ends = paragraph_boundaries(source)
    starts = frozenset(opener_offsets)

    def source_steps(start, end):
        return [_tts(source[start:end], source[start:end], main, purpose="source",
                     source_start=start, source_end=end)] if start < end else []

    def plain(reason):
        return tuple(source_steps(0, len(source))), reason

    if invalid_settings:
        return plain("invalid optional settings snapshot")
    if not placements:
        return plain(None)
    try:
        for key in ("pauseS", "repeatPauseS", "chunkPauseS"):
            if type(cfg[key]) not in (int, float) or not math.isfinite(cfg[key]) or cfg[key] < 0:
                raise ValueError("invalid silence setting")
        entries = {entry["id"]: entry for entry in inventory}
        if len(entries) != len(inventory):
            raise ValueError("duplicate curriculum id")
        if sum(entries[p.lesson_id].get("category") not in ("greeting", "opener", "closing")
               for p in placements if p.lesson_id in entries) > 4:
            raise ValueError("too many lesson placements")
        steps: list[Clip] = []
        seen: set[str] = set()
        counts = {"connector": 0, "learning_sentence": 0}
        cursor = 0
        bookends = {"opener": 0, "closing": 0}
        for placement in sorted(placements, key=lambda p: (p.offset, p.mode == "close")):
            if placement.lesson_id not in entries:
                continue  # unknown ID drops only this placement
            record = entries[placement.lesson_id]
            offset = placement.offset
            if (
                type(offset) is not int
                or not 0 <= offset <= len(source)
                or offset not in (starts if placement.position == "start" else ends)
                or any(a < offset < b for a, b in protected_ranges)
            ):
                raise ValueError("lesson is not at an authorized source boundary")
            if placement.position not in ("start", "end") or placement.mode not in ("open", "close"):
                raise ValueError("invalid lesson slot or mode")
            if type(placement.level) is bool or placement.level not in (1, 2):
                raise ValueError("invalid manual level")
            phrase = record["phrase"]
            if not isinstance(phrase, str) or not isinstance(record.get("translation"), str):
                raise ValueError("lesson speech fields must be strings")
            literal = record.get("literal")
            if (
                literal is not None
                and not isinstance(literal, str)
                and not (isinstance(literal, list) and all(isinstance(part, str) for part in literal))
            ):
                raise ValueError("literal gloss must contain only strings")
            # Even explicit long-break fallback must validate unit coverage.
            compile_lesson(dict(record), profile={"p0": "", "p1": "", "p2_0": "", "p2_1": ""})
            identity = canonical_phrase(record.get("ledger_identity", phrase))
            if identity in seen:
                raise ValueError("duplicate lesson placement")
            kind = record["lesson_type"]
            if kind not in counts:
                raise ValueError("unclassified lesson")
            category = record.get("category")
            if category in ("greeting", "opener", "closing"):
                bookend = "closing" if category == "closing" else "opener"
                bookends[bookend] += 1
                if bookends[bookend] > 1:
                    raise ValueError("duplicate message bookend")
                if category == "closing" and (
                    offset != len(source) or placement.position != "end" or placement.mode != "close"
                ):
                    raise ValueError("closing phrase must close the message")
            else:
                counts[kind] += 1
            if counts["connector"] > 3 or counts["learning_sentence"] > 1:
                raise ValueError("lesson class cap exceeded")
            if kind == "learning_sentence" and (offset != len(source) or placement.position != "end"):
                raise ValueError("learning sentence must close the note")
            seen.add(identity)
            steps.extend(source_steps(cursor, offset))
            cursor = offset
            segment = {"l2": phrase, "l1": record["translation"], "mode": placement.mode}
            literal = record.get("literal")
            if literal:
                segment["literal"] = " — ".join(literal) if isinstance(literal, list) else literal

            def silence(seconds):
                return replace(_tts("", "", main, purpose="silence"), pause_after_s=seconds)

            def l1(text, purpose):
                return _tts(text, text, main, purpose=purpose, phrase_key=identity)

            def l2(occurrence):
                voice = (
                    placement.lead
                    if occurrence == 1 or placement.level == 2 or not cfg["repeatSecondVoice"]
                    else placement.echo
                )
                if occurrence == 2 and "echo_rate" in cfg:
                    voice = replace(voice, rate=cfg["echo_rate"])
                profile = resolve_profile(
                    cfg.get("pacing", {}), slot=f"{placement.position}_{occurrence}", voice=voice.voice
                )
                unit_break = cfg.get("longBreakSeconds", 0)
                if type(unit_break) not in (int, float) or not math.isfinite(unit_break) or unit_break < 0:
                    raise ValueError("invalid explicit long break")
                if unit_break:
                    # Explicit fallback request beyond punctuation capabilities;
                    # original units remain intact, with no timestamp cutting.
                    rendered = []
                    for i, unit in enumerate(record["units"]):
                        if i:
                            rendered.append(silence(unit_break))
                        rendered.append(
                            _tts(unit, unit, voice, purpose="lesson", phrase_key=identity, echo=occurrence == 2)
                        )
                    return rendered
                paced = compile_lesson(dict(record), profile=profile)
                return [_tts(paced.rendered, phrase, voice, purpose="lesson", phrase_key=identity, echo=occurrence == 2)]

            lesson_cfg = {
                **cfg,
                "repeat": False
                if placement.level == 2 or (cfg["repeatSecondVoice"] and placement.echo is None)
                else (cfg["repeat"] or record.get("pending_learning") is True),
            }
            lesson_steps = _sequence(segment, lesson_cfg, l1, l2, silence)
            steps.extend(lesson_steps)
        steps.extend(source_steps(cursor, len(source)))
        if any(
            step.purpose != "source"
            and len(step.text.encode("utf-16-le", errors="surrogatepass")) // 2 > 4000
            for step in steps
        ):
            return plain("lesson step exceeds provider text bound")
        if sum(len(step.text.encode("utf-16-le", errors="surrogatepass")) // 2 for step in steps) > 12000:
            return plain("enriched plan exceeds speakable budget")
        if "".join(step.text for step in steps if step.purpose == "source") != source:
            return plain("source coverage mismatch")
        return _fold_pauses(steps), None
    except (ValueError, KeyError, TypeError, OverflowError):
        return plain("invalid optional lesson plan")
