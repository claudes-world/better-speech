"""TOML profiles and explicit paths; never read host-specific configuration."""

from __future__ import annotations

import json
import math
import os
import tomllib
from pathlib import Path

from .types import Profile, Voice


def _identity_fields(data):
    """Retain independently usable learner routing even when optional config fails."""
    from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

    fields = {key: data[key] for key in ("learner_id", "state_dir", "curriculum_path")
              if isinstance(data.get(key), str) and data[key].strip()}
    try:
        if isinstance(data.get("timezone"), str):
            ZoneInfo(data["timezone"])
            fields["timezone"] = data["timezone"]
    except (ValueError, ZoneInfoNotFoundError):
        pass
    return fields


def resolve_profile(profile: Profile) -> Profile:
    """Invalid optional settings disable lessons while preserving a usable narrator."""
    from dataclasses import replace

    diagnostics = []
    try:
        voice = profile.english
        voice.validate()
        from .providers.elevenlabs import ElevenLabsProvider
        from .providers.google import GoogleProvider

        builtin = {"google": GoogleProvider, "elevenlabs": ElevenLabsProvider}.get(voice.provider)
        if builtin and ("en" not in builtin.languages or voice.output_format not in builtin.formats):
            raise ValueError("unsupported narrator language/format")
    except Exception:
        profile = replace(profile, english=Profile().english)
        diagnostics.append("invalid_english_voice")
    try:
        if profile.heteronyms is None:
            profile = replace(profile, heteronyms=profile.model != "none")
        Profile.from_dict(profile.to_dict())
        for key in (
            "compile_timeout_s",
            "render_timeout_s",
            "provider_timeout_s",
            "model_timeout_s",
            "fallback_reserve_s",
            "pause_s",
            "chunk_pause_s",
            "repeat_pause_s",
            "long_break_seconds",
            "echo_rate",
            "anchor_floor_days",
        ):
            value = getattr(profile, key)
            minimum = (
                0
                if key
                in {"fallback_reserve_s", "pause_s", "chunk_pause_s", "repeat_pause_s", "long_break_seconds"}
                else 1e-9
            )
            if type(value) not in (int, float) or not math.isfinite(value) or value < minimum:
                raise ValueError("invalid numeric profile field")
        if not isinstance(profile.pacing, dict):
            raise ValueError("pacing must be an object")
        if any(
            type(getattr(profile, key)) is not bool for key in ("enabled", "repeat", "repeat_second_voice", "heteronyms")
        ):
            raise ValueError("invalid boolean profile field")
        if type(profile.intensity) is not int or profile.intensity not in (1, 2, 3):
            raise ValueError("invalid intensity")
        if type(profile.boost_daily_cap) is not int or profile.boost_daily_cap < 0:
            raise ValueError("invalid boost daily cap")
        if not isinstance(profile.learner_id, str) or not profile.learner_id:
            raise ValueError("invalid learner id")
        if any(not isinstance(reason, str) for reason in profile.config_diagnostics):
            raise ValueError("invalid diagnostic")
        if diagnostics:
            return replace(
                profile,
                enabled=False,
                config_diagnostics=tuple(profile.config_diagnostics) + tuple(diagnostics),
            )
        return profile
    except Exception:
        fallback = Profile(
            english=profile.english,
            enabled=False,
            **_identity_fields(vars(profile)),
            config_diagnostics=tuple(diagnostics) + ("invalid_profile",),
        )
        try:
            fallback.to_dict()
        except Exception:
            fallback = replace(fallback, english=Profile().english)
        return fallback


def state_dir(profile: Profile) -> Path:
    if profile.state_dir:
        return Path(profile.state_dir).expanduser()
    return Path(os.environ.get("XDG_DATA_HOME", Path.home() / ".local/share")) / "better-speech"


def cache_dir() -> Path:
    return Path(os.environ.get("XDG_CACHE_HOME", Path.home() / ".cache")) / "better-speech/clips"


def load_profile(path: str | Path | None = None) -> Profile:
    if path is None:
        candidate = (
            Path(os.environ.get("XDG_CONFIG_HOME", Path.home() / ".config")) / "better-speech/profile.toml"
        )
        if not candidate.is_file():
            return Profile()
        path = candidate
    path = Path(path).expanduser().resolve()
    try:
        data = tomllib.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return Profile(config_diagnostics=("profile_unavailable",))
    diagnostics = []
    pool_file = data.pop("voices_file", None)
    if pool_file:
        try:
            pool = json.loads((path.parent / pool_file).read_text(encoding="utf-8"))
            excluded = pool.get("exclude", [])
            data["voices"] = [
                dict(voice=key, weight=weight, region=pool.get("regions", {}).get(key))
                for key, weight in pool["weights"].items()
                if key not in excluded
            ]
        except (OSError, ValueError, KeyError, TypeError, AttributeError):
            diagnostics.append("voice_pool_unavailable")
            data["voices"] = []
    try:
        for key in ("curriculum_path", "state_dir"):
            if data.get(key):
                data[key] = str((path.parent / Path(data[key]).expanduser()).resolve())
        data["config_diagnostics"] = diagnostics
        return resolve_profile(Profile.from_dict(data))
    except (TypeError, ValueError):
        # A broken optional setting must not erase a usable configured narrator.
        english = data.get("english", {})
        try:
            voice = Voice(**english) if english else Profile().english
        except (TypeError, ValueError):
            voice = Profile().english
        return resolve_profile(Profile(
            english=voice, enabled=False, **_identity_fields(data),
            config_diagnostics=("invalid_profile",),
        ))
