"""Host-configured TTS clients. Cache hits never invoke this protocol."""

from typing import Protocol

from ..types import Clip


class Provider(Protocol):
    def synthesize(self, clip: Clip, *, timeout: float) -> bytes: ...


__all__ = ["Provider"]
