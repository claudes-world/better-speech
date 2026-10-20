"""Cheap local audio evidence; no provider, network, custody or live service.

These execute the production assembly program. The rate fallback test injects
local callbacks, not a real provider adapter or its error classifier.
"""

import asyncio
import json
import math
import shutil
import struct
import subprocess
import wave
from dataclasses import replace
from pathlib import Path

import pytest

from better_speech.executor import ffmpeg_assembly_program
from tests.reference.planner import SpeechStep
from tests.reference.executor import (
    AudioPart,
    CompletedClip,
    NativeRateRejected,
    render_with_rate_fallback,
)

pytestmark = pytest.mark.skipif(
    not shutil.which("ffmpeg") or not shutil.which("ffprobe"),
    reason="local audio integration requires installed ffmpeg and ffprobe",
)


def run(*args):
    return subprocess.run(args, check=True, capture_output=True, timeout=10).stdout


def tone(path, *, hz, seconds=0.2, sample_rate=22050, channels=2):
    frames = b"".join(
        struct.pack("<h", round(6000 * math.sin(2 * math.pi * hz * i / sample_rate))) * channels
        for i in range(round(seconds * sample_rate))
    )
    with wave.open(str(path), "wb") as audio:
        audio.setnchannels(channels)
        audio.setsampwidth(2)
        audio.setframerate(sample_rate)
        audio.writeframes(frames)


def probe(path):
    return json.loads(
        run("ffprobe", "-v", "error", "-show_streams", "-show_format", "-of", "json", str(path))
    )


def pcm(path):
    decoded = run(
        "ffmpeg", "-v", "error", "-i", str(path), "-f", "s16le", "-ar", "48000", "-ac", "1", "pipe:1"
    )
    return [value[0] for value in struct.iter_unpack("<h", decoded)]


def window(samples, start, end):
    return samples[round(start * 48000) : round(end * 48000)]


def rms(samples):
    return math.sqrt(sum(x * x for x in samples) / len(samples))


def assert_tone(samples, start, end, hz):
    part = window(samples, start, end)
    assert rms(part) > 500
    positive_crossings = sum(a <= 0 < b for a, b in zip(part, part[1:]))
    measured_hz = positive_crossings / ((len(part) - 1) / 48000)
    assert measured_hz == pytest.approx(hz, abs=15)


def test_real_ffmpeg_mixed_order_silence_reuse_and_mono_48k_opus(tmp_path):
    # Quoted/spaced paths also exercise the real concat manifest escaping.
    scratch = tmp_path / "owned scratch's audio"
    scratch.mkdir()
    low, high = tmp_path / "low.wav", tmp_path / "high.wav"
    tone(low, hz=440)
    tone(high, hz=880, sample_rate=32000, channels=1)
    output = tmp_path / "complete.ogg"
    program = ffmpeg_assembly_program(
        (
            AudioPart("clip", str(low)),
            AudioPart("silence", seconds=0.12),
            AudioPart("clip", str(high)),
            AudioPart("silence", seconds=0.12),
            AudioPart("clip", str(low)),
        ),
        scratch=scratch,
        output=output,
    )
    Path(program.manifest_path).write_text(program.manifest_text)
    for command in program.commands:
        run(*command)
    metadata = probe(output)
    assert len(metadata["streams"]) == 1
    stream = metadata["streams"][0]
    assert stream["codec_name"] == "opus"
    assert stream["sample_rate"] == "48000" and stream["channels"] == 1
    assert float(metadata["format"]["duration"]) == pytest.approx(0.84, abs=0.03)
    samples = pcm(output)
    assert len(samples) / 48000 == pytest.approx(0.84, abs=0.02)
    assert_tone(samples, 0.04, 0.14, 440)
    assert_tone(samples, 0.36, 0.46, 880)
    assert_tone(samples, 0.68, 0.78, 440)
    # Lossless concat must contain exact silence. Opus can retain a small
    # decaying tail/DC offset; require it below 2% of adjacent tone amplitude.
    joined = pcm(scratch / "joined.wav")
    for start, end in ((0.24, 0.29), (0.56, 0.61)):
        assert rms(window(joined, start, end)) == 0
        assert rms(window(samples, start, end)) < 0.02 * rms(window(samples, 0.04, 0.14))


def test_definitive_native_rate_fallback_applies_real_atempo(tmp_path):
    source, slowed = tmp_path / "source.wav", tmp_path / "slowed.wav"
    tone(source, hz=440, seconds=0.4, sample_rate=48000, channels=1)
    step = SpeechStep(
        "tts",
        text="test",
        display_text="test",
        provider="local-test",
        model="tone",
        voice="440Hz",
        rate=0.5,
        audio_format="wav",
    )
    rates = []

    async def call(rate):
        rates.append(rate)
        if rate is not None:
            raise NativeRateRejected("synthetic definitive rejection")
        return CompletedClip(str(source), replace(step, rate=1.0))

    async def atempo(clip, rate):
        run("ffmpeg", "-v", "error", "-i", clip.reference, "-filter:a", f"atempo={rate}", "-y", str(slowed))
        return CompletedClip(str(slowed), replace(clip.step, rate=rate))

    clip = asyncio.run(render_with_rate_fallback(0.5, call=call, atempo=atempo))
    assert rates == [0.5, None]
    assert clip.step.rate == 0.5 and clip.reference == str(slowed)
    samples = pcm(slowed)
    # atempo has finite-window end effects; duration doubles approximately,
    # while pitch remains unchanged (unlike simply lowering sample rate).
    assert len(samples) / 48000 == pytest.approx(0.8, abs=0.06)
    assert_tone(samples, 0.15, 0.35, 440)
