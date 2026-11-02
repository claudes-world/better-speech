"""0.2.18 per-phrase marks: recall mode, retire and restore (PLAN.md sections 2 and 7B)."""

import json
from dataclasses import replace
from datetime import datetime, timedelta, timezone

import pytest

from better_speech import compile
from better_speech.cli import main
from better_speech.compiler import _rank
from better_speech.curriculum import runtime_curriculum
from better_speech.learning import due, learn, learned, phrases, restore, retire, set_mode
from better_speech.ledger import Ledger
from tests.test_model_free_audio import profile

NOW = datetime(2026, 10, 21, 14, tzinfo=timezone.utc)


@pytest.fixture
def cfg(tmp_path):
    return profile(state_dir=str(tmp_path))


def ledger(cfg):
    return Ledger.for_profile(cfg)


def shipped(cfg, index=0):
    return [r for r in runtime_curriculum(cfg) if r.get("category") is None][index]


def test_mode_round_trip_on_shipped_phrase_keeps_its_schedule(cfg):
    row = shipped(cfg)
    stored = set_mode(row["id"], "recall", cfg, ledger=ledger(cfg))
    assert stored == {**row, "learner_phrase": False, "recall": True}
    listed = {p["id"]: p for p in phrases(cfg, ledger=ledger(cfg))}
    assert listed[row["id"]]["mode"] == "recall" and listed[row["id"]]["learner_phrase"] is False
    runtime = {r["id"]: r for r in runtime_curriculum(cfg, additions=ledger(cfg).learner_state(cfg.learner_id)[1])}
    # The override keeps every shipped field (hint, units, category) and adds only the mark.
    assert {k: v for k, v in runtime[row["id"]].items() if k not in ("recall", "learner_phrase")} == {
        k: v for k, v in row.items() if k != "learner_phrase"}
    # Same priority tier as an unmarked shipped phrase.
    assert _rank([runtime[row["id"]]], {}, "2026-10-21T14:00:00.000Z") == [runtime[row["id"]]]
    assert not runtime[row["id"]]["learner_phrase"]
    assert set_mode(row["ledger_identity"], "learning", cfg, ledger=ledger(cfg))["learner_phrase"] is False
    assert {p["id"]: p for p in phrases(cfg, ledger=ledger(cfg))}[row["id"]]["mode"] == "learning"
    with pytest.raises(ValueError):
        set_mode(row["id"], "fast", cfg, ledger=ledger(cfg))
    with pytest.raises(ValueError):
        set_mode("no-such-phrase", "recall", cfg, ledger=ledger(cfg))


def test_mode_on_learner_phrase_keeps_learner_priority_and_learn_reclaims_a_marked_phrase(cfg):
    added = learn("Học nhé.", "Let us learn.", cfg, now=NOW, ledger=ledger(cfg))
    marked = set_mode(added["id"], "recall", cfg, ledger=ledger(cfg))
    assert marked["recall"] is True and "learner_phrase" not in marked and marked["pending_learning"] is True
    row = shipped(cfg, 1)
    retire(row["id"], cfg, ledger=ledger(cfg))
    relearned = learn(row["l2"], "New gloss.", cfg, now=NOW, ledger=ledger(cfg))
    assert "learner_phrase" not in relearned and "retired" not in relearned
    listed = {p["id"]: p for p in phrases(cfg, ledger=ledger(cfg))}
    assert listed[row["id"]]["learner_phrase"] is True


def test_retired_phrase_leaves_ranking_due_and_compile_but_keeps_history(cfg):
    target = shipped(cfg)
    first = compile("Source.", replace(cfg, closer_chance=0.0), ledger=ledger(cfg), now=NOW)
    assert first.lessons
    heard = first.lessons[0]
    plan = replace(first, executed=True)
    ledger(cfg).record(plan, "delivery-1", NOW)
    retire(heard.phrase_key, cfg, ledger=ledger(cfg))
    retire(target["id"], cfg, ledger=ledger(cfg))
    later = NOW + timedelta(days=60)
    hidden = {heard.phrase_key, target["ledger_identity"]}
    for seed in range(15):
        result = compile("Source.", replace(cfg, closer_chance=1.0), ledger=ledger(cfg), now=later, seed=seed,
                         greeting=True)
        assert not hidden & {lesson.phrase_key for lesson in result.lessons}
    assert not hidden & {r["ledger_identity"] for r in due(cfg, ledger=ledger(cfg), now=later)}
    records, _ = ledger(cfg).learner_state(cfg.learner_id)
    assert heard.phrase_key in records  # exposure history stays
    assert {p["id"] for p in phrases(cfg, ledger=ledger(cfg), retired=True)} >= {target["id"]}
    assert target["id"] not in {p["id"] for p in phrases(cfg, ledger=ledger(cfg), retired=False)}
    restored = restore(heard.phrase_key, cfg, ledger=ledger(cfg))
    assert "retired" not in restored
    assert heard.phrase_key in {r["ledger_identity"] for r in due(cfg, ledger=ledger(cfg), now=later)}
    assert ledger(cfg).learner_state(cfg.learner_id)[0][heard.phrase_key] == records[heard.phrase_key]


def test_marks_survive_learned(cfg):
    row = shipped(cfg)
    set_mode(row["id"], "recall", cfg, ledger=ledger(cfg))
    stored = learned(row["id"], cfg, ledger=ledger(cfg))
    assert stored["recall"] is True and stored["landed"] is True


def test_unmarked_learner_has_no_rows_and_unchanged_plans(cfg, tmp_path):
    other = profile(state_dir=str(tmp_path / "other"))
    assert ledger(other).learner_state(other.learner_id)[1] == []
    assert all(p["mode"] == "learning" and not p["retired"] for p in phrases(other, ledger=ledger(other)))
    a = compile("Source.", other, ledger=ledger(other), now=NOW)
    b = compile("Source.", other, ledger=ledger(other), now=NOW)
    assert a.to_dict() == b.to_dict()


def test_cli_phrases_mode_retire_restore(tmp_path, capsys):
    config = tmp_path / "profile.toml"
    config.write_text('model="none"\nstate_dir="state"\n[[voices]]\nvoice="native"\n')
    args = ["--profile", str(config)]
    assert main(["phrases", *args]) == 0
    listed = json.loads(capsys.readouterr().out)
    key = next(p["id"] for p in listed if p["category"] is None)
    assert main(["mode", key, "recall", *args]) == 0
    assert json.loads(capsys.readouterr().out)["recall"] is True
    assert main(["retire", key, *args]) == 0
    assert json.loads(capsys.readouterr().out)["retired"] is True
    assert main(["phrases", "--retired", *args]) == 0
    assert [p["id"] for p in json.loads(capsys.readouterr().out)] == [key]
    assert main(["restore", key, *args]) == 0
    assert "retired" not in json.loads(capsys.readouterr().out)
    assert main(["phrases", "--active", *args]) == 0
    assert len(json.loads(capsys.readouterr().out)) == len(listed)
    assert main(["retire", "missing", *args]) == 2
