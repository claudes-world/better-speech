import asyncio
import json
from dataclasses import replace
from pathlib import Path

import pytest

from better_speech.executor import ffmpeg_assembly_program
from tests.reference.planner import SpeechPlan, SpeechStep
from tests.reference.executor import (
    AudioPart,
    CompletedClip,
    NativeRateRejected,
    execute_plan,
    render_with_rate_fallback,
)

FIXTURE = json.loads((Path(__file__).parent / "fixtures/enrichment/executor.json").read_text())


def step(text, purpose="source"):
    return SpeechStep(
        "tts",
        text=text,
        display_text=text,
        provider="test",
        model="test-model",
        voice="main",
        rate=0.9,
        purpose=purpose,
        lesson_id="lesson-1" if purpose == "lesson" else "",
    )


SOURCE = "one two three"
PLAIN = SpeechPlan(SOURCE, (step(SOURCE),), "{}", "plain")
ENRICHED = SpeechPlan(
    SOURCE,
    (step("one "), SpeechStep("silence", seconds=0.05), step("Vietnamese", "lesson"), step("two three")),
    "{}",
    "applied",
)


class ProviderFailure(Exception):
    def __init__(self, disposition):
        self.disposition = disposition
        self.error_code = "fake_" + disposition


def test_all_inflight_reverse_completion_still_ordered_and_actual_resolution():
    async def run():
        started, gates, assembled, cleaned = [], [], [], []

        async def render(s, index, phase):
            started.append(index)
            gate = asyncio.Event()
            gates.append(gate)
            await gate.wait()
            return CompletedClip(str(index), replace(s, voice="effective-budget-voice"))

        async def assemble(parts, phase):
            assembled.extend(parts)
            return "final"

        async def cleanup():
            cleaned.append(True)

        task = asyncio.create_task(
            execute_plan(ENRICHED, fallback_plan=PLAIN, render=render, assemble=assemble, cleanup=cleanup)
        )
        for _ in range(10):
            await asyncio.sleep(0)
            if len(gates) == 3:
                break
        assert started == FIXTURE["concurrent"]["started_indices"]
        for gate in reversed(gates):
            gate.set()
            await asyncio.sleep(0)
        result = await task
        assert [p.reference if p.kind == "clip" else "silence" for p in assembled] == FIXTURE["concurrent"][
            "assembly"
        ]
        assert result.status == "rendered" and cleaned == [True]
        assert all(s.voice == "effective-budget-voice" for s in result.executed_plan.steps if s.kind == "tts")
        assert result.executed_plan.steps[2].lesson_id == "lesson-1"

    asyncio.run(run())


@pytest.mark.parametrize("case", FIXTURE["failures"], ids=lambda c: c["disposition"])
def test_failure_waits_for_siblings_then_only_known_terminal_falls_back(case):
    async def run():
        events = []

        async def render(s, index, phase):
            if phase == "primary" and s.purpose == "lesson":
                events.append("failed")
                raise ProviderFailure(case["disposition"])
            if phase == "primary":
                await asyncio.sleep(0.001)
                events.append("settled")
            else:
                assert events.count("settled") == 2
                events.append("fallback")
            return CompletedClip(s.text, s)

        async def assemble(parts, phase):
            events.append("assembled:" + phase)
            return "whole-source"

        async def cleanup():
            assert events.count("settled") == 2
            events.append("cleanup")

        result = await execute_plan(
            ENRICHED, fallback_plan=PLAIN, render=render, assemble=assemble, cleanup=cleanup
        )
        assert result.status == case["status"]
        assert events[-1] == "cleanup"
        assert events.count("fallback") == case["fallback_calls"]
        assert result.failures[0].index == 2
        assert result.failures[0].disposition == case["disposition"]
        if result.executed_plan:
            assert result.executed_plan == PLAIN
            assert not any(s.lesson_id for s in result.executed_plan.steps)
        else:
            assert result.audio_reference is None

    asyncio.run(run())


