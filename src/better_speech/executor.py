"""Pure ffmpeg concatenation, silence and loudnorm program; no synthesis retries."""

from __future__ import annotations

import math
from dataclasses import dataclass
from pathlib import Path

LOUDNORM = "loudnorm=I=-11:LRA=11:TP=-1.5,volume=3dB,alimiter=limit=0.95"


@dataclass(frozen=True)
class AudioPart:
    kind: str
    reference: str = ""
    seconds: float = 0


@dataclass(frozen=True)
class AssemblyProgram:
    commands: tuple[tuple[str, ...], ...]
    manifest_path: str
    manifest_text: str


def ffmpeg_assembly_program(parts: tuple[AudioPart, ...], *, scratch: Path, output: Path) -> AssemblyProgram:
    """Existing ffmpeg contract, no process or file writes in this pure seam.

    Input refs here must be custody-resolved local paths. Caller creates a unique
    owned scratch directory, writes manifest, runs commands and cleans after all
    work. Never pass opaque user references as local paths.
    """
    commands = []
    silence_commands = []
    wavs = []
    silences = {}

    def command(*args):
        target = silence_commands if args[:2] == ("-f", "lavfi") else commands
        target.append(("ffmpeg", *args, "-y", "-loglevel", "error"))

    if not parts:
        raise ValueError("empty audio assembly")
    for i, part in enumerate(parts):
        wav = str(scratch / f"part-{i}.wav")
        if part.kind == "silence":
            if type(part.seconds) not in (int, float) or not math.isfinite(part.seconds) or part.seconds < 0:
                raise ValueError("invalid silence duration")
            if part.seconds in silences:
                wavs.append(silences[part.seconds])
                continue
            command("-f", "lavfi", "-i", "anullsrc=r=48000:cl=mono", "-t", str(part.seconds), wav)
            silences[part.seconds] = wav
        elif part.kind == "clip" and part.reference:
            command("-i", part.reference, "-ar", "48000", "-ac", "1", wav)
        else:
            raise ValueError("invalid assembly part")
        wavs.append(wav)
    manifest = str(scratch / "list.txt")
    joined = str(scratch / "joined.wav")
    command("-f", "concat", "-safe", "0", "-i", manifest, joined)
    # Ogg otherwise chooses a random stream serial on each render. Stable mux
    # output lets retention rehydrate the same immutable derivative checksum.
    command(
        "-i",
        joined,
        "-filter:a",
        LOUDNORM,
        "-fflags",
        "+bitexact",
        "-flags:a",
        "+bitexact",
        "-c:a",
        "libopus",
        str(output),
    )
    contents = "".join("file '" + p.replace("'", "'\\''") + "'\n" for p in wavs)
    return AssemblyProgram(tuple(silence_commands + commands), manifest, contents)
