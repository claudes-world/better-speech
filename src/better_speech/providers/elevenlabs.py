"""ElevenLabs request construction copied from the earlier Python tts.py adapter."""

from __future__ import annotations

import json
import time
from urllib.parse import quote

from ..budget import remaining
from ..types import Clip


class ElevenLabsProvider:
    def __init__(self, api_key: str | None = None, *, client=None):
        self.api_key = api_key
        self.client = client

    def synthesize(self, clip: Clip, *, timeout: float) -> bytes:
        deadline = time.monotonic() + timeout
        import httpx

        if not self.api_key:
            raise ValueError("ElevenLabs credential unavailable")
        if clip.provider != "elevenlabs" or clip.language != "vi":
            raise ValueError("ElevenLabs is configured for Vietnamese only")
        body = {
            "text": clip.text,
            "model_id": clip.model,
            "voice_settings": {**json.loads(clip.settings_json), "speed": clip.rate},
        }

        def call(client):
            remaining(deadline)
            with client.stream(
                "POST",
                "https://api.elevenlabs.io/v1/text-to-speech/" + quote(clip.voice, safe=""),
                params={"output_format": clip.output_format},
                json=body,
                headers={"xi-api-key": self.api_key},
                timeout=remaining(deadline),
            ) as response:
                response.raise_for_status()
                chunks = []
                for chunk in response.iter_bytes():
                    remaining(deadline)
                    chunks.append(chunk)
                remaining(deadline)
                return b"".join(chunks)

        if self.client is not None:
            return call(self.client)
        remaining(deadline)
        with httpx.Client() as client:
            return call(client)
