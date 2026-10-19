"""Pure pinned-RNG voice roles and echo selection; no provider invocation.

Runtime voices and lead weights come from the configured profile; no roster
is built into the library.
"""

from __future__ import annotations

import math
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass


@dataclass(frozen=True)
class VoiceRef:
    provider: str
    voice: str
    region: str | None = None


def _roll(random: Callable[[], float]) -> float:
    value = random()
    if type(value) not in (int, float) or not math.isfinite(value) or not 0 <= value < 1:
        raise ValueError("pinned voice RNG must return a finite number in [0,1)")
    return value


def _pick(pool: Sequence[VoiceRef], random: Callable[[], float]) -> VoiceRef:
    return pool[math.floor(_roll(random) * len(pool))]


def resolve_repeat_voice(
    primary: VoiceRef,
    *,
    random: Callable[[], float],
    repeat: bool = False,
    second_voice: bool = True,
    pool: Sequence[VoiceRef] = (),
) -> VoiceRef | None:
    """Legacy accent preference. None means the executor reuses primary."""
    if not repeat or not second_voice:
        return None
    other = [
        v for v in pool if v.voice != primary.voice and v.region is not None and v.region != primary.region
    ]
    candidates = other or [v for v in pool if v.voice != primary.voice]
    return _pick(candidates, random) if candidates else None


@dataclass(frozen=True)
class LessonVoices:
    main: VoiceRef
    lead: VoiceRef
    echo: VoiceRef


def choose_lesson_voices(
    *,
    main: VoiceRef,
    random: Callable[[], float],
    weights: Mapping[str, float] | None = None,
    excluded: Sequence[str] = (),
    repeat: bool = True,
    second_voice: bool = True,
    level: int = 1,
    pool: Sequence[VoiceRef] = (),
) -> LessonVoices | None:
    """Owner delta: weighted eligible lead, safe echo, lane-owned narrator.

    Explicit pool is the resolved immutable settings snapshot. No roster is built into the library; callers must configure the pool. Weights are
    lead-selection weights; echo uniformly chooses eligible accent candidates.
    No eligible voice returns None so caller retains plain main-voice output.
    Level 2 never introduces a new accent. Caller persists selections so retry
    does not reroll. No implicit RNG or mastery policy exists here.
    """
    if level not in (1, 2) or type(level) is bool:
        raise ValueError("manual level must be 1 or 2")
    if len({v.voice for v in pool}) != len(pool):
        raise ValueError("duplicate voice id in resolved pool")
    configured = {}
    if weights is not None:
        configured.update(weights)
    eligible: list[VoiceRef] = []
    amounts: list[float] = []
    for voice in pool:
        if voice.voice in excluded:
            continue
        weight = configured.get(voice.voice, 1)
        if type(weight) not in (int, float) or not math.isfinite(weight) or not 0 <= weight <= 5:
            raise ValueError("voice lead weight must be finite and between 0 and 5")
        if weight > 0:
            eligible.append(voice)
            amounts.append(weight)
    if not eligible:
        return None
    target = _roll(random) * sum(amounts)
    lead = eligible[-1]
    for voice, amount in zip(eligible, amounts):
        if target < amount:
            lead = voice
            break
        target -= amount
    echo = resolve_repeat_voice(
        lead, random=random, repeat=repeat, second_voice=second_voice and level == 1, pool=eligible
    )
    return LessonVoices(main, lead, echo or lead)
