"""Pure pinned-RNG voice roles and echo selection; no provider invocation.

V1 defaults and the later locked owner pool are separate contracts. Runtime
settings/main narrator come from the owning stage, never process environment.
"""

from __future__ import annotations

import math
import re
import unicodedata
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass


@dataclass(frozen=True)
class VoiceRef:
    provider: str
    voice: str
    region: str | None = None


# Exact V1 polyglot-speak.ts inventory/order, not current owner eligibility.
VOICE_POOL = (
    VoiceRef("elevenlabs", "CE5BJM57m1rtrAFVXfCq", "central"),
    VoiceRef("elevenlabs", "uN2lv5lSgnxa1hcYRElP", "central"),
    VoiceRef("elevenlabs", "9FhIljxdGFirnCpAoUES", "central"),
    VoiceRef("elevenlabs", "2G3KT0n4xucedtpsBlU2", "southern"),
    VoiceRef("google", "vi-VN-Chirp3-HD-Puck", "northern"),
    VoiceRef("google", "vi-VN-Chirp3-HD-Enceladus", "northern"),
    VoiceRef("google", "vi-VN-Chirp3-HD-Aoede", "northern"),
    VoiceRef("google", "vi-VN-Chirp3-HD-Leda", "northern"),
)
OPENAI_L2_POOL = tuple(VoiceRef("openai", name) for name in ("nova", "shimmer", "fable", "alloy"))
ROLE_DEFAULTS = {"tailL1Lead": True, "midL1Close": True, "midL1Lead": False, "l1InPool": False}

# Owner 10376 locked roster and lead weights, verbatim schema table order.
# This supersedes earlier CamHong exclusion; Google remains excluded.
LOCKED_LEAD_WEIGHTS = {
    "ZvBK3ITp9pAzXbi1yZZH": 1,
    "uN2lv5lSgnxa1hcYRElP": 2,
    "CE5BJM57m1rtrAFVXfCq": 4,
    "L5c6tGA8OiORYKxez5Zu": 4,
    "9FhIljxdGFirnCpAoUES": 1,
    "a3AkyqGG4v8Pg7SWQ0Y3": 4,
    "IZKDEBDVLx4BnFc8W03D": 2,
    "zRKf3RQpAJq11rSezsxD": 1,
    "iCOyvtdHUPOHaQYt93QQ": 3,
    "2G3KT0n4xucedtpsBlU2": 1,
    "x4KAhuXs2G8TfK9Zr7Q4": 3,
}
LOCKED_POOL = tuple(
    VoiceRef("elevenlabs", name, "southern" if i >= 9 else "central")
    for i, name in enumerate(LOCKED_LEAD_WEIGHTS)
)


def is_valid_voice_id(value: object) -> bool:
    return isinstance(value, str) and re.fullmatch(r"[A-Za-z0-9_:-]+", value) is not None


def _roll(random: Callable[[], float]) -> float:
    value = random()
    if type(value) not in (int, float) or not math.isfinite(value) or not 0 <= value < 1:
        raise ValueError("pinned voice RNG must return a finite number in [0,1)")
    return value


def _pick(pool: Sequence[VoiceRef], random: Callable[[], float]) -> VoiceRef:
    return pool[math.floor(_roll(random) * len(pool))]


def resolve_role_settings(lane: Mapping, per_call: Mapping) -> dict[str, bool]:
    """Existing hook/tool precedence: engine defaults < lane < explicit call.

    False is an override. Omitted/null/nonboolean role knobs leave the lower
    resolved layer in charge, matching voiceRoleArgs/configFromArgs.
    """
    result = dict(ROLE_DEFAULTS)
    for layer in (lane, per_call):
        for name in ROLE_DEFAULTS:
            if type(layer.get(name)) is bool:
                result[name] = layer[name]
    return result


def resolve_l2_voice(config: Mapping, *, random: Callable[[], float]) -> tuple[VoiceRef, bool]:
    """Legacy exact RNG order. Return voice plus malformed-ID fallback flag."""
    name = config.get("l2Voice", "random")
    if name != "random" and not is_valid_voice_id(name):
        return _pick(VOICE_POOL, random), True
    if name == "random":
        if config.get("l1InPool", False) and _roll(random) < 0.25:
            return _pick(OPENAI_L2_POOL, random), False
        return _pick(VOICE_POOL, random), False
    pooled = next((voice for voice in VOICE_POOL if voice.voice == name), None)
    if pooled is not None:
        return pooled, False
    provider = config.get("l2Provider")
    if provider is None:
        provider = "google" if re.match(r"^[a-z]{2,3}-[A-Z]{2}-", name) else "elevenlabs"
    return VoiceRef(provider, name), False


def resolve_l1_voice(config: Mapping) -> tuple[VoiceRef, bool]:
    """Legacy narrator only. Owner API below requires the lane's main voice."""
    name = config.get("l1Voice", "onyx")
    valid = is_valid_voice_id(name)
    return VoiceRef("openai", name if valid else "onyx"), not valid


def l2_rate_for(voice: VoiceRef, config: Mapping) -> float:
    return config.get("l2RateGoogle", 0.95) if voice.provider == "google" else config.get("l2Rate", 0.9)


def resolve_repeat_voice(
    primary: VoiceRef,
    *,
    random: Callable[[], float],
    repeat: bool = False,
    second_voice: bool = True,
    pool: Sequence[VoiceRef] = VOICE_POOL,
) -> VoiceRef | None:
    """Legacy accent preference. None means the executor reuses primary."""
    if not repeat or not second_voice:
        return None
    other = [
        v for v in pool if v.voice != primary.voice and v.region is not None and v.region != primary.region
    ]
    candidates = other or [v for v in pool if v.voice != primary.voice]
    return _pick(candidates, random) if candidates else None


def sentence_position(segment: Mapping) -> str:
    rest = re.sub(r"⟦[^⟧]*⟧", "", segment.get("rest") or "")
    return "mid" if any(unicodedata.category(char)[0] in ("L", "N") for char in rest) else "tail"


def exposure_role(segment: Mapping, config: Mapping) -> bool | None:
    """True=narrator leads; False=native leads; None=legacy echo sequence.

    Caller must invoke only for glossed L2 segments. Three-exposure roles
    supersede practice echo; full step sequencing is a separate planner slice.
    """
    cfg = {**ROLE_DEFAULTS, **config}
    if sentence_position(segment) == "tail":
        return True if cfg["tailL1Lead"] else None
    if cfg["midL1Lead"]:
        return True
    return False if cfg["midL1Close"] else None


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
    pool: Sequence[VoiceRef] = LOCKED_POOL,
) -> LessonVoices | None:
    """Owner delta: weighted eligible lead, safe echo, lane-owned narrator.

    Explicit pool is the resolved immutable settings snapshot. Default is the
    locked ElevenLabs roster; no provider registration is changed. Weights are
    lead-selection weights; echo uniformly chooses eligible accent candidates.
    No eligible voice returns None so caller retains plain main-voice output.
    Level 2 never introduces a new accent. Caller persists selections so retry
    does not reroll. No implicit RNG or mastery policy exists here.
    """
    if level not in (1, 2) or type(level) is bool:
        raise ValueError("manual level must be 1 or 2")
    if len({v.voice for v in pool}) != len(pool):
        raise ValueError("duplicate voice id in resolved pool")
    configured = dict(LOCKED_LEAD_WEIGHTS)
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
