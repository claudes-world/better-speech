"""Public, serializable contracts. No provider clients or credentials in plans."""

from __future__ import annotations

import hashlib
import json
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Literal


def canonical_json(value: object) -> str:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"), allow_nan=False)


def digest(value: object) -> str:
    return hashlib.sha256(canonical_json(value).encode()).hexdigest()


@dataclass(frozen=True)
class Diagnostic:
    stage: str
    reason: str
    adapter: str = ""


@dataclass(frozen=True)
class Voice:
    voice: str
    provider: str = "elevenlabs"
    model: str = "eleven_turbo_v2_5"
    rate: float = 0.9
    output_format: str = "mp3_44100_128"
    region: str | None = None
    weight: float = 1
    settings_json: str = "{}"


@dataclass(frozen=True)
class Profile:
    english: Voice = field(default_factory=lambda: Voice("en-US-Neural2-J", "google", "neural2", 1.05, "mp3"))
    voices: tuple[Voice, ...] = ()
    learner_id: str = "liam"
    enabled: bool = True
    model: str = "claude"
    claude_model: str = "claude-haiku-5-5"
    codex_model: str = "gpt-6-luna"
    agy_model: str = "gemini-3-flash"
    timezone: str = "Asia/Ho_Chi_Minh"
    intensity: int = 1
    repeat: bool = True
    repeat_second_voice: bool = True
    pause_s: float = 0.6
    chunk_pause_s: float = 0.4
    repeat_pause_s: float = 1.0
    long_break_seconds: float = 0
    pacing: dict = field(default_factory=dict)
    curriculum_path: str | None = None
    state_dir: str | None = None
    model_timeout_s: float = 5
    provider_timeout_s: float = 60
    compile_timeout_s: float = 15
    render_timeout_s: float = 180
    fallback_reserve_s: float = 15
    config_diagnostics: tuple[str, ...] = ()

    def to_dict(self) -> dict:
        return json.loads(canonical_json(asdict(self)))

    @classmethod
    def from_dict(cls, value: dict) -> Profile:
        data = dict(value)
        if "english" in data:
            data["english"] = Voice(**data["english"])
        if "voices" in data:
            data["voices"] = tuple(Voice(**row) for row in data["voices"])
        if "config_diagnostics" in data:
            data["config_diagnostics"] = tuple(data["config_diagnostics"])
        return cls(**data)


@dataclass(frozen=True)
class Clip:
    text: str
    language: Literal["en", "vi"]
    voice: str
    rate: float
    pause_after_s: float
    provider: str
    model: str
    output_format: str
    display_text: str = ""
    settings_json: str = "{}"
    purpose: str = "source"
    phrase_key: str | None = None
    source_start: int | None = None
    source_end: int | None = None


@dataclass(frozen=True)
class Lesson:
    id: str
    phrase_key: str
    record_json: str
    landed: bool = False

    @property
    def record(self) -> dict:
        return json.loads(self.record_json)


@dataclass(frozen=True)
class Plan:
    identity: str
    source: str
    speech_text: str
    learner_id: str
    curriculum_digest: str
    profile_digest: str
    clips: tuple[Clip, ...]
    fallback_clips: tuple[Clip, ...]
    lessons: tuple[Lesson, ...] = ()
    diagnostics: tuple[Diagnostic, ...] = ()
    status: str = "english"
    profile_json: str = "{}"
    selection_time: str = ""
    executed: bool = False
    schema_version: int = 1
    executed_digest: str | None = None

    def __post_init__(self):
        object.__setattr__(
            self,
            "executed_digest",
            digest([[asdict(clip) for clip in self.clips], [asdict(lesson) for lesson in self.lessons]])
            if self.executed
            else None,
        )

    def to_dict(self) -> dict:
        return asdict(self)

    @classmethod
    def from_dict(cls, value: dict) -> Plan:
        data = dict(value)
        data["clips"] = tuple(Clip(**v) for v in data["clips"])
        data["fallback_clips"] = tuple(Clip(**v) for v in data["fallback_clips"])
        data["lessons"] = tuple(Lesson(**v) for v in data.get("lessons", ()))
        data["diagnostics"] = tuple(Diagnostic(**v) for v in data.get("diagnostics", ()))
        return cls(**data)


@dataclass(frozen=True)
class Section:
    kind: str
    text: str
    collapsible: bool = False


@dataclass(frozen=True)
class Rendering:
    audio_path: Path | None
    artifact_identity: str | None
    plan: Plan
    sections: tuple[Section, ...]
    markdown: str
    diagnostics: tuple[Diagnostic, ...]
    cache_hits: int = 0
    audio_sha256: str | None = None

    @property
    def status(self) -> str:
        return self.plan.status