@pytest.mark.parametrize(
    "fallback_disposition,status", [("terminal", "text"), ("uncertain", "held"), ("retryable", "held")]
)
def test_total_failure_never_returns_phantom_lesson(fallback_disposition, status):
    async def run():
        calls = []

        async def render(s, index, phase):
            calls.append(phase)
            raise ProviderFailure("terminal" if phase == "primary" else fallback_disposition)

        async def assemble(parts, phase):
            pytest.fail("failed clips cannot assemble")

        async def cleanup():
            pass

        result = await execute_plan(
            ENRICHED, fallback_plan=PLAIN, render=render, assemble=assemble, cleanup=cleanup
        )
        assert result.status == status and result.source == SOURCE
        assert result.executed_plan is None and result.audio_reference is None
        assert calls.count("monolingual") == 1
        assert len(result.failures) == 4

    asyncio.run(run())


def test_cancellation_drains_all_inflight_before_cleanup_and_no_fallback():
    async def run():
        started = asyncio.Event()
        release = asyncio.Event()
        events = []

        async def render(s, index, phase):
            started.set()
            await release.wait()
            events.append("settled")
            return CompletedClip("clip", s)

        async def assemble(parts, phase):
            pytest.fail("cancelled plan cannot assemble")

        async def cleanup():
            events.append("cleanup")

        task = asyncio.create_task(
            execute_plan(PLAIN, fallback_plan=PLAIN, render=render, assemble=assemble, cleanup=cleanup)
        )
        await started.wait()
        task.cancel()
        await asyncio.sleep(0)
        assert not task.done() and events == []
        task.cancel()
        await asyncio.sleep(0)
        assert not task.done() and events == []
        release.set()
        with pytest.raises(asyncio.CancelledError):
            await task
        assert events == ["settled", "cleanup"]

    asyncio.run(run())


def test_cancellation_never_starts_semaphore_queued_provider_calls():
    async def run():
        started, cleaned = [], []
        first = asyncio.Event()
        release = asyncio.Event()

        async def render(s, index, phase):
            started.append(index)
            first.set()
            await release.wait()
            return CompletedClip(str(index), s)

        async def assemble(*args):
            pytest.fail("cancelled plan cannot assemble")

        async def cleanup():
            cleaned.append(True)

        task = asyncio.create_task(
            execute_plan(
                ENRICHED,
                fallback_plan=PLAIN,
                render=render,
                assemble=assemble,
                cleanup=cleanup,
                concurrency=1,
            )
        )
        await first.wait()
        task.cancel()
        await asyncio.sleep(0)
        assert not cleaned
        release.set()
        with pytest.raises(asyncio.CancelledError):
            await task
        assert started == [0] and cleaned == [True]

    asyncio.run(run())


def test_local_assembly_failure_falls_back_whole_once():
    async def run():
        phases = []

        async def render(s, index, phase):
            return CompletedClip(str(index), s)

        async def assemble(parts, phase):
            phases.append(phase)
            if phase == "primary":
                raise OSError("local decode failed")
            return "plain"

        async def cleanup():
            pass

        result = await execute_plan(
            ENRICHED, fallback_plan=PLAIN, render=render, assemble=assemble, cleanup=cleanup
        )
        assert result.status == "monolingual" and result.executed_plan == PLAIN
        assert phases == ["primary", "monolingual"]
        assert result.failures[0].code == "local_assembly_failed"

    asyncio.run(run())


@pytest.mark.parametrize(
    "error", [RuntimeError("network"), ProviderFailure("uncertain"), ProviderFailure("retryable")]
)
def test_rate_fallback_does_not_retry_unclassified_or_uncertain_error(error):
    async def run():
        calls = []

        async def call(rate):
            calls.append(rate)
            raise error

        async def atempo(clip, rate):
            pytest.fail("must not transform")

        with pytest.raises(type(error)):
            await render_with_rate_fallback(0.8, call=call, atempo=atempo)
        assert calls == [0.8]

    asyncio.run(run())


