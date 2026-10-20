"""Source-preserving Vietnamese lesson pacing (Composer's locked four slots).

Rendered punctuation is synthesis input only. The immutable phrase, unit and
literal fields remain presentation authority; this compiler never rewrites
the transcript. Invalid optional lesson records are rejected for the caller
to omit, not guessed into a different lesson.
"""

from __future__ import annotations

import re
import unicodedata
from dataclasses import dataclass

DEFAULT = {"p0": ", ", "p1": " – ", "p2_0": "", "p2_1": ""}
SLOTS = frozenset({"start_1", "start_2", "end_1", "end_2"})
PRECEDENCE = ("p2_0", "p2_1", "p1", "p0")
WORD = re.compile(r"\w+(?:['’\-]\w+)*", re.UNICODE)


@dataclass(frozen=True)
class PacedLesson:
    rendered: str
    pacing: str
    # Word offsets are against NFC phrase, not Markdown/source positions.
    candidates: tuple[tuple[int, tuple[str, ...]], ...]


def resolve_profile(config: object, *, slot: str, voice: str) -> dict[str, str]:
    if slot not in SLOTS:
        raise ValueError("unknown lesson placement slot")
    config = config if isinstance(config, dict) else {}
    voices = config.get("voices", {})
    voice_config = voices.get(voice, {}) if isinstance(voices, dict) else {}
    voice_config = voice_config if isinstance(voice_config, dict) else {}
    slots = config.get("slots", {})
    voice_slots = voice_config.get("slots", {})
    layers = [
        config.get("default"),
        slots.get(slot) if isinstance(slots, dict) else None,
        voice_config.get("default"),
        voice_slots.get(slot) if isinstance(voice_slots, dict) else None,
    ]
    profile = dict(DEFAULT)
    for layer in layers:
        if isinstance(layer, dict):
            for key in DEFAULT:
                value = layer.get(key)
                if isinstance(value, str):  # null/invalid values inherit; empty is explicitly off
                    profile[key] = value
    return profile


def _words(text: str) -> list[str]:
    return [m.group().casefold() for m in WORD.finditer(unicodedata.normalize("NFC", text))]


def compile_lesson(record: dict, *, profile: dict[str, str]) -> PacedLesson:
    phrase = unicodedata.normalize("NFC", record["phrase"])
    matches = list(WORD.finditer(phrase))
    words = [m.group().casefold() for m in matches]
    if not words:
        raise ValueError("lesson phrase has no words")
    units = record["units"]
    if not isinstance(units, list) or not units:
        raise ValueError("lesson units missing")
    candidates: dict[int, set[str]] = {}

    def add(index: int, layer: str) -> None:
        if index > 0:  # no leading pause at phrase start
            candidates.setdefault(index, set()).add(layer)

    position = 0
    for unit in units:
        tokens = _words(unit)
        if not tokens or words[position : position + len(tokens)] != tokens:
            raise ValueError("lesson units do not align with phrase")
        add(position, "p0")
        position += len(tokens)
    if position != len(words):
        raise ValueError("lesson units do not cover phrase")
    for index in range(1, len(matches)):
        gap = phrase[matches[index - 1].end() : matches[index].start()]
        if "," in gap:
            add(index, "p1")

    def locate(term: str, occurrence: int) -> int:
        if type(occurrence) is not int or occurrence < 1:
            raise ValueError("lesson occurrence must be positive")
        needle = _words(term)
        starts = [i for i in range(len(words)) if needle and words[i : i + len(needle)] == needle]
        if len(starts) < occurrence:
            raise ValueError("lesson span is not in phrase")
        return starts[occurrence - 1]

    for pause in record.get("structure_pauses", []):
        add(locate(pause["before"], pause.get("occurrence", 1)), "p2_0")
    for vocabulary in record.get("vocabulary", []):
        index = locate(vocabulary["term"], vocabulary.get("occurrence", 1))
        if vocabulary.get("emphasis") is not False:
            add(index, "p2_1")

    rendered = phrase[: matches[0].end()]
    debug = rendered
    manifest = []
    for index in range(1, len(matches)):
        left, right = matches[index - 1], matches[index]
        gap = phrase[left.end() : right.start()]
        layers = candidates.get(index, set())
        ordered = tuple(key for key in ("p0", "p1", "p2_0", "p2_1") if key in layers)
        if ordered:
            manifest.append((right.start(), ordered))
        # Sentence punctuation always wins. Other punctuation is not owned
        # by pacing either; preserve it instead of silently deleting it.
        punctuation = gap.strip().replace(",", "").strip()
        if punctuation:
            mark = gap
        else:
            mark = next(
                (profile.get(key, "") for key in PRECEDENCE if key in layers and profile.get(key, "")), " "
            )
            if not layers:
                mark = gap
        rendered += mark + right.group()
        debug += ("⟨" + ",".join(ordered) + "⟩" if ordered else gap) + right.group()
    suffix = phrase[matches[-1].end() :]
    return PacedLesson(rendered + suffix, debug + suffix, tuple(manifest))
