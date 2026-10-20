"""Injected utterance executor; provider/cache/delivery authority stays outside.

Render callbacks must finish their fenced custody transition before returning a
completed clip. They own deadlines, budget resolution and cache accounting.
This module never retries an uncertain/retryable call, sends audio or records
exposure. Assembly is local only; publication remains an independent fence.
"""

from __future__ import annotations

import asyncio
import math
from collections.abc import Awaitable, Callable
from dataclasses import dataclass, replace

from better_speech.executor import AudioPart
from tests.reference.planner import SpeechPlan, SpeechStep


@dataclass(frozen=True)
class CompletedClip:
    reference: str
    step: SpeechStep  # effective provider input after custody-owned resolution


@dataclass(frozen=True)
class Failure:
    phase: str
    index: int
    disposition: str
    code: str


@dataclass(frozen=True)
class ExecutionResult:
    status: str  # rendered | monolingual | text | held
    source: str
    audio_reference: str | None = None
    executed_plan: SpeechPlan | None = None
    failures: tuple[Failure, ...] = ()


Render = Callable[[SpeechStep, int, str], Awaitable[CompletedClip]]
Assemble = Callable[[tuple[AudioPart, ...], str], Awaitable[str]]


def _failure(error: BaseException, phase: str, index: int) -> Failure:
    disposition = getattr(error, "disposition", "uncertain")
    if disposition not in ("terminal", "retryable", "uncertain"):
        disposition = "uncertain"
    return Failure(
        phase, index, disposition, str(getattr(error, "error_code", "unclassified_render_failure"))
    )


async def _attempt(
    plan: SpeechPlan, phase: str, render: Render, assemble: Assemble, concurrency: int
) -> tuple[str | None, SpeechPlan | None, tuple[Failure, ...]]:
    if not plan.steps:
        return None, None, (Failure(phase, -1, "terminal", "empty_plan"),)
    semaphore = asyncio.Semaphore(concurrency)
    cancelled = False

    async def one(index, step):
        async with semaphore:
            if cancelled:
                raise asyncio.CancelledError()
            clip = await render(step, index, phase)
            if (
                not isinstance(clip, CompletedClip)
                or not clip.reference
                or not isinstance(clip.step, SpeechStep)
            ):
                raise RuntimeError("render did not return a completed custody reference")
            return clip

    indexed = [(i, step) for i, step in enumerate(plan.steps) if step.kind == "tts"]
    tasks = [asyncio.create_task(one(i, step)) for i, step in indexed]
    settled = asyncio.gather(*tasks, return_exceptions=True)
    try:
        results = await asyncio.shield(settled)
    except asyncio.CancelledError:
        cancelled = True
        # Do not release scratch while an accepted provider call is writing.
        # Callback deadlines bound this drain; cancellation is not resubmission.
        while not settled.done():
            try:
                await asyncio.shield(settled)
            except asyncio.CancelledError:
                continue
        raise
    failures = tuple(
        _failure(value, phase, indexed[n][0])
        for n, value in enumerate(results)
        if isinstance(value, BaseException)
    )
    if failures:
        return None, None, failures
    clips = {indexed[n][0]: value for n, value in enumerate(results)}
    parts = tuple(
        AudioPart("clip", clips[i].reference)
        if step.kind == "tts"
        else AudioPart("silence", seconds=step.seconds)
        for i, step in enumerate(plan.steps)
    )
    # Actual resolved voices/text are evidence; source spans/lesson identity are
    # still the original immutable plan's metadata, not callback-authored prose.
    fields = (
        "provider",
        "model",
        "voice",
        "rate",
        "text",
        "input_mode",
        "audio_format",
        "voice_settings_json",
    )
    effective = replace(
        plan,
        steps=tuple(
            replace(step, **{key: getattr(clips[i].step, key) for key in fields})
            if step.kind == "tts"
            else step
            for i, step in enumerate(plan.steps)
        ),
    )
    try:
        reference = await assemble(parts, phase)
        if not isinstance(reference, str) or not reference:
            raise ValueError("local assembly returned no artifact")
        return reference, effective, ()
    except asyncio.CancelledError:
        raise
    except Exception:
        # Assembly callback is local-only, never a send/provider operation.
        return None, None, (Failure(phase, -1, "terminal", "local_assembly_failed"),)


async def execute_plan(
    plan: SpeechPlan,
    *,
    fallback_plan: SpeechPlan,
    render: Render,
    assemble: Assemble,
    cleanup: Callable[[], Awaitable[None]],
    concurrency: int = 4,
) -> ExecutionResult:
    """One enriched attempt; at most one complete monolingual fallback.

    Caller supplies a source-only fallback retaining valid source pronunciation
    annotations. Callback indices are stable within phase; use phase plus the
    persisted plan identity in existing rendition custody, not random IDs.
    Cleanup owns scratch only, never completed persistent clip/final artifacts.
    """
    if type(concurrency) is not int or concurrency < 1:
        raise ValueError("concurrency must be a positive integer")
    if (
        fallback_plan.source != plan.source
        or any(s.purpose != "source" for s in fallback_plan.steps)
        or "".join(s.display_text for s in fallback_plan.steps) != plan.source
    ):
        raise ValueError("fallback must cover complete original source without lessons")

    async def run():
        audio, actual, failures = await _attempt(plan, "primary", render, assemble, concurrency)
        if audio is not None:
            return ExecutionResult("rendered", plan.source, audio, actual)
        if any(f.disposition != "terminal" for f in failures):
            return ExecutionResult("held", plan.source, failures=failures)
        if plan == fallback_plan:
            return ExecutionResult("text", plan.source, failures=failures)
        audio, actual, more = await _attempt(fallback_plan, "monolingual", render, assemble, concurrency)
        failures += more
        if audio is not None:
            return ExecutionResult("monolingual", plan.source, audio, actual, failures)
        return ExecutionResult(
            "held" if any(f.disposition != "terminal" for f in more) else "text",
            plan.source,
            failures=failures,
        )

    cleanup_failed = False
    try:
        result = await run()
    finally:
        try:
            await cleanup()
        except Exception:
            # Do not replace an uncertain provider receipt with a scratch error
            # that might subsequently be mistaken for retryable synthesis.
            cleanup_failed = True
    if cleanup_failed:
        result = replace(
            result, failures=result.failures + (Failure("cleanup", -1, "terminal", "scratch_cleanup_failed"),)
        )
    return result


class NativeRateRejected(RuntimeError):
    """Adapter-certified definitive native-rate rejection, no accepted render.

    Not a generic HTTP 4xx/network failure. Custody owner must classify it.
    """


async def render_with_rate_fallback(
    rate: float,
    *,
    call: Callable[[float | None], Awaitable[CompletedClip]],
    atempo: Callable[[CompletedClip, float], Awaitable[CompletedClip]],
) -> CompletedClip:
    """Narrow native-rate fallback; all other failures preserve their outcome."""
    if type(rate) not in (int, float) or not math.isfinite(rate) or rate <= 0:
        raise ValueError("rate must be positive and finite")
    try:
        return await call(rate)
    except NativeRateRejected:
        flat = await call(None)
        return await atempo(flat, rate)
