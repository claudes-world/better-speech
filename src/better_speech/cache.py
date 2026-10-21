"""Pure clip identities and a local cache, without delivery authority.

Resolve provider downgrades, settings and rendered text BEFORE keying. Use the
returned NFC text as the actual synthesis input. Profile/slot names are plan
metadata, not audible inputs. Existing rendition keys remain untouched; never
look up a new clip under an underspecified legacy key on a miss.
"""

from __future__ import annotations

import hashlib
import json
import math
import re
import unicodedata
from collections.abc import Mapping
from dataclasses import dataclass

CLIP_SCHEMA = "worldos.speech-clip/v1"


def _number(value: int | float) -> str:
    """ECMAScript binary64 number spelling (including 1.0 and negative zero).

    Python's shortest round-trip digits use different exponent thresholds;
    expand them for JS's [1e-6, 1e21) fixed notation range.
    """
    # Do not round a Python-only exact integer into another request's identity.
    # Such settings cannot safely cross the legacy JS numeric contract; caller
    # can bypass the optional cache rather than lose source delivery.
    if type(value) is int and abs(value) > 2**53 - 1:
        raise ValueError("clip settings integer exceeds exact JS numeric range")
    number = float(value)
    if not math.isfinite(number):
        raise ValueError("clip identity requires finite numeric inputs")
    if number == 0:
        return "0"
    sign = "-" if number < 0 else ""
    digits, _, exponent = repr(abs(number)).partition("e")
    whole, _, fraction = digits.partition(".")
    point = len(whole) + (int(exponent) if exponent else 0)
    digits = (whole + fraction).rstrip("0")
    if 1e-6 <= abs(number) < 1e21:
        if point <= 0:
            rendered = "0." + "0" * -point + digits
        elif point >= len(digits):
            rendered = digits + "0" * (point - len(digits))
        else:
            rendered = digits[:point] + "." + digits[point:]
    else:
        power = point - 1
        rendered = digits[0] + ("." + digits[1:] if len(digits) > 1 else "")
        rendered += "e" + ("+" if power >= 0 else "-") + str(abs(power))
    return sign + rendered


def _string(value: str) -> str:
    # JSON.stringify escapes lone surrogate code units; UTF-8 cannot encode them.
    return re.sub(r"[\ud800-\udfff]", lambda m: f"\\u{ord(m[0]):04x}", json.dumps(value, ensure_ascii=False))


def _encode(value: object, *, sort_keys: bool = True) -> str:
    if value is None:
        return "null"
    if type(value) is bool:
        return "true" if value else "false"
    if type(value) in (int, float):
        return _number(value)
    if isinstance(value, str):
        return _string(value)
    if isinstance(value, Mapping):
        keys = list(value)
        if not all(isinstance(key, str) for key in keys):
            raise ValueError("clip settings keys must be strings")
        if sort_keys:
            keys.sort(key=lambda key: key.encode("utf-16-be", "surrogatepass"))
        return "{" + ",".join(_string(key) + ":" + _encode(value[key]) for key in keys) + "}"
    if isinstance(value, (list, tuple)):
        return "[" + ",".join(_encode(item) for item in value) + "]"
    raise ValueError("clip settings must contain JSON values")


@dataclass(frozen=True)
class ClipIdentity:
    cache_key: str
    canonical_bytes: bytes
    text: str
    input_sha256: str


