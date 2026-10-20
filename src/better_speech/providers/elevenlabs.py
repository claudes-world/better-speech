"""ElevenLabs request construction copied from the earlier Python tts.py adapter."""

from __future__ import annotations

import json
import time
from threading import Event, Thread
from urllib.parse import quote

from ..budget import remaining
from ..types import Clip

MAX_AUDIO_BYTES = 20 * 1024 * 1024


class ElevenLabsProvider:
    languages = frozenset({"vi"})
    max_request_bytes = 12000
    formats = frozenset({"mp3_44100_128"})

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

        # HTTPX timeouts apply to individual operations. A supervising caller
        # enforces the whole-call deadline, including headers and decoder buffering.
        client = self.client if self.client is not None else httpx.Client()
        done, expired = Event(), Event()
        responses, result, errors = [], [], []

        def call():
            try:
                with client.stream(
                    "POST",
                    "https://api.elevenlabs.io/v1/text-to-speech/" + quote(clip.voice, safe=""),
                    params={"output_format": clip.output_format},
                    json=body,
                    headers={"xi-api-key": self.api_key},
                    timeout=remaining(deadline),
                ) as response:
                    responses.append(response)
                    remaining(deadline)
                    response.raise_for_status()
                    audio = bytearray()
                    for chunk in response.iter_bytes(chunk_size=64 * 1024):
                        remaining(deadline)
                        if expired.is_set():
                            raise TimeoutError("provider deadline expired")
                        if len(audio) + len(chunk) > MAX_AUDIO_BYTES:
                            raise ValueError("provider audio exceeds size limit")
                        audio.extend(chunk)
                    remaining(deadline)
                    result.append(bytes(audio))
            except BaseException as error:
                errors.append(error)
            finally:
                if self.client is None:
                    client.close()
                done.set()

        worker = Thread(target=call, name="elevenlabs-request", daemon=True)
        worker.start()
        try:
            if not done.wait(remaining(deadline)):
                raise TimeoutError("provider deadline expired")
            remaining(deadline)
            if errors:
                raise errors[0]
            return result[0]
        except TimeoutError:
            expired.set()
            # Closing the active response aborts the transport; before headers,
            # close the client instead. Late worker results are never consumed.
            if responses:
                responses[0].close()
            else:
                client.close()
            raise
