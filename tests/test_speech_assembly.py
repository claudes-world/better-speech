"""Local process/filesystem adapter evidence, not provider or DB acceptance."""

import asyncio
import shutil
import sys
import time
import wave
from pathlib import Path

import pytest

from better_speech import assembly as assembly
from better_speech.executor import AudioPart


def test_real_audio_runner_leaves_only_complete_output(tmp_path):
    if not shutil.which("ffmpeg"):
        pytest.skip("ffmpeg required for local audio evidence")
    source, output = tmp_path / "source.wav", tmp_path / "complete.ogg"
    with wave.open(str(source), "wb") as audio:
        audio.setnchannels(1)
        audio.setsampwidth(2)
        audio.setframerate(48000)
        audio.writeframes(b"\0\0" * 4800)
    result = assembly.assemble_audio(
            (AudioPart("clip", str(source)), AudioPart("silence", seconds=0.1)),
            scratch_parent=tmp_path,
            output=output,
            deadline=time.monotonic() + 10,
        )
    assert result == str(output)
    assert output.read_bytes().startswith(b"OggS")
    assert sorted(p.name for p in tmp_path.iterdir()) == ["complete.ogg", "source.wav"]


def test_repeated_real_assembly_has_identical_bytes(tmp_path):
    """Retention rehydration must preserve immutable derivative checksums."""
    if not shutil.which("ffmpeg"):
        pytest.skip("ffmpeg required for local audio evidence")
    source = tmp_path / "source.wav"
    with wave.open(str(source), "wb") as audio:
        audio.setnchannels(1)
        audio.setsampwidth(2)
        audio.setframerate(48000)
        audio.writeframes(
            b"".join(
                (6000 if index % 100 < 50 else -6000).to_bytes(2, "little", signed=True)
                for index in range(4800)
            )
        )
    results = []
    for index in range(2):
        output = tmp_path / f"complete-{index}.ogg"
        assembly.assemble_audio(
                (AudioPart("clip", str(source)), AudioPart("silence", seconds=0.1)),
                scratch_parent=tmp_path,
                output=output,
                deadline=time.monotonic() + 10,
            )
        results.append(output.read_bytes())
    assert results[0] == results[1]


@pytest.mark.parametrize("failure", ["process", "cancel", "deadline", "empty", "exists"])
def test_no_partial_publication_and_no_overwrite(tmp_path, monkeypatch, failure):
    output = tmp_path / "output.ogg"
    if failure == "exists":
        output.write_bytes(b"existing artifact")
    now = [10.0]

    def run(command, deadline, clock):
        if failure == "process":
            raise assembly.AssemblyError("failed")
        if failure == "cancel":
            raise asyncio.CancelledError()
        # Only the last ffmpeg command writes the staged Opus file.
        if "libopus" in command and failure != "empty":
            Path(command[command.index("libopus") + 1]).write_bytes(b"complete")
        if failure == "deadline":
            now[0] = 100.0

    monkeypatch.setattr(assembly, "_run", run)
    expected = {
        "process": assembly.AssemblyError,
        "cancel": asyncio.CancelledError,
        "deadline": TimeoutError,
        "empty": assembly.AssemblyError,
        "exists": FileExistsError,
    }[failure]
    with pytest.raises(expected):
        assembly.assemble_audio(
                (AudioPart("clip", "owned.wav"),),
                scratch_parent=tmp_path,
                output=output,
                deadline=20.0,
                clock=lambda: now[0],
            )
    assert sorted(p.name for p in tmp_path.iterdir()) == (["output.ogg"] if failure == "exists" else [])
    if failure == "exists":
        assert output.read_bytes() == b"existing artifact"




@pytest.mark.parametrize("interrupt", [False, True])
def test_real_child_reaped_before_timeout_or_interrupt_returns(monkeypatch, interrupt):
    original = assembly.subprocess.Popen
    processes = []

    def spawn(*args, **kwargs):
        process = original(*args, **kwargs)
        processes.append(process)
        if interrupt:
            wait = process.wait
            calls = []

            def interrupted_wait(*args, **kwargs):
                if not calls:
                    calls.append(True)
                    raise KeyboardInterrupt()
                return wait(*args, **kwargs)

            process.wait = interrupted_wait
        return process

    monkeypatch.setattr(assembly.subprocess, "Popen", spawn)
    with pytest.raises(KeyboardInterrupt if interrupt else TimeoutError):
        assembly._run(
            (sys.executable, "-c", "import time; time.sleep(60)"),
            time.monotonic() + 0.1,
            time.monotonic,
        )
    assert len(processes) == 1 and processes[0].returncode is not None
