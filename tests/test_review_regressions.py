"""Public-behavior regressions found by the cold review; no paid transports."""

import json
import sqlite3
import sys
import time
from dataclasses import replace
from pathlib import Path

import pytest

from better_speech import Ledger, compile, record_delivered, render
from better_speech.config import load_profile
from tests.test_model_free_audio import EmptyLedger, ToneProvider, profile


@pytest.mark.parametrize(
    "field,value",
    [
        ("pacing", {"default": {"p0": float("nan")}}),
        ("pacing", object()),
        ("compile_timeout_s", "bad"),
        ("render_timeout_s", None),
        ("fallback_reserve_s", float("inf")),
        ("provider_timeout_s", -1),
        ("repeat", "false"),
    ],
)
def test_invalid_optional_profile_never_refuses_english(field, value, tmp_path):
    cfg = replace(profile(state_dir=str(tmp_path)), **{field: value})
    plan = compile("Complete source.", cfg, ledger=EmptyLedger())
    assert plan.status == "english" and not plan.lessons and plan.diagnostics
    result = render(plan, {"google": ToneProvider()}, tmp_path / "cache")
    assert result.audio_path and result.status == "english", result.diagnostics
    assert "".join(c.text for c in result.plan.clips) == "Complete source."


@pytest.mark.parametrize("value", ["[]", '{"weights":[]}'])
def test_malformed_voice_pool_is_diagnostic_english(tmp_path, value):
    pool = tmp_path / "pool.json"
    pool.write_text(value)
    settings = tmp_path / "profile.toml"
    settings.write_text('voices_file="pool.json"\n')
    plan = compile("Complete source.", load_profile(settings), ledger=Ledger.for_profile(load_profile(settings)))
    assert not plan.lessons and any(d.stage == "config" for d in plan.diagnostics)


def test_non_json_curriculum_does_not_raise_while_hashing_fallback(tmp_path):
    path = tmp_path / "curriculum.json"
    path.write_text('{"focusPhrases":[{"id":"x","l2":"Xin chào.","l1":"Hello.","unused":NaN}]}')
    plan = compile("Source.", profile(curriculum_path=str(path), state_dir=str(tmp_path)), ledger=Ledger.for_profile(profile(curriculum_path=str(path), state_dir=str(tmp_path))))
    assert plan.status == "english" and plan.diagnostics
    assert plan.identity


def test_ledger_read_failure_does_not_reintroduce_unknown_mastered_phrases():
    class Unavailable(EmptyLedger):
        def snapshot(self, *args, **kwargs):
            raise sqlite3.OperationalError("unavailable")

    plan = compile("Complete source.", profile(), ledger=Unavailable())
    assert plan.status == "english" and not plan.lessons
    assert any(d.stage == "ledger" for d in plan.diagnostics)


def test_contextual_size_overflow_cannot_evict_reserved_lesson(tmp_path):
    curriculum = tmp_path / "curriculum.json"
    curriculum.write_text(
        json.dumps(
            {
                "focusPhrases": [
                    {"id": "reserved", "l2": "Học.", "l1": "Learn."},
                    {
                        "id": "optional",
                        "l2": "Xin chào. " * 90,
                        "l1": "Hello. " * 90,
                        "lesson_type": "connector",
                    },
                ]
            }
        )
    )
    cfg = replace(profile(curriculum_path=str(curriculum)), model="claude")
    source = "A useful source sentence. " * 450
    baseline = compile(source, replace(cfg, model="none"), ledger=EmptyLedger())
    assert baseline.status == "bilingual"

    def select(payload, *, timeout):
        return json.dumps({"placements": [{"phraseId": "optional", "slotId": payload["slots"][0]}]})

    plan = compile(source, cfg, ledger=EmptyLedger(), model_call=select)
    assert [v.id for v in plan.lessons] == ["reserved"], plan.diagnostics
    assert any(d.reason == "contextual_plan_rejected" for d in plan.diagnostics)
    assert "".join(c.text for c in plan.clips if c.purpose == "source") == plan.speech_text


@pytest.mark.parametrize("failure", ["setup", "manifest", "settings", "cache"])
def test_render_setup_and_optional_metadata_fail_open(tmp_path, monkeypatch, failure):
    plan = compile("Complete source.", profile(), ledger=EmptyLedger())
    cache = tmp_path / "cache"
    render_options = {}
    if failure == "setup":

        def unavailable(**kwargs):
            raise PermissionError("temporary storage unavailable")

        monkeypatch.setattr("better_speech.rendering.tempfile.mkdtemp", unavailable)
    elif failure == "manifest":
        plan = replace(plan, lessons=(replace(plan.lessons[0], record_json="{}"),))
    elif failure == "settings":
        render_options = {"render_timeout_s": "bad"}
    else:
        cache = object()
    result = render(plan, {"google": ToneProvider(), "elevenlabs": ToneProvider()}, cache, **render_options)
    assert result.diagnostics
    if failure == "setup":
        assert result.status == "text" and result.audio_path is None
    elif failure == "cache":
        assert result.status == "bilingual" and result.audio_path
    else:
        assert result.status == "english" and result.audio_path
    if failure != "cache":
        assert not result.plan.lessons and "Vietnamese lexicon" not in result.markdown
    assert "Complete source." in result.markdown


