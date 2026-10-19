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
    result = asyncio.run(
        assembly.assemble_audio(
            (AudioPart("clip", str(source)), AudioPart("silence", seconds=0.1)),
            scratch_parent=tmp_path,
            output=output,
            deadline=time.monotonic() + 10,
        )
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
        asyncio.run(
            assembly.assemble_audio(
                (AudioPart("clip", str(source)), AudioPart("silence", seconds=0.1)),
                scratch_parent=tmp_path,
                output=output,
                deadline=time.monotonic() + 10,
            )
        )
        results.append(output.read_bytes())
    assert results[0] == results[1]


@pytest.mark.parametrize("failure", ["process", "cancel", "deadline", "empty", "exists"])
def test_no_partial_publication_and_no_overwrite(tmp_path, monkeypatch, failure):
    output = tmp_path / "output.ogg"
    if failure == "exists":
        output.write_bytes(b"existing artifact")
    now = [10.0]

    async def run(command, deadline, clock):
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
        asyncio.run(
            assembly.assemble_audio(
                (AudioPart("clip", "owned.wav"),),
                scratch_parent=tmp_path,
                output=output,
                deadline=20.0,
                clock=lambda: now[0],
            )
        )
    assert sorted(p.name for p in tmp_path.iterdir()) == (["output.ogg"] if failure == "exists" else [])
    if failure == "exists":
        assert output.read_bytes() == b"existing artifact"


@pytest.mark.parametrize("cancel", [False, True])
def test_real_child_is_reaped_before_timeout_or_cancellation_returns(monkeypatch, cancel):
    async def check():
        original = asyncio.create_subprocess_exec
        processes = []
        spawned = asyncio.Event()

        async def spawn(*args, **kwargs):
            process = await original(*args, **kwargs)
            processes.append(process)
            spawned.set()
            return process

        monkeypatch.setattr(assembly.asyncio, "create_subprocess_exec", spawn)
        task = asyncio.create_task(
            assembly._run(
                (sys.executable, "-c", "import time; time.sleep(60)"),
                time.monotonic() + (5 if cancel else 0.3),
                time.monotonic,
            )
        )
        await spawned.wait()
        if cancel:
            task.cancel()
        with pytest.raises(asyncio.CancelledError if cancel else TimeoutError):
            await task
        assert len(processes) == 1 and processes[0].returncode is not None

    asyncio.run(check())


def test_cancel_during_spawn_still_drains_and_reaps_child(monkeypatch):
    async def check():
        original = asyncio.create_subprocess_exec
        processes = []
        started, release = asyncio.Event(), asyncio.Event()

        async def spawn(*args, **kwargs):
            process = await original(*args, **kwargs)
            processes.append(process)
            started.set()
            await release.wait()
            return process

        monkeypatch.setattr(assembly.asyncio, "create_subprocess_exec", spawn)
        task = asyncio.create_task(
            assembly._run(
                (sys.executable, "-c", "import time; time.sleep(60)"),
                time.monotonic() + 5,
                time.monotonic,
            )
        )
        await started.wait()
        task.cancel()
        await asyncio.sleep(0)
        task.cancel()
        assert not task.done()
        release.set()
        with pytest.raises(asyncio.CancelledError):
            await task
        assert processes[0].returncode is not None

    asyncio.run(check())
