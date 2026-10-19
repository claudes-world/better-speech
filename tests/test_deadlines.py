import time
from dataclasses import replace

from better_speech import compile, render
from tests.test_model_free_audio import EmptyLedger, ToneProvider, profile, tone


def test_deadline_hit_during_lesson_yields_english(tmp_path):
    plan = compile("Complete source.", profile(), ledger=EmptyLedger())

    class SlowNative:
        def synthesize(self, clip, *, timeout):
            time.sleep(timeout + 0.01)
            return tone()

    result = render(
        plan, {"google": ToneProvider(), "elevenlabs": SlowNative()}, tmp_path, deadline=time.monotonic() + 4
    )
    assert result.audio_path and result.plan.status == "english", result.diagnostics
    assert not result.plan.lessons and "Vietnamese lesson" not in result.markdown
    assert "".join(c.text for c in result.plan.clips) == plan.speech_text


def test_expired_compile_and_render_return_english_content(tmp_path):
    source = "Complete source."
    plan = compile(source, profile(), deadline=time.monotonic() - 1)
    assert not plan.lessons and plan.source == source and plan.diagnostics
    result = render(plan, {}, tmp_path, deadline=time.monotonic() - 1)
    assert result.audio_path is None and result.plan.status == "text"
    assert source in result.markdown


def test_model_timeout_preserves_reserved_lesson(tmp_path):
    cfg = replace(profile(), model="claude", model_timeout_s=0.02)

    def late(*args, timeout):
        time.sleep(timeout + 0.02)
        return '{"placements":[]}'

    plan = compile(
        "Complete source.", cfg, model_call=late, ledger=EmptyLedger(), deadline=time.monotonic() + 2
    )
    assert plan.lessons
    assert any(d.stage == "model" and d.reason == "TimeoutError" for d in plan.diagnostics)
    # A transport must enforce its own timeout, with the compiler also checking late responses.