def test_audio_hash_failure_renders_complete_english(tmp_path, monkeypatch):
    plan = compile("Complete source.", profile(), ledger=EmptyLedger())
    original = Path.open
    failed = []

    def open_file(path, *args, **kwargs):
        if path.name == "note.ogg" and args == ("rb",) and not failed:
            failed.append(True)
            raise OSError("cannot read primary artifact")
        return original(path, *args, **kwargs)

    monkeypatch.setattr(Path, "open", open_file)
    result = render(plan, {"google": ToneProvider(), "elevenlabs": ToneProvider()}, tmp_path)
    assert failed and result.audio_path and result.status == "english"
    assert not result.plan.lessons and "Vietnamese lexicon" not in result.markdown


def test_provider_returning_after_its_timeout_yields_english(tmp_path):
    class LateNative(ToneProvider):
        def synthesize(self, clip, *, timeout):
            time.sleep(timeout + 0.02)
            return super().synthesize(clip, timeout=timeout)

    plan = compile("Source.", profile(provider_timeout_s=0.02), ledger=EmptyLedger())
    result = render(
        plan, {"google": ToneProvider(), "elevenlabs": LateNative()}, tmp_path, deadline=time.monotonic() + 3, provider_timeout_s=0.02
    )
    assert result.status == "english" and result.audio_path
    assert any(d.reason == "TimeoutError" for d in result.diagnostics)


@pytest.mark.parametrize("adapter", ["claude", "codex", "agy"])
@pytest.mark.parametrize("malicious", [False, True])
def test_real_subprocess_adapter_path_never_uses_model_authored_text(
    tmp_path, monkeypatch, adapter, malicious
):
    tools = tmp_path / "bin"
    tools.mkdir()
    executable = tools / adapter
    executable.write_text(
        "#!"
        + sys.executable
        + "\n"
        + """
import json, sys
if 'auth' in sys.argv:
    print(json.dumps(dict(loggedIn=True,authMethod='claude.ai',apiProvider='firstParty')))
else:
    prompt = sys.stdin.read()
    data = json.loads(prompt[prompt.index('{'):])
    row = {'phraseId': data['inventory'][0]['id'], 'slotId': data['slots'][0]}
"""
        + ("    row['rest'] = 'MODEL_AUTHORED_INTRUSION'\n" if malicious else "")
        + "    print(json.dumps({'placements':[row]}))\n"
    )
    executable.chmod(0o700)
    monkeypatch.setenv("PATH", str(tools))
    cfg = replace(profile(state_dir=str(tmp_path / "state")), model=adapter)
    plan = compile("Original source.", cfg, ledger=EmptyLedger())
    assert len(plan.lessons) == (1 if malicious else 2), plan.diagnostics
    assert "".join(c.text for c in plan.clips if c.purpose == "source") == "Original source."
    assert all("MODEL_AUTHORED_INTRUSION" not in c.text for c in plan.clips)
    if malicious:
        assert any(d.stage == "model" for d in plan.diagnostics)


def test_ledger_lock_wait_obeys_single_deadline_and_rolls_back(tmp_path):
    plan = replace(compile("Source.", profile(), ledger=EmptyLedger()), executed=True)
    ledger = Ledger(tmp_path / "ledger.db", busy_timeout_s=5)
    ledger._connect().close()
    with sqlite3.connect(ledger.path, isolation_level=None) as blocker:
        blocker.execute("BEGIN IMMEDIATE")
        started = time.monotonic()
        receipt = record_delivered(plan, "blocked", ledger=ledger, deadline=started + 0.05)
        elapsed = time.monotonic() - started
        blocker.rollback()
    assert receipt.diagnostics and elapsed < 0.5
    assert ledger.snapshot() == {}
    assert record_delivered(plan, "blocked", ledger=ledger).added == 1


def test_replay_deadline_rolls_back_new_event_and_projection(tmp_path, monkeypatch):
    plan = replace(compile("Source.", profile(), ledger=EmptyLedger()), executed=True)
    ledger = Ledger(tmp_path / "ledger.db")
    for index in range(8):
        assert record_delivered(plan, str(index), "2026-10-19T00:00:00Z", ledger=ledger).added == 1
    before = ledger.snapshot()
    from better_speech.ledger import record_exposures

    def slow(*args, **kwargs):
        time.sleep(0.02)
        return record_exposures(*args, **kwargs)

    with monkeypatch.context() as patch:
        patch.setattr("better_speech.ledger.record_exposures", slow)
        started = time.monotonic()
        receipt = record_delivered(plan, "late", ledger=ledger, deadline=started + 0.05)
        assert receipt.diagnostics and time.monotonic() - started < 0.3
    assert ledger.snapshot() == before
    with sqlite3.connect(ledger.path) as db:
        assert db.execute("SELECT count(*) FROM events").fetchone()[0] == 8


def test_artifact_hashing_shares_lesson_deadline_and_reserves_english(tmp_path, monkeypatch):
    class Clock:
        offset = 0

        @classmethod
        def monotonic(cls):
            return time.monotonic() + cls.offset

    monkeypatch.setattr("better_speech.rendering.time", Clock)
    monkeypatch.setattr("better_speech.budget.time", Clock)
    original = Path.open

    def open_file(path, *args, **kwargs):
        if path.name == "note.ogg" and args == ("rb",):
            Clock.offset += 6
        return original(path, *args, **kwargs)

    monkeypatch.setattr(Path, "open", open_file)
    plan = compile("Source.", profile(), ledger=EmptyLedger())
    result = render(
        plan,
        {"google": ToneProvider(), "elevenlabs": ToneProvider()},
        tmp_path,
        deadline=time.monotonic() + 10,
    )
    assert result.audio_path and result.status == "english", result.diagnostics
    assert not result.plan.lessons
    assert any(d.reason == "TimeoutError" for d in result.diagnostics)