def clip_identity(
    *,
    provider: str,
    model: str,
    voice: str,
    rate: float,
    text: str,
    response_format: str,
    voice_settings: Mapping,
    input_mode: str = "text",
    pronunciations=(),
) -> ClipIdentity:
    """Hash actual effective request inputs, with stable recursive key order.

    Names/provenance of pacing profiles deliberately have no parameter. Pass
    only settings actually consumed by synthesis, not a whole lane snapshot.
    Invalid optional configuration must degrade at the caller, never prevent
    source delivery. This pure function performs no I/O or provider validation.
    """
    if input_mode not in ("text", "ssml"):
        raise ValueError("invalid speech input mode")
    if type(rate) not in (int, float) or not math.isfinite(rate) or rate <= 0:
        raise ValueError("clip rate must be positive and finite")
    normalized = unicodedata.normalize("NFC", text)
    encoded = _encode(
        {
            "schema_version": CLIP_SCHEMA,
            "provider": provider,
            "model": model,
            "voice": voice,
            "rate": rate,
            "text": normalized,
            "format": response_format,
            "voice_settings": voice_settings,
            "input_mode": input_mode,
            **({"pronunciations": pronunciations} if pronunciations else {}),
        }
    ).encode("utf-8")
    return ClipIdentity(
        hashlib.sha256(encoded).hexdigest(),
        encoded,
        normalized,
        hashlib.sha256(normalized.encode("utf-8")).hexdigest(),
    )


class ClipCache:
    """Atomic content-addressed audio with bounded per-key interprocess locks.

    A checksum envelope detects interrupted/corrupt external writes. Cache I/O
    failure bypasses caching, but never catches or retries a provider failure.
    """

    def __init__(self, directory, *, lock_timeout: float = 5):
        from pathlib import Path

        self.directory = Path(directory)
        self.lock_timeout = lock_timeout

    def get_or_synthesize(self, clip, synthesize, *, deadline, diagnostics):
        import os
        import tempfile

        from filelock import FileLock, Timeout

        from .budget import remaining
        from .pronunciation import speech_input
        from .types import Diagnostic

        mode, _ = speech_input(clip)
        identity = clip_identity(
            provider=clip.provider,
            model=clip.model,
            voice=clip.voice,
            rate=clip.rate,
            text=clip.text,
            response_format=clip.output_format,
            voice_settings=json.loads(clip.settings_json),
            input_mode=mode,
            pronunciations=clip.pronunciations if mode == "ssml" else (),
        )
        path = self.directory / (identity.cache_key + ".clip")

        def read():
            try:
                data = path.read_bytes()
                if (
                    data[:4] == b"BSC1"
                    and len(data) > 36
                    and hashlib.sha256(data[36:]).digest() == data[4:36]
                ):
                    return data[36:]
            except FileNotFoundError:
                pass
            return None

        lock = None

        def release():
            if lock is not None and lock.is_locked:
                try:
                    lock.release()
                except OSError:
                    diagnostics.append(Diagnostic("cache", "lock_cleanup_unavailable"))

        try:
            self.directory.mkdir(parents=True, exist_ok=True)
            cached = read()
            if cached is not None:
                return cached, True
            lock = FileLock(str(path) + ".lock")
            lock.acquire(timeout=min(self.lock_timeout, remaining(deadline)))
            cached = read()  # A competing writer may have just published.
            if cached is not None:
                release()
                return cached, True
        except (OSError, Timeout) as error:
            release()
            diagnostics.append(Diagnostic("cache", "bypassed:" + type(error).__name__))
            return synthesize(), False
        try:
            audio = synthesize()  # Provider errors must propagate exactly once.
            temporary = None
            try:
                with tempfile.NamedTemporaryFile(dir=self.directory, prefix=".clip-", delete=False) as stream:
                    temporary = stream.name
                    stream.write(b"BSC1" + hashlib.sha256(audio).digest() + audio)
                    stream.flush()
                    os.fsync(stream.fileno())
                os.replace(temporary, path)
            except OSError as error:
                diagnostics.append(Diagnostic("cache", "write_bypassed:" + type(error).__name__))
            finally:
                if temporary is not None:
                    from pathlib import Path

                    try:
                        Path(temporary).unlink(missing_ok=True)
                    except OSError:
                        diagnostics.append(Diagnostic("cache", "temporary_cleanup_unavailable"))
            return audio, False
        finally:
            release()
