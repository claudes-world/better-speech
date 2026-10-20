"""Local utterance assembly; no provider, delivery or publication authority.

Inputs are custody-resolved local clips. The caller supplies its existing
deadline and private directories. Success creates a new complete file without
overwriting an existing artifact; custody must still authorize its publication.
"""

from __future__ import annotations

import subprocess
import os
import tempfile
import time
from collections.abc import Callable
from pathlib import Path

from .executor import AudioPart, ffmpeg_assembly_program


class AssemblyError(RuntimeError):
    pass


def _run(command: tuple[str, ...], deadline: float, clock: Callable[[], float]):
    if deadline <= clock():
        raise TimeoutError("assembly deadline expired")
    process = subprocess.Popen(
        command, stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL
    )
    try:
        budget = deadline - clock()
        if budget <= 0:
            raise TimeoutError("assembly deadline expired")
        try:
            process.wait(timeout=budget)
        except subprocess.TimeoutExpired as error:
            raise TimeoutError("assembly deadline expired") from error
        if process.returncode != 0:
            raise AssemblyError("ffmpeg assembly failed")
    finally:
        if process.poll() is None:
            process.kill()
        process.wait()


def assemble_audio(
    parts: tuple[AudioPart, ...],
    *,
    scratch_parent: Path,
    output: Path,
    deadline: float,
    clock: Callable[[], float] = time.monotonic,
) -> str:
    """Run the existing ffmpeg program under one deadline; clean on all exits.

    Only our temporary paths are removed. The final hard link is atomic and
    fails if output already exists. Its staging file is in output's directory,
    so publication does not require scratch and output to share a filesystem.
    Neither final artifact linking nor this return claims a database fence.
    """
    if deadline <= clock():
        raise TimeoutError("assembly deadline expired")
    fd, staged_name = tempfile.mkstemp(prefix=".speech-assembly-", suffix=".ogg", dir=output.parent)
    os.close(fd)
    staged = Path(staged_name)
    try:
        with tempfile.TemporaryDirectory(prefix="speech-assembly-", dir=scratch_parent) as owned:
            program = ffmpeg_assembly_program(parts, scratch=Path(owned), output=staged)
            Path(program.manifest_path).write_text(program.manifest_text, encoding="utf-8")
            for command in program.commands:
                _run(command, deadline, clock)
            if deadline <= clock():
                raise TimeoutError("assembly deadline expired")
            if staged.stat().st_size == 0:
                raise AssemblyError("ffmpeg returned empty audio")
            os.link(staged, output)
            return str(output)
    finally:
        staged.unlink(missing_ok=True)
