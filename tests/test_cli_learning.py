import json
from dataclasses import replace
from datetime import datetime, timezone
from unittest.mock import Mock

import pytest

from better_speech import compile
from better_speech.cli import main
from better_speech.config import load_profile
from better_speech.learning import learn
from better_speech.ledger import Ledger
from tests.test_model_free_audio import EmptyLedger, ToneProvider, profile


def provider_factory(provider):
    return Mock(return_value=provider, languages=provider.languages, formats=provider.formats)


@pytest.fixture
def config(tmp_path):
    path = tmp_path / "profile.toml"
    path.write_text('model="none"\nstate_dir="state"\n[[voices]]\nvoice="native"\n')
    return path


@pytest.mark.parametrize(
    "zone,instant,expected",
    [
        ("Asia/Ho_Chi_Minh", "2026-10-19T12:00:00+00:00", "2026-10-19T17:00:00.000Z"),
        ("America/New_York", "2026-03-08T06:30:00+00:00", "2026-03-09T04:00:00.000Z"),
        ("UTC", "2026-10-19T23:59:59+00:00", "2026-10-20T00:00:00.000Z"),
    ],
)
def test_boost_next_local_midnight_in_profile_zone(tmp_path, zone, instant, expected):
    cfg = profile(state_dir=str(tmp_path), timezone=zone)
    assert (
        learn("Học nhé.", "Let us learn.", cfg, now=datetime.fromisoformat(instant), ledger=Ledger.for_profile(cfg))["boost_until"]
        == expected
    )


def test_existing_sample_phrase_can_be_learned_and_reloaded(tmp_path):
    cfg = profile(state_dir=str(tmp_path))
    instant = datetime(2026, 10, 19, 12, tzinfo=timezone.utc)
    row = learn("Cảm ơn nha!", "Thanks!", cfg, now=instant, ledger=Ledger.for_profile(cfg))
    result = compile("The update is ready.", cfg, now=instant, ledger=Ledger.for_profile(cfg))
    assert result.status == "bilingual", result.diagnostics
    assert result.lessons[0].id == row["id"]
    assert sum(c.language == "vi" for c in result.clips) == 2
    assert Ledger.for_profile(cfg).learner_state(cfg.learner_id)[1]


def test_cli_learn_due_plan_learned_and_learner_isolation(config, tmp_path, capsys):
    args = ["--profile", str(config), "--learner", "other"]
    assert main(["learn", "Học nhé.", "--gloss", "Let us learn.", *args]) == 0
    learned_row = json.loads(capsys.readouterr().out)
    assert main(["due", *args]) == 0
    assert json.loads(capsys.readouterr().out)[0]["id"] == learned_row["id"]
    source = tmp_path / "in.md"
    source.write_text("The update is ready.")
    assert main(["plan", str(source), *args]) == 0
    plan = json.loads(capsys.readouterr().out)
    assert plan["learner_id"] == "other" and not plan["executed"]
    assert plan["lessons"][0]["id"] == learned_row["id"]
    assert Ledger(tmp_path / "state/ledger.sqlite3").snapshot("other") == {}
    assert main(["learned", learned_row["id"], *args]) == 0
    assert json.loads(capsys.readouterr().out)["landed"]
    cfg = load_profile(config)
    assert not Ledger.for_profile(cfg).learner_state(cfg.learner_id)[1]
    assert Ledger.for_profile(replace(cfg, learner_id="../../escape")).path == tmp_path / "state/ledger.sqlite3"


def test_cli_render_prints_executed_transcript(config, tmp_path, monkeypatch, capsys):
    provider = ToneProvider()
    monkeypatch.setattr("better_speech.providers.google.GoogleProvider", provider_factory(provider))
    monkeypatch.setattr("better_speech.providers.elevenlabs.ElevenLabsProvider", provider_factory(provider))
    source, output = tmp_path / "in.md", tmp_path / "note.ogg"
    source.write_text("The update is ready.\n\n[Read more](https://example.org).")
    assert (
        main(
            [
                "render",
                str(source),
                "--out",
                str(output),
                "--profile",
                str(config),
                "--cache",
                str(tmp_path / "cache"),
            ]
        )
        == 0
    )
    markdown = capsys.readouterr().out
    assert "<summary>Vietnamese lesson</summary>" in markdown and "Links" in markdown
    assert output.read_bytes().startswith(b"OggS")
    assert not (tmp_path / "state/ledger.sqlite3").exists()


def test_missing_profile_is_diagnostic_english(tmp_path):
    cfg = load_profile(tmp_path / "missing.toml")
    plan = compile("An update.", cfg, ledger=EmptyLedger())
    assert plan.status == "english" and plan.diagnostics


def queue_writer(state_dir, vietnamese, ready):
    ready.wait(10)
    learn(vietnamese, "A learner phrase.", profile(state_dir=state_dir), ledger=Ledger.for_profile(profile(state_dir=state_dir)))


def test_simultaneous_learns_preserve_both_phrases(tmp_path):
    import multiprocessing

    context = multiprocessing.get_context("spawn")
    ready = context.Event()
    processes = [
        context.Process(target=queue_writer, args=(str(tmp_path), phrase, ready))
        for phrase in ("Học nữa.", "Học tiếp nhé.")
    ]
    for process in processes:
        process.start()
    ready.set()
    for process in processes:
        process.join(15)
        assert process.exitcode == 0
    rows = Ledger.for_profile(profile(state_dir=str(tmp_path))).learner_state("liam")[1]
    assert {row["l2"] for row in rows} == {"Học nữa.", "Học tiếp nhé."}
    assert all(row["pending_learning"] for row in rows)


def test_cli_text_degradation_removes_previous_successful_output(config, tmp_path, monkeypatch, capsys):
    provider = ToneProvider()
    monkeypatch.setattr("better_speech.providers.google.GoogleProvider", provider_factory(provider))
    monkeypatch.setattr("better_speech.providers.elevenlabs.ElevenLabsProvider", provider_factory(provider))
    source, output = tmp_path / "in.md", tmp_path / "note.ogg"
    args = [
        "render",
        str(source),
        "--out",
        str(output),
        "--profile",
        str(config),
        "--cache",
        str(tmp_path / "cache"),
    ]
    source.write_text("The earlier note.")
    assert main(args) == 0
    assert output.read_bytes().startswith(b"OggS")
    capsys.readouterr()

    class Unavailable(ToneProvider):
        def synthesize(self, clip, *, timeout):
            raise TimeoutError("provider unavailable")

    monkeypatch.setattr("better_speech.providers.google.GoogleProvider", Unavailable)
    monkeypatch.setattr("better_speech.providers.elevenlabs.ElevenLabsProvider", provider_factory(Unavailable()))
    source.write_text("A different complete note.")
    assert main(args) == 0
    printed = capsys.readouterr()
    assert "A different complete note." in printed.out
    assert "Vietnamese lesson" not in printed.out
    assert "english_render" in printed.err
    assert not output.exists()
