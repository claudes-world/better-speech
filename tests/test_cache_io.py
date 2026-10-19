import multiprocessing
import time

from filelock import FileLock

from better_speech import compile, render
from better_speech.cache import ClipCache
from tests.test_model_free_audio import EmptyLedger, ToneProvider, profile, tone


def cache_worker(directory, clip, counter, barrier):
    barrier.wait(10)

    def purchase():
        with counter.get_lock():
            counter.value += 1
        time.sleep(0.15)
        return tone()

    result, _ = ClipCache(directory).get_or_synthesize(
        clip, purchase, deadline=time.monotonic() + 10, diagnostics=[]
    )
    assert result == tone()


def test_two_process_simultaneous_misses_purchase_once(tmp_path):
    context = multiprocessing.get_context("spawn")
    counter, barrier = context.Value("i", 0), context.Barrier(2)
    clip = compile("Source.", profile(), ledger=EmptyLedger()).clips[0]
    processes = [
        context.Process(target=cache_worker, args=(tmp_path, clip, counter, barrier)) for _ in range(2)
    ]
    for process in processes:
        process.start()
    for process in processes:
        process.join(15)
        assert process.exitcode == 0
    assert counter.value == 1
    assert not list(tmp_path.glob(".clip-*"))


def test_unwritable_cache_bypassed_and_bilingual_render_succeeds(tmp_path):
    directory = tmp_path / "readonly"
    directory.mkdir(mode=0o500)
    plan = compile("Source.", profile(), ledger=EmptyLedger())
    provider = ToneProvider()
    try:
        result = render(plan, {"google": provider, "elevenlabs": provider}, directory)
        assert result.audio_path and result.plan.lessons, result.diagnostics
        assert any(d.stage == "cache" for d in result.diagnostics)
        assert provider.calls
    finally:
        directory.chmod(0o700)


def test_corrupt_cache_replaced_then_hit_calls_no_provider(tmp_path):
    clip = compile("Source.", profile(), ledger=EmptyLedger()).clips[0]
    cache = ClipCache(tmp_path)
    first, hit = cache.get_or_synthesize(clip, tone, deadline=time.monotonic() + 5, diagnostics=[])
    assert not hit
    next(tmp_path.glob("*.clip")).write_bytes(b"corrupt")
    second, hit = cache.get_or_synthesize(clip, tone, deadline=time.monotonic() + 5, diagnostics=[])
    assert first == second and not hit

    def unexpected_purchase():
        raise AssertionError("cache hit purchased audio")

    cached, hit = cache.get_or_synthesize(
        clip, unexpected_purchase, deadline=time.monotonic() + 5, diagnostics=[]
    )
    assert hit and cached == second


def test_bounded_lock_timeout_bypasses_cache(tmp_path):
    clip = compile("Source.", profile(), ledger=EmptyLedger()).clips[0]
    cache = ClipCache(tmp_path, lock_timeout=0.02)
    # FileLock objects are independently owned, even in one process.
    from better_speech.cache import clip_identity

    identity = clip_identity(
        provider=clip.provider,
        model=clip.model,
        voice=clip.voice,
        rate=clip.rate,
        text=clip.text,
        response_format=clip.output_format,
        voice_settings={},
    )
    diagnostics = []
    with FileLock(str(tmp_path / (identity.cache_key + ".clip.lock"))):
        start = time.monotonic()
        audio, hit = cache.get_or_synthesize(clip, tone, deadline=start + 2, diagnostics=diagnostics)
    assert not hit and audio and diagnostics
    assert time.monotonic() - start < 1


def test_provider_failure_is_not_misclassified_as_cache_io(tmp_path):
    import pytest

    clip = compile("Source.", profile(), ledger=EmptyLedger()).clips[0]
    calls = []

    def failed_attempt():
        calls.append(True)
        raise PermissionError("host budget denied")

    with pytest.raises(PermissionError):
        ClipCache(tmp_path).get_or_synthesize(
            clip, failed_attempt, deadline=time.monotonic() + 5, diagnostics=[]
        )
    assert calls == [True]
    assert not list(tmp_path.glob("*.clip"))


def test_unavailable_cache_path_is_bypassed_without_repeating_purchase(tmp_path):
    directory = tmp_path / "occupied"
    directory.write_text("not a directory")
    provider = ToneProvider()
    plan = compile("Source.", profile(), ledger=EmptyLedger())
    result = render(plan, {"google": provider, "elevenlabs": provider}, directory)
    assert result.audio_path and result.status == "bilingual", result.diagnostics
    assert len(provider.calls) == len(plan.clips)
    assert any(d.stage == "cache" for d in result.diagnostics)
