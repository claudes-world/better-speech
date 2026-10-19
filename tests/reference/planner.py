"""Pure multilingual sequencing with immutable render snapshots.

Source is normalized prose supplied by the owning stage, never model `rest`.
Placements must already pass semantic eligibility/caps in polyglot_selection.
This module does no synthesis and never records a lesson as heard.
"""

from __future__ import annotations

import hashlib
import json
import math
from collections.abc import Callable, Mapping, Sequence
from dataclasses import asdict, dataclass, replace

from better_speech.boundaries import paragraph_boundaries
from better_speech.curriculum import canonical_phrase, js_trim
from better_speech.pacing import compile_lesson, resolve_profile
from tests.reference.legacy_pacing import render_pauses_for_audio
from tests.reference.segments import l2_units, validate_segments

from .voices import exposure_role, l2_rate_for, resolve_l1_voice, resolve_l2_voice, resolve_repeat_voice

DEFAULTS = {
    "repeat": False,
    "repeatSecondVoice": True,
    "pauseS": 0.6,
    "chunkPauseS": 0.4,
    "repeatPauseS": 1.0,
    "tailL1Lead": True,
    "midL1Close": True,
    "midL1Lead": False,
}


def _sequence(segment: Mapping, config: Mapping, l1: Callable, l2: Callable, silence: Callable) -> list:
    """Exact original role/open/close ordering; callbacks own render policy."""
    result = []
    has_l2 = bool(l2_units(segment))
    role = exposure_role(segment, config) if has_l2 and segment.get("l1") else None
    repeat = config["repeat"]
    mode = segment.get("mode", "open")
    if role is not None:
        result += l2("main" if role else "lead", 1)
        result += [silence(0.25), l1(segment["l1"], "translation"), silence(0.25)]
        result += l2("lead" if role else "main", 2)
        result += [silence(config["pauseS"])]
    elif has_l2 and segment.get("l1") and mode == "close":
        result += [l1(segment["l1"], "translation"), silence(0.25)] + l2("lead", 1)
        if repeat:
            result += [
                silence(config["repeatPauseS"]),
                l1(
                    segment.get("literal", segment["l1"]),
                    "literal" if "literal" in segment else "translation",
                ),
                silence(0.25),
            ]
            result += l2("echo", 2)
        result += [silence(0.25)]
    elif has_l2:
        result += l2("lead", 1)
        if segment.get("l1"):
            result += [silence(0.25), l1(segment["l1"], "translation")]
        if repeat:
            result += [silence(config["repeatPauseS"])]
            if segment.get("literal"):
                result += [l1(segment["literal"], "literal"), silence(0.25)]
            result += l2("echo", 2)
        result += [silence(0.25 if repeat else config["pauseS"])]
    elif segment.get("l1"):
        result += [l1(segment["l1"], "translation"), silence(0.25)]
    if segment.get("rest") and js_trim(segment["rest"]):
        result += [l1(segment["rest"], "source")]
    return result


def legacy_sequence(document: object, config: Mapping, *, random: Callable[[], float]) -> tuple[dict, ...]:
    """Parity-only original unit sequencing; not E1 source/annotation authority.

    Legacy punctuation maps remain separate from the owner lesson compiler.
    """
    checked = validate_segments(document)
    if not checked["ok"]:
        raise ValueError("invalid legacy lesson segments")
    cfg = {**DEFAULTS, **config}
    lead, _ = resolve_l2_voice(cfg, random=random)
    main, _ = resolve_l1_voice(cfg)
    echo = (
        resolve_repeat_voice(lead, random=random, repeat=cfg["repeat"], second_voice=cfg["repeatSecondVoice"])
        or lead
    )

    def voice_dict(voice):
        return {k: v for k, v in asdict(voice).items() if v is not None}

    def silence(seconds):
        return {"kind": "silence", "seconds": seconds}

    def l1(text, purpose):
        return {
            "kind": "tts",
            "lang": "l1",
            "text": text,
            "voice": voice_dict(main),
            "rate": cfg.get("l1Rate", 1.15),
        }

    steps = []
    for segment in checked["value"]["segments"]:

        def l2(role, occurrence):
            voice = {"main": main, "lead": lead, "echo": echo}[role]
            rendered = []
            tiered = l2_units(segment, tiered=True)
            for i, unit in enumerate(l2_units(segment)):
                if i:
                    rendered.append(silence(cfg["chunkPauseS"]))
                text = (
                    render_pauses_for_audio(tiered[i], voice.provider, cfg.get("pauseStyle", "16"))
                    if i < len(tiered)
                    else unit
                )
                rendered.append(
                    {
                        "kind": "tts",
                        "lang": "l2",
                        "text": text,
                        "voice": voice_dict(voice),
                        "rate": l2_rate_for(voice, cfg),
                    }
                )
            return rendered

        steps.extend(_sequence(segment, cfg, l1, l2, silence))
    return tuple(steps)


@dataclass(frozen=True)
class RenderVoice:
    provider: str
    model: str
    voice: str
    rate: float
    voice_settings_json: str = "{}"
    audio_format: str = "ogg"

    def __post_init__(self):
        if any(
            not isinstance(v, str) or not v
            for v in (self.provider, self.model, self.voice, self.audio_format)
        ):
            raise ValueError("render voice requires actual provider/model/voice/format")
        if type(self.rate) not in (int, float) or not math.isfinite(self.rate) or self.rate <= 0:
            raise ValueError("render rate must be positive and finite")
        settings = json.loads(self.voice_settings_json)
        if not isinstance(settings, dict):
            raise ValueError("voice settings must be an object")
        object.__setattr__(
            self,
            "voice_settings_json",
            json.dumps(settings, ensure_ascii=False, sort_keys=True, separators=(",", ":"), allow_nan=False),
        )


