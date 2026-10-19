"""Google plain-text synthesis, extracted from md-speak's google_synthesize."""

from __future__ import annotations

import json
import time

from ..budget import remaining
from ..types import Clip


class GoogleProvider:
    def __init__(self, client=None, *, credentials_file: str | None = None):
        self.client = client
        self.credentials_file = credentials_file

    def synthesize(self, clip: Clip, *, timeout: float) -> bytes:
        deadline = time.monotonic() + timeout
        from google.cloud import texttospeech

        if clip.provider != "google" or clip.language != "en":
            raise ValueError("Google is configured for English only")
        encoding = {
            "mp3": texttospeech.AudioEncoding.MP3,
            "ogg_opus": texttospeech.AudioEncoding.OGG_OPUS,
            "wav": texttospeech.AudioEncoding.LINEAR16,
        }[clip.output_format]
        if self.client is None:
            remaining(deadline)
            self.client = (
                texttospeech.TextToSpeechClient.from_service_account_file(self.credentials_file)
                if self.credentials_file
                else texttospeech.TextToSpeechClient()
            )
            remaining(deadline)
        settings = json.loads(clip.settings_json)
        response = self.client.synthesize_speech(
            input=texttospeech.SynthesisInput(text=clip.text),
            voice=texttospeech.VoiceSelectionParams(
                language_code="-".join(clip.voice.split("-")[:2]), name=clip.voice
            ),
            audio_config=texttospeech.AudioConfig(
                audio_encoding=encoding, speaking_rate=clip.rate, sample_rate_hertz=24000, **settings
            ),
            retry=None,
            timeout=remaining(deadline),
        )
        remaining(deadline)
        return response.audio_content
