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
from .pronunciation import speech_input
from .providers.elevenlabs import ElevenLabsProvider
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
    pronunciation_failure = False

    def attempt(requested, budget, name):
        nonlocal hits, artifact_dir, pronunciation_failure
        pronunciation_failure = False
        remaining(budget)
        # Present before purchasing clips: a corrupt optional manifest is not audible authority.
        lowered = []
        for clip in requested.clips:
            normalized = unicodedata.normalize("NFC", clip.text)
            if clip.pronunciations:
                try:
                    if normalized != clip.text:
                        raise ValueError('pronunciation normalization changed offsets')
                    speech_input(clip)
                except Exception:
                    diagnostics.append(Diagnostic('heteronym', 'invalid_pronunciations_plain_fallback'))
                    clip = replace(clip, pronunciations=[])
            clip = replace(clip, text=normalized)
            if not clip.text.strip():
                lowered.append(replace(clip, pause_after_s=clip.pause_after_s + (0 if requested.executed else 0.25)))
                continue
            provider = providers[clip.provider]
            if clip.language not in provider.languages or clip.output_format not in provider.formats:
                raise ValueError("provider does not support clip language/format")
            minimum_rate = getattr(provider, "minimum_rate",
                                   ElevenLabsProvider.minimum_rate if clip.provider == "elevenlabs" else 0)
            if clip.echo and clip.rate < minimum_rate:
                clip = replace(clip, rate=minimum_rate)
                diagnostics.append(Diagnostic("voice", "echo_rate_clamped_to_provider_minimum"))
            parts = tuple(lower_clip(clip, provider.max_request_bytes))
            if sum(len(c.pronunciations) for c in parts) != len(clip.pronunciations):
                diagnostics.append(Diagnostic('heteronym', 'provider_limit_plain_fallback'))
                parts = tuple(lower_clip(replace(clip, pronunciations=[]), provider.max_request_bytes))
            lowered.extend(parts)
        actual = replace(requested, clips=tuple(lowered), executed=True)
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
                if not clip.text.strip():
                    parts.append(AudioPart("silence", seconds=clip.pause_after_s))
                    continue

                def synthesize():
                    timeout = min(provider_timeout, remaining(budget))
                    call_deadline = time.monotonic() + timeout
                    audio = providers[clip.provider].synthesize(clip, timeout=timeout)
                    remaining(call_deadline)
                    if not isinstance(audio, bytes) or not audio or len(audio) > 20 * 1024 * 1024:
                        raise ValueError("invalid provider audio")
                    return audio

                pronunciation_failure = bool(clip.pronunciations)
                audio, hit = (
                    cache.get_or_synthesize(clip, synthesize, deadline=budget, diagnostics=diagnostics)
                    if cache is not None
                    else (synthesize(), False)
                )
                pronunciation_failure = False
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
        result = attempt(candidate, lesson_deadline if candidate.lessons or any(c.pronunciations for c in candidate.clips) else deadline, "note.ogg")
    except Exception as error:
        diagnostics.append(Diagnostic("render", type(error).__name__))
        if pronunciation_failure:
            diagnostics.append(Diagnostic("heteronym", "provider_error_plain_fallback"))
            candidate = replace(candidate, clips=tuple(replace(c, pronunciations=[]) for c in candidate.clips))
            try:
                result = attempt(candidate, lesson_deadline if candidate.lessons else deadline, "plain.ogg")
            except Exception as plain_error:
                diagnostics.append(Diagnostic("plain_render", type(plain_error).__name__))
        if result is None and (candidate.lessons or any(c.pronunciations for c in candidate.clips)):
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
