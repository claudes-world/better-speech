import json
import socket
import threading
import time
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
    httpx = pytest.importorskip("httpx")
    requests = []

    def respond(request):
        requests.append(request)
        return httpx.Response(200, content=b"audio")

    native = replace(
        clip("elevenlabs", "vi"),
        text="Cảm ơn.",
        voice="configured-id",
        model="eleven_turbo_v2_5",
        rate=0.9,
        output_format="mp3_44100_128",
    )
    with httpx.Client(transport=httpx.MockTransport(respond)) as client:
        assert ElevenLabsProvider("test-credential", client=client).synthesize(native, timeout=2) == b"audio"
    assert len(requests) == 1
    request = requests[0]
    assert request.method == "POST" and request.url.path.endswith("/configured-id")
    assert json.loads(request.content) == {
        "text": "Cảm ơn.",
        "model_id": "eleven_turbo_v2_5",
        "voice_settings": {"speed": 0.9},
    }
    assert dict(request.url.params) == {"output_format": "mp3_44100_128"}
    assert 0 < request.extensions["timeout"]["read"] <= 2


def test_elevenlabs_dribbling_body_stops_at_operation_deadline():
    """Use the real HTTPX/httpcore read path without binding a TCP port."""
    httpx = pytest.importorskip("httpx")
    from httpcore._backends.sync import SyncStream

    left, right = socket.socketpair()

    class PlainStream(SyncStream):
        def start_tls(self, ssl_context, server_hostname=None, timeout=None):
            return self

    class Backend:
        def connect_tcp(self, *args, **kwargs):
            return PlainStream(left)

    sent = []

    def server():
        try:
            right.settimeout(2)
            right.recv(65536)
            right.sendall(b"HTTP/1.1 200 OK\r\nContent-Length: 10\r\nConnection: close\r\n\r\n")
            for _ in range(10):
                time.sleep(0.04)
                right.sendall(b"x")
                sent.append(True)
        except (BrokenPipeError, ConnectionResetError):
            pass  # The provider must close the stream before the body completes.
        finally:
            right.close()

    worker = threading.Thread(target=server)
    transport = httpx.HTTPTransport()
    transport._pool._network_backend = Backend()
    worker.start()
    try:
        with httpx.Client(transport=transport) as client:
            started = time.monotonic()
            with pytest.raises(TimeoutError):
                ElevenLabsProvider("test-credential", client=client).synthesize(
                    clip("elevenlabs", "vi"), timeout=0.1
                )
            elapsed = time.monotonic() - started
            assert elapsed < 0.2  # A buffered read takes >0.4 seconds.
            assert left.fileno() == -1
    finally:
        left.close()
        worker.join(2)
    assert not worker.is_alive() and len(sent) < 10


def test_google_initialization_consumes_rpc_budget(monkeypatch):
    texttospeech = pytest.importorskip("google.cloud.texttospeech")
    client = SimpleNamespace()
    observed = []

    def synthesize(**kwargs):
        observed.append(kwargs["timeout"])
        return SimpleNamespace(audio_content=b"audio")

    client.synthesize_speech = synthesize

    def initialize():
        time.sleep(0.04)
        return client

    monkeypatch.setattr(texttospeech, "TextToSpeechClient", initialize)
    assert GoogleProvider().synthesize(clip(), timeout=0.2) == b"audio"
    assert len(observed) == 1 and 0 < observed[0] < 0.18


def test_missing_credential_never_makes_paid_attempt():
    pytest.importorskip("httpx")
    with pytest.raises(ValueError):
        ElevenLabsProvider().synthesize(clip("elevenlabs", "vi"), timeout=1)


@pytest.mark.parametrize('mode', ['stall', 'headers', 'gzip'])
def test_elevenlabs_total_deadline_including_buffered_progress(mode):
    httpx = pytest.importorskip('httpx')
    closed = threading.Event()

    class Stream(httpx.SyncByteStream):
        def __iter__(self):
            if mode == 'gzip':
                import gzip
                for byte in gzip.compress(b'audio'):
                    time.sleep(0.02)
                    yield bytes([byte])
            else:
                time.sleep(0.07)
                yield b'x'
                time.sleep(0.15)
                yield b'y'

        def close(self):
            closed.set()

    def respond(request):
        if mode == 'headers':
            time.sleep(0.25)
        return httpx.Response(200, stream=Stream(), headers={'Content-Encoding': 'gzip'} if mode == 'gzip' else {})

    with httpx.Client(transport=httpx.MockTransport(respond)) as client:
        start = time.monotonic()
        with pytest.raises(TimeoutError):
            ElevenLabsProvider('test', client=client).synthesize(clip('elevenlabs', 'vi'), timeout=0.1)
        assert time.monotonic() - start < 0.18
    assert closed.wait(0.5)


def test_elevenlabs_caps_audio_while_streaming(monkeypatch):
    httpx = pytest.importorskip('httpx')
    monkeypatch.setattr('better_speech.providers.elevenlabs.MAX_AUDIO_BYTES', 64 * 1024)
    consumed = []

    class Stream(httpx.SyncByteStream):
        def __iter__(self):
            for _ in range(100):
                consumed.append(True)
                yield b'x' * (64 * 1024)

    with httpx.Client(transport=httpx.MockTransport(lambda r: httpx.Response(200, stream=Stream()))) as client:
        with pytest.raises(ValueError, match='size limit'):
            ElevenLabsProvider('test', client=client).synthesize(clip('elevenlabs', 'vi'), timeout=2)
    assert len(consumed) == 2
