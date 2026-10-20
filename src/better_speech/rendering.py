"""Local rendering with one complete English fallback and no delivery side effects."""

from __future__ import annotations

import hashlib
import math
import tempfile
import time
import unicodedata
from dataclasses import replace
from pathlib import Path

from .assembly import assemble_audio
from .budget import phase_deadline, remaining
from .cache import ClipCache
from .chunking import lower_clip
from .config import cache_dir
from .executor import AudioPart
from .transcript import present
from .types import Diagnostic, Rendering, Section, digest


def render(
    plan, providers, cache=None, *, deadline: float | None = None,
    render_timeout_s: float = 180, provider_timeout_s: float = 60, fallback_reserve_s: float = 15,
) -> Rendering:
    """Provider wrappers own budget/spend; they must honor the supplied timeout.

    Setup, assembly, hashing and presentation share the deadline and fail open.
    Persist the returned executed plan before publishing its caller-owned file.
    """
    started = time.monotonic()
    diagnostics = list(plan.diagnostics)
    english = replace(plan, clips=plan.fallback_clips, lessons=(), status="english", executed=False)
    candidate = plan
    try:
        settings = dict(render_timeout_s=render_timeout_s, provider_timeout_s=provider_timeout_s,
                        fallback_reserve_s=fallback_reserve_s)
        for key, default in (
            ("render_timeout_s", 180),
            ("provider_timeout_s", 60),
            ("fallback_reserve_s", 15),
        ):
            value = settings.get(key, default)
            if (
                type(value) not in (float, int)
                or not math.isfinite(value)
                or value < (0 if key == "fallback_reserve_s" else 1e-9)
            ):
                raise ValueError("invalid render budget")
    except Exception as error:
        settings = {}
        candidate = english
        diagnostics.append(Diagnostic("render_config", type(error).__name__))
    deadline = min(
        deadline if deadline is not None else float("inf"), started + settings.get("render_timeout_s", 180)
    )
    lesson_deadline = phase_deadline(deadline, settings.get("fallback_reserve_s", 15))
    provider_timeout = settings.get("provider_timeout_s", 60)
    try:
        cache = (
            cache if isinstance(cache, ClipCache) else ClipCache(cache if cache is not None else cache_dir())
        )
    except Exception as error:
        diagnostics.append(Diagnostic("cache", "bypassed:" + type(error).__name__))
        cache = None
    hits = 0
    artifact_dir = None
    outputs = []

    def attempt(requested, budget, name):
        nonlocal hits, artifact_dir
        remaining(budget)
        # Present before purchasing clips: a corrupt optional manifest is not audible authority.
        actual = replace(
            requested,
            clips=tuple(
                part for c in requested.clips
                for part in lower_clip(replace(c, text=unicodedata.normalize("NFC", c.text)),
                                       4500 if c.provider == "google" else 12000)
            ),
            executed=True,
        )
        sections, markdown = present(actual)
        remaining(budget)
        if artifact_dir is None:
            artifact_dir = Path(tempfile.mkdtemp(prefix="better-speech-artifact-"))
        output = artifact_dir / name
        outputs.append(output)
        with tempfile.TemporaryDirectory(prefix="better-speech-clips-") as owned:
            scratch = Path(owned)
            parts = []
            for index, clip in enumerate(actual.clips):
                remaining(budget)
                if (clip.language, clip.provider) not in {("en", "google"), ("vi", "elevenlabs")}:
                    raise ValueError("unsupported language/provider pair")

                def synthesize():
                    timeout = min(provider_timeout, remaining(budget))
                    call_deadline = time.monotonic() + timeout
                    audio = providers[clip.provider].synthesize(clip, timeout=timeout)
                    remaining(call_deadline)
                    if not isinstance(audio, bytes) or not audio or len(audio) > 20 * 1024 * 1024:
                        raise ValueError("invalid provider audio")
                    return audio

                audio, hit = (
                    cache.get_or_synthesize(clip, synthesize, deadline=budget, diagnostics=diagnostics)
                    if cache is not None
                    else (synthesize(), False)
                )
                hits += hit
                remaining(budget)
                path = scratch / f"{index}.audio"
                path.write_bytes(audio)
                parts.append(AudioPart("clip", str(path)))
                if clip.pause_after_s:
                    parts.append(AudioPart("silence", seconds=clip.pause_after_s))
            remaining(budget)
            assemble_audio(tuple(parts), scratch_parent=scratch, output=output, deadline=budget)
        remaining(budget)
        checksum = hashlib.sha256()
        with output.open("rb") as audio:
            while chunk := audio.read(1024 * 1024):
                checksum.update(chunk)
                remaining(budget)
        artifact_identity = digest([actual.identity, actual.executed_digest])
        remaining(budget)
        return actual, output, artifact_identity, checksum.hexdigest(), sections, markdown

    result = None
    try:
        result = attempt(candidate, lesson_deadline if candidate.lessons else deadline, "note.ogg")
    except Exception as error:
        diagnostics.append(Diagnostic("render", type(error).__name__))
        if candidate.lessons:
            try:
                result = attempt(english, deadline, "english.ogg")
            except Exception as fallback_error:
                diagnostics.append(Diagnostic("english_render", type(fallback_error).__name__))
    if result is None:
        actual = replace(english, status="text")
        audio_path, artifact_identity, audio_sha256 = None, None, None
        try:
            sections, markdown = present(actual)
        except Exception as error:
            diagnostics.append(Diagnostic("presentation", type(error).__name__))
            sections, markdown = (Section("transcript", plan.source),), plan.source
    else:
        actual, audio_path, artifact_identity, audio_sha256, sections, markdown = result
    # Cleanup failure must not erase a usable rendition or escape as a new refusal.
    for output in outputs:
        if output != audio_path:
            try:
                output.unlink(missing_ok=True)
            except OSError:
                diagnostics.append(Diagnostic("cleanup", "artifact_unavailable"))
    if artifact_dir is not None and audio_path is None:
        try:
            artifact_dir.rmdir()
        except OSError:
            diagnostics.append(Diagnostic("cleanup", "directory_unavailable"))
    actual = replace(actual, diagnostics=tuple(diagnostics))
    return Rendering(
        audio_path, artifact_identity, actual, sections, markdown, tuple(diagnostics), hits, audio_sha256
    )