@dataclass(frozen=True)
class LessonPlacement:
    lesson_id: str
    offset: int
    position: str  # start or end: controls pacing slots, not model prose
    lead: RenderVoice
    echo: RenderVoice
    level: int = 1
    mode: str = "open"


@dataclass(frozen=True)
class SpeechStep:
    kind: str
    text: str = ""
    display_text: str = ""
    provider: str = ""
    model: str = ""
    voice: str = ""
    rate: float = 1
    input_mode: str = "text"
    audio_format: str = "ogg"
    voice_settings_json: str = "{}"
    seconds: float = 0
    purpose: str = ""
    lesson_id: str = ""
    source_start: int | None = None
    source_end: int | None = None


@dataclass(frozen=True)
class SpeechPlan:
    source: str
    steps: tuple[SpeechStep, ...]
    settings_json: str
    status: str
    reason: str | None = None
    schema_version: int = 1

    def digest(self) -> str:
        return hashlib.sha256(
            json.dumps(
                asdict(self), ensure_ascii=False, sort_keys=True, separators=(",", ":"), allow_nan=False
            ).encode()
        ).hexdigest()


def _tts(text: str, display: str, voice: RenderVoice, **fields) -> SpeechStep:
    return SpeechStep(
        "tts",
        text,
        display,
        voice.provider,
        voice.model,
        voice.voice,
        voice.rate,
        audio_format=voice.audio_format,
        voice_settings_json=voice.voice_settings_json,
        **fields,
    )


def _chunks(text: str, limit: int = 4000):
    """Complete contiguous UTF-16-bounded chunks, no source truncation."""
    start = 0
    while start < len(text):
        end, units = start, 0
        while end < len(text) and units + (2 if ord(text[end]) > 0xFFFF else 1) <= limit:
            units += 2 if ord(text[end]) > 0xFFFF else 1
            end += 1
        if end < len(text):
            spaces = [i for i in range(start, end) if not js_trim(text[i])]
            if spaces:
                end = spaces[-1] + 1
        yield start, end, text[start:end]
        start = end


def build_speech_plan(
    source: str,
    *,
    inventory: Sequence[Mapping],
    placements: Sequence[LessonPlacement],
    main: RenderVoice,
    settings: Mapping,
    protected_ranges: Sequence[tuple[int, int]] = (),
) -> SpeechPlan:
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
    for key in ("repeat", "repeatSecondVoice", "tailL1Lead", "midL1Close", "midL1Lead"):
        if type(cfg[key]) is not bool:
            cfg[key] = DEFAULTS[key]
    starts, ends = paragraph_boundaries(source)
    boundaries = starts | ends

    def source_steps(start, end):
        return [
            _tts(text, text, main, purpose="source", source_start=start + a, source_end=start + b)
            for a, b, text in _chunks(source[start:end])
        ]

    def plain(reason):
        return SpeechPlan(source, tuple(source_steps(0, len(source))), frozen, "plain", reason)

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
        if len(placements) > 4:
            raise ValueError("too many lesson placements")
        steps: list[SpeechStep] = []
        seen: set[str] = set()
        counts = {"connector": 0, "learning_sentence": 0}
        cursor = 0
        for placement in sorted(placements, key=lambda p: p.offset):
            if placement.lesson_id not in entries:
                continue  # unknown ID drops only this placement
            record = entries[placement.lesson_id]
            offset = placement.offset
            if (
                type(offset) is not int
                or not 0 <= offset <= len(source)
                or offset not in boundaries
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
            counts[kind] += 1
            if counts["connector"] > 3 or counts["learning_sentence"] > 1:
                raise ValueError("lesson class cap exceeded")
            if kind == "learning_sentence" and (offset != len(source) or placement.position != "end"):
                raise ValueError("learning sentence must close the note")
            seen.add(identity)
            steps.extend(source_steps(cursor, offset))
            cursor = offset
            # Never put source prose into a model-provided rest. This synthetic
            # marker only selects original role ordering and is never emitted.
            segment = {"l2": phrase, "l1": record["translation"], "mode": placement.mode}
            if placement.position == "start":
                segment["rest"] = "following source"
            literal = record.get("literal")
            if literal:
                segment["literal"] = " — ".join(literal) if isinstance(literal, list) else literal

            def silence(seconds):
                return SpeechStep("silence", seconds=seconds)

            def l1(text, purpose):
                return _tts(text, text, main, purpose=purpose, lesson_id=placement.lesson_id)

            def l2(role, occurrence):
                if role == "main":
                    # Main Vietnamese is whole/plain, never paced unit seams.
                    return [
                        _tts(
                            phrase,
                            phrase,
                            replace(main, rate=cfg.get("mainL2Rate", 0.9)),
                            purpose="lesson",
                            lesson_id=placement.lesson_id,
                        )
                    ]
                voice = (
                    placement.lead
                    if role == "lead" or placement.level == 2 or not cfg["repeatSecondVoice"]
                    else placement.echo
                )
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
                            _tts(unit, unit, voice, purpose="lesson", lesson_id=placement.lesson_id)
                        )
                    return rendered
                paced = compile_lesson(dict(record), profile=profile)
                return [_tts(paced.rendered, phrase, voice, purpose="lesson", lesson_id=placement.lesson_id)]

            lesson_steps = _sequence(segment, cfg, l1, l2, silence)
            steps.extend(step for step in lesson_steps if step.purpose != "source")
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
        return SpeechPlan(source, tuple(steps), frozen, "applied" if seen else "plain")
    except (ValueError, KeyError, TypeError, OverflowError):
        return plain("invalid optional lesson plan")