def test_native_rejection_flat_then_atempo_preserves_requested_rate():
    async def run():
        trace = []

        async def call(rate):
            trace.append(["call", rate])
            if rate is not None:
                raise NativeRateRejected()
            return CompletedClip("flat", step("text"))

        async def atempo(clip, rate):
            trace.append(["atempo", rate])
            return replace(clip, reference="paced", step=replace(clip.step, rate=rate))

        result = await render_with_rate_fallback(0.8, call=call, atempo=atempo)
        assert trace == FIXTURE["rate_rejection_trace"]
        assert result.reference == "paced" and result.step.rate == 0.8

    asyncio.run(run())


def test_ffmpeg_normalizes_and_assembles_ordered_opus_with_loudness():
    program = ffmpeg_assembly_program(
        (
            AudioPart("clip", "/input/a"),
            AudioPart("silence", seconds=0.05),
            AudioPart("clip", "/input/b"),
            AudioPart("silence", seconds=0.05),
        ),
        scratch=Path("/scratch/job"),
        output=Path("/output/final.ogg"),
    )
    # Preserve the V1 command fixture; the deliberate V2 delta is deterministic
    # mux/codec output for immutable derivative cache rehydration.
    expected = [list(command) for command in FIXTURE["ffmpeg_commands"]]
    codec_index = expected[-1].index("-c:a")
    expected[-1][codec_index:codec_index] = ["-fflags", "+bitexact", "-flags:a", "+bitexact"]
    assert [list(c) for c in program.commands] == expected
    assert program.manifest_text == FIXTURE["manifest"]


def test_unknown_callback_result_is_held_not_resynthesized():
    async def run():
        async def render(s, index, phase):
            return None

        async def assemble(parts, phase):
            pytest.fail("unknown clip is not assembled")

        async def cleanup():
            pass

        result = await execute_plan(
            PLAIN, fallback_plan=PLAIN, render=render, assemble=assemble, cleanup=cleanup
        )
        assert result.status == "held"

    asyncio.run(run())


def test_configured_concurrency_bound_and_no_plain_retry_after_terminal_failure():
    async def run():
        active, peak = 0, 0

        async def render(s, index, phase):
            nonlocal active, peak
            active += 1
            peak = max(peak, active)
            await asyncio.sleep(0)
            active -= 1
            raise ProviderFailure("terminal")

        async def assemble(parts, phase):
            pytest.fail("terminal clips must not assemble")

        async def cleanup():
            assert active == 0

        plan = replace(PLAIN, steps=tuple(step(c) for c in SOURCE))
        result = await execute_plan(
            plan, fallback_plan=plan, render=render, assemble=assemble, cleanup=cleanup, concurrency=2
        )
        assert result.status == "text" and peak == 2
        assert len(result.failures) == len(SOURCE)
        assert {f.phase for f in result.failures} == {"primary"}

    asyncio.run(run())


def test_empty_plan_returns_text_after_cleanup():
    async def run():
        plan = SpeechPlan("", (), "{}", "plain")
        cleaned = []

        async def render(*args):
            pytest.fail("empty plan must not render")

        async def assemble(*args):
            pytest.fail("empty plan must not assemble")

        async def cleanup():
            cleaned.append(True)

        result = await execute_plan(
            plan, fallback_plan=plan, render=render, assemble=assemble, cleanup=cleanup
        )
        assert result.status == "text" and result.failures[0].code == "empty_plan"
        assert cleaned == [True]

    asyncio.run(run())


@pytest.mark.parametrize("parts", [(), (AudioPart("silence", seconds=float("nan")),), (AudioPart("other"),)])
def test_invalid_local_audio_program_rejected(parts):
    with pytest.raises(ValueError):
        ffmpeg_assembly_program(parts, scratch=Path("/scratch"), output=Path("/out"))


def test_cleanup_error_never_erases_uncertain_provider_receipt():
    async def run():
        async def render(*args):
            raise ProviderFailure("uncertain")

        async def assemble(*args):
            pytest.fail("uncertain cannot assemble")

        async def cleanup():
            raise OSError("scratch removal failed")

        result = await execute_plan(
            PLAIN, fallback_plan=PLAIN, render=render, assemble=assemble, cleanup=cleanup
        )
        assert result.status == "held"
        assert result.failures[0].disposition == "uncertain"
        assert result.failures[1].code == "scratch_cleanup_failed"

    asyncio.run(run())
