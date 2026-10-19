import io
import math
import shutil
import struct
import wave

from better_speech import Profile, Voice, compile, render


class EmptyLedger:
    def snapshot(self, learner_id, **kwargs):
        return {}


def tone(hz=440, seconds=0.1):
    buffer = io.BytesIO()
    with wave.open(buffer, "wb") as stream:
        stream.setparams((1, 2, 24000, 0, "NONE", "not compressed"))
        stream.writeframes(
            b"".join(
                struct.pack("<h", int(5000 * math.sin(i * hz * math.tau / 24000)))
                for i in range(round(seconds * 24000))
            )
        )
    return buffer.getvalue()


class ToneProvider:
    def __init__(self):
        self.calls = []

    def synthesize(self, clip, *, timeout):
        self.calls.append(clip)
        return tone(880 if clip.language == "vi" else 440)


def profile(**kwargs):
    return Profile(
        model="none",
        voices=(Voice("native-central", region="central"), Voice("native-south", region="southern")),
        **kwargs,
    )


def test_bilingual_ogg_with_no_model_executable(tmp_path, monkeypatch):
    tools = tmp_path / "bin"
    tools.mkdir()
    (tools / "ffmpeg").symlink_to(shutil.which("ffmpeg"))
    monkeypatch.setenv("PATH", str(tools))
    assert all(shutil.which(name) is None for name in ("claude", "codex", "agy"))
    plan = compile("# Status\n\nThe update is ready.", profile(), ledger=EmptyLedger())
    assert plan.status == "bilingual", plan.diagnostics
    assert {c.language for c in plan.clips} == {"en", "vi"}
    assert all(c.provider == "elevenlabs" for c in plan.clips if c.language == "vi")
    provider = ToneProvider()
    result = render(plan, {"google": provider, "elevenlabs": provider}, tmp_path / "cache")
    assert result.audio_path and result.audio_path.read_bytes().startswith(b"OggS"), result.diagnostics
    assert result.plan.executed and result.plan.lessons
    assert [s.kind for s in result.sections] == ["title", "lesson", "transcript"]
    assert "<summary>Vietnamese lesson</summary>" in result.markdown
    assert "1. **" in result.markdown
    first_count = len(provider.calls)
    again = render(plan, {"google": provider, "elevenlabs": provider}, tmp_path / "cache")
    assert again.audio_path
    assert len(provider.calls) == first_count
    assert again.cache_hits == len(plan.clips)


def test_missing_native_provider_yields_complete_english(tmp_path):
    plan = compile("First paragraph.\n\nSecond paragraph.", profile(), ledger=EmptyLedger())
    result = render(plan, {"google": ToneProvider()}, tmp_path / "cache")
    assert result.audio_path and result.plan.status == "english"
    assert not result.plan.lessons
    assert "Vietnamese lesson" not in result.markdown
    assert "".join(c.text for c in result.plan.clips) == plan.speech_text
