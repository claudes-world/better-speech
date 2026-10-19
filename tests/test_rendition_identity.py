import hashlib
import json
import os
import subprocess
import sys
from datetime import datetime, timedelta, timezone

from better_speech import compile, record_delivered, render
from better_speech.types import Plan, digest
from tests.test_model_free_audio import ToneProvider, profile


def test_advanced_ledger_changes_execution_and_custody_key(tmp_path):
    cfg = profile(state_dir=str(tmp_path / "state"))
    now = datetime(2026, 10, 1, tzinfo=timezone.utc)
    first = compile("The update is ready.", cfg, now=now)
    assert first.executed_digest is None
    provider = ToneProvider()
    providers = {"google": provider, "elevenlabs": provider}
    heard = render(first, providers, tmp_path / "cache")
    assert heard.audio_path and heard.plan.lessons, heard.diagnostics
    assert record_delivered(heard.plan, "confirmed-send", now).added == 1

    second = compile("The update is ready.", cfg, now=now + timedelta(minutes=1))
    next_hearing = render(second, providers, tmp_path / "cache")
    assert next_hearing.audio_path and next_hearing.plan.lessons, next_hearing.diagnostics
    assert first.identity == second.identity
    assert heard.plan.lessons[0].phrase_key != next_hearing.plan.lessons[0].phrase_key
    assert heard.plan.executed_digest != next_hearing.plan.executed_digest
    assert heard.artifact_identity != next_hearing.artifact_identity
    for result in (heard, next_hearing):
        assert result.artifact_identity == digest([result.plan.identity, result.plan.executed_digest])
        assert result.audio_sha256 == hashlib.sha256(result.audio_path.read_bytes()).hexdigest()
        assert Plan.from_dict(result.plan.to_dict()) == result.plan


def test_executed_digest_stable_in_another_process(tmp_path):
    plan = compile("The update is ready.", profile(state_dir=str(tmp_path / "state")))
    provider = ToneProvider()
    result = render(plan, {"google": provider, "elevenlabs": provider}, tmp_path / "cache")
    assert result.audio_path and result.plan.executed_digest
    process = subprocess.run(
        [
            sys.executable,
            "-c",
            "import json,sys; from better_speech import Plan; "
            "from better_speech.types import digest; "
            "p=Plan.from_dict(json.load(sys.stdin)); "
            "print(json.dumps([p.executed_digest,digest([p.identity,p.executed_digest])]))",
        ],
        input=json.dumps(result.plan.to_dict()),
        capture_output=True,
        text=True,
        timeout=5,
        check=True,
        env={**os.environ, "PYTHONHASHSEED": "random"},
    )
    assert json.loads(process.stdout) == [result.plan.executed_digest, result.artifact_identity]


def test_english_fallback_key_describes_executed_manifest(tmp_path):
    plan = compile("The update is ready.", profile(state_dir=str(tmp_path / "state")))
    provider = ToneProvider()
    cache = tmp_path / "cache"
    english = render(plan, {"google": provider}, cache)
    bilingual = render(plan, {"google": provider, "elevenlabs": provider}, cache)
    assert english.audio_path and english.status == "english", english.diagnostics
    assert bilingual.audio_path and bilingual.status == "bilingual", bilingual.diagnostics
    assert english.plan.identity == bilingual.plan.identity
    assert english.plan.executed and not english.plan.lessons
    assert english.plan.executed_digest != bilingual.plan.executed_digest
    assert english.artifact_identity != bilingual.artifact_identity
    text = render(plan, {}, tmp_path / "empty-cache")
    assert text.status == "text" and not text.plan.executed
    assert text.plan.executed_digest is None
    assert text.artifact_identity is None and text.audio_sha256 is None
