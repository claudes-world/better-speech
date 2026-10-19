from dataclasses import replace
from types import SimpleNamespace

import pytest

from better_speech import Clip
from better_speech.providers.elevenlabs import ElevenLabsProvider
from better_speech.providers.google import GoogleProvider


def clip(provider="google", language="en", **kwargs):
    return Clip(
        "A <break> literal.", language, "en-US-Neural2-J", 1.05, 0, provider, "neural2", "mp3", **kwargs
    )


def test_google_uses_plain_text_and_one_bounded_attempt():
    pytest.importorskip("google.cloud.texttospeech")

    class Client:
        def synthesize_speech(self, **kwargs):
            self.request = kwargs
            return SimpleNamespace(audio_content=b"audio")

    client = Client()
    assert GoogleProvider(client).synthesize(clip(), timeout=2) == b"audio"
    request = client.request
    assert request["input"].text == "A <break> literal."
    assert not request["input"].ssml
    assert request["voice"].name == "en-US-Neural2-J"
    assert request["audio_config"].speaking_rate == 1.05
    assert request["retry"] is None and 0 < request["timeout"] <= 2


def test_elevenlabs_uses_configured_native_voice_and_no_retry():
    pytest.importorskip("httpx")

    class Client:
        def post(self, url, **kwargs):
            self.url, self.request = url, kwargs
            return SimpleNamespace(raise_for_status=lambda: None, content=b"audio")

    client = Client()
    native = replace(
        clip("elevenlabs", "vi"),
        text="Cảm ơn.",
        voice="configured-id",
        model="eleven_turbo_v2_5",
        rate=0.9,
        output_format="mp3_44100_128",
    )
    assert ElevenLabsProvider("test-credential", client=client).synthesize(native, timeout=2) == b"audio"
    assert client.url.endswith("/configured-id")
    assert client.request["json"] == {
        "text": "Cảm ơn.",
        "model_id": "eleven_turbo_v2_5",
        "voice_settings": {"speed": 0.9},
    }
    assert client.request["params"] == {"output_format": "mp3_44100_128"}
    assert 0 < client.request["timeout"] <= 2


def test_missing_credential_never_makes_paid_attempt():
    pytest.importorskip("httpx")
    with pytest.raises(ValueError):
        ElevenLabsProvider().synthesize(clip("elevenlabs", "vi"), timeout=1)
