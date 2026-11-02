"""0.2.19 per-phrase marks: recall mode, retire and restore (PLAN.md sections 2 and 7B)."""

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
    assert stored == {"id": row["id"], "l2": row["l2"], "l1": row["l1"], "learner_phrase": False, "recall": True}
    listed = {p["id"]: p for p in phrases(cfg, ledger=ledger(cfg))}
    assert listed[row["id"]]["mode"] == "recall" and listed[row["id"]]["learner_phrase"] is False
    runtime = {r["id"]: r for r in runtime_curriculum(cfg, additions=ledger(cfg).learner_state(cfg.learner_id)[1])}
    # The override keeps every shipped field (hint, units, category) and adds only the mark.
    assert {k: v for k, v in runtime[row["id"]].items() if k not in ("recall", "learner_phrase")} == {
        k: v for k, v in row.items() if k != "learner_phrase"}
    # Same priority tier as an unmarked shipped phrase: an unseen learner phrase still ranks first,
    # and the marked phrase keeps its curriculum position among shipped phrases.
    other = shipped(cfg, 1)
    mine = dict(other, id="mine", l2="Học nhé.", phrase="Học nhé.", ledger_identity="học nhé.", learner_phrase=True)
    at = "2026-10-21T14:00:00.000Z"
    assert _rank([runtime[row["id"]], other, mine], {}, at) == [mine, runtime[row["id"]], other]
    assert not runtime[row["id"]]["learner_phrase"]
    assert "recall" not in set_mode(row["ledger_identity"], "learning", cfg, ledger=ledger(cfg))
    assert ledger(cfg).learner_state(cfg.learner_id)[1] == []  # last mark cleared: stub deleted
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


def test_noop_marks_write_nothing(cfg):
    row = shipped(cfg)
    assert set_mode(row["id"], "learning", cfg, ledger=ledger(cfg))["id"] == row["id"]
    assert restore(row["id"], cfg, ledger=ledger(cfg))["id"] == row["id"]
    assert ledger(cfg).learner_state(cfg.learner_id)[1] == []


def test_shipped_fixes_still_apply_to_marked_phrases_and_dropped_phrases_leave_no_orphan(tmp_path):
    path = tmp_path / "curriculum.json"
    base = {"id": "coffee", "l2": "Cho mình cà phê.", "l1": "Coffee, please.", "units": ["Cho mình", "cà phê"],
            "lesson_type": "connector", "hint": "chaw ming kah feh"}
    path.write_text(json.dumps({"focusPhrases": [base]}))
    cfg = profile(state_dir=str(tmp_path), curriculum_path=str(path))
    set_mode("coffee", "recall", cfg, ledger=ledger(cfg))
    retire("coffee", cfg, ledger=ledger(cfg))
    fixed = dict(base, id="coffee-v2", l1="A coffee for me, please.", hint="chaw meeng kah feh")
    path.write_text(json.dumps({"focusPhrases": [fixed]}))
    [row] = runtime_curriculum(cfg, additions=ledger(cfg).learner_state(cfg.learner_id)[1])
    assert (row["id"], row["l1"], row["translation"], row["hint"], row["recall"], row["retired"]) == (
        "coffee-v2", "A coffee for me, please.", "A coffee for me, please.", "chaw meeng kah feh", True, True)
    assert restore("coffee-v2", cfg, ledger=ledger(cfg))["recall"] is True
    assert len(ledger(cfg).learner_state(cfg.learner_id)[1]) == 1
    path.write_text(json.dumps({"focusPhrases": [dict(base, id="coffee2", l2="Cho tôi cà phê.",
                                                      units=["Cho tôi", "cà phê"])]}))
    assert [r["id"] for r in runtime_curriculum(cfg, additions=ledger(cfg).learner_state(cfg.learner_id)[1])] == [
        "coffee2"]


def test_retired_greetings_and_closings_are_never_placed(cfg):
    bookends = [r for r in runtime_curriculum(cfg) if r.get("category") in ("greeting", "opener", "bookend", "closing")]
    assert bookends
    for row in bookends:
        retire(row["id"], cfg, ledger=ledger(cfg))
    hidden = {r["ledger_identity"] for r in bookends}
    for seed in range(20):
        plan = compile("Source.", replace(cfg, closer_chance=1.0), ledger=ledger(cfg), now=NOW, seed=seed,
                       greeting=True)
        assert not hidden & {lesson.phrase_key for lesson in plan.lessons}


def test_marks_survive_learned_and_learned_ranks_the_same_marked_or_not(cfg, tmp_path):
    row = shipped(cfg)
    set_mode(row["id"], "recall", cfg, ledger=ledger(cfg))
    stored = learned(row["id"], cfg, ledger=ledger(cfg))
    assert stored["recall"] is True and stored["landed"] is True and "learner_phrase" not in stored
    plain = profile(state_dir=str(tmp_path / "plain"))
    learned(row["id"], plain, ledger=ledger(plain))
    runtime = {r["id"]: r for r in runtime_curriculum(cfg, additions=ledger(cfg).learner_state(cfg.learner_id)[1])}
    unmarked = {r["id"]: r for r in runtime_curriculum(plain, additions=ledger(plain).learner_state("liam")[1])}
    assert {k: v for k, v in runtime[row["id"]].items() if k != "recall"} == unmarked[row["id"]]
    assert {p["id"]: p for p in phrases(cfg, ledger=ledger(cfg))}[row["id"]]["landed"] is True


def test_legacy_rows_stay_learner_phrases_and_keys_are_nfc(cfg):
    import unicodedata

    row = shipped(cfg)
    legacy = {k: v for k, v in row.items() if k != "ledger_identity"}
    [merged] = [r for r in runtime_curriculum(cfg, additions=[legacy]) if r["id"] == row["id"]]
    assert merged["learner_phrase"] is True
    decomposed = " " + unicodedata.normalize("NFD", row["ledger_identity"]) + " "
    assert set_mode(decomposed, "recall", cfg, ledger=ledger(cfg))["id"] == row["id"]


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
    identity = next(p["ledger_identity"] for p in listed if p["id"] == key)
    assert main(["mode", identity, "learning", *args]) == 0
    assert "recall" not in json.loads(capsys.readouterr().out)
    assert main(["retire", key, *args]) == 0
    assert json.loads(capsys.readouterr().out)["retired"] is True
    assert main(["phrases", "--retired", *args]) == 0
    assert [p["id"] for p in json.loads(capsys.readouterr().out)] == [key]
    assert main(["restore", identity, *args]) == 0
    assert "retired" not in json.loads(capsys.readouterr().out)
    assert main(["phrases", "--active", *args]) == 0
    assert len(json.loads(capsys.readouterr().out)) == len(listed)
    assert main(["retire", "missing", *args]) == 2


def _curriculum(tmp_path, **changes):
    base = {"id": "coffee", "l2": "Cho mình cà phê.", "l1": "Coffee, please.", "units": ["Cho mình", "cà phê"],
            "lesson_type": "connector"}
    path = tmp_path / "curriculum.json"
    path.write_text(json.dumps({"focusPhrases": [dict(base, **changes)] if changes.get("id") != "gone" else []}))
    return profile(state_dir=str(tmp_path), curriculum_path=str(path))


def test_rename_then_learned_keeps_one_stored_row_and_marks_stay_editable(tmp_path):
    cfg = _curriculum(tmp_path)
    set_mode("coffee", "recall", cfg, ledger=ledger(cfg))
    cfg = _curriculum(tmp_path, id="coffee-v2")
    learned("coffee-v2", cfg, ledger=ledger(cfg))
    assert [r["id"] for r in ledger(cfg).learner_state(cfg.learner_id)[1]] == ["coffee-v2"]
    set_mode("coffee-v2", "learning", cfg, ledger=ledger(cfg))
    retire("coffee-v2", cfg, ledger=ledger(cfg))
    [row] = phrases(cfg, ledger=ledger(cfg))
    assert (row["id"], row["mode"], row["retired"], row["landed"]) == ("coffee-v2", "learning", True, True)
    restore("coffee-v2", cfg, ledger=ledger(cfg))
    assert phrases(cfg, ledger=ledger(cfg))[0]["retired"] is False


def test_learn_re_adds_a_marked_phrase_that_stopped_shipping(tmp_path):
    cfg = _curriculum(tmp_path)
    retire("coffee", cfg, ledger=ledger(cfg))
    cfg = _curriculum(tmp_path, id="gone")
    assert phrases(cfg, ledger=ledger(cfg)) == []
    row = learn("Cho mình cà phê.", "Coffee again.", cfg, now=NOW, ledger=ledger(cfg))
    assert row["id"].startswith("learn-") and row["units"] == ["Cho mình cà phê."] and "retired" not in row
    [listed] = phrases(cfg, ledger=ledger(cfg))
    assert (listed["translation"], listed["learner_phrase"]) == ("Coffee again.", True)
    assert [r["id"] for r in ledger(cfg).learner_state(cfg.learner_id)[1]] == [row["id"]]


def test_marks_leave_plans_of_lanes_that_ignore_recall_unchanged(cfg, tmp_path):
    plain = profile(state_dir=str(tmp_path / "plain"))
    for row in runtime_curriculum(cfg):
        set_mode(row["id"], "recall", cfg, ledger=ledger(cfg))
    other = shipped(cfg, 2)
    retire(other["id"], cfg, ledger=ledger(cfg))
    restore(other["id"], cfg, ledger=ledger(cfg))
    assert getattr(cfg, "recall", False) is False
    for seed in (None, *range(5)):  # None: the RNG is seeded from the plan identity
        for greeting in (False, True):
            marked = compile("Source.\n\nMore.", cfg, ledger=ledger(cfg), now=NOW, seed=seed, greeting=greeting)
            unmarked = compile("Source.\n\nMore.", plain, ledger=ledger(plain), now=NOW, seed=seed,
                               greeting=greeting)
            assert marked.to_dict() == unmarked.to_dict()
    # Round trips back to a never-marked ledger.
    for row in runtime_curriculum(cfg):
        set_mode(row["id"], "learning", cfg, ledger=ledger(cfg))
    assert ledger(cfg).learner_state(cfg.learner_id)[1] == []


def test_marks_follow_a_shipped_text_fix_that_keeps_the_id(tmp_path):
    cfg = _curriculum(tmp_path)
    retire("coffee", cfg, ledger=ledger(cfg))
    set_mode("coffee", "recall", cfg, ledger=ledger(cfg))
    cfg = _curriculum(tmp_path, l2="Cho mình ly cà phê.", units=["Cho mình", "ly cà phê"])
    [row] = phrases(cfg, ledger=ledger(cfg))
    assert (row["phrase"], row["retired"], row["mode"]) == ("Cho mình ly cà phê.", True, "recall")
    restore("coffee", cfg, ledger=ledger(cfg))
    retire("coffee", cfg, ledger=ledger(cfg))
    [stored] = ledger(cfg).learner_state(cfg.learner_id)[1]
    assert stored["l2"] == "Cho mình ly cà phê." and stored["retired"] is True
    assert phrases(cfg, ledger=ledger(cfg))[0]["retired"] is True
    assert not due(cfg, ledger=ledger(cfg), now=NOW + timedelta(days=60))


def test_learn_after_a_text_fix_owns_the_marked_phrase_without_duplicates(tmp_path):
    cfg = _curriculum(tmp_path)
    set_mode("coffee", "recall", cfg, ledger=ledger(cfg))
    cfg = _curriculum(tmp_path, l2="Cho mình ly cà phê.", units=["Cho mình", "ly cà phê"])
    row = learn("Cho mình ly cà phê.", "A coffee, please.", cfg, now=NOW, ledger=ledger(cfg))
    assert (row["id"], row["recall"], "learner_phrase" in row) == ("coffee", True, False)
    [stored] = ledger(cfg).learner_state(cfg.learner_id)[1]
    assert stored["l2"] == "Cho mình ly cà phê." and stored["units"] == ["Cho mình", "ly cà phê"]
    [listed] = phrases(cfg, ledger=ledger(cfg))
    assert (listed["learner_phrase"], listed["mode"], listed["translation"]) == (True, "recall", "A coffee, please.")


def test_learning_the_old_text_of_a_retexted_marked_phrase_keeps_its_marks(tmp_path):
    cfg = _curriculum(tmp_path)
    retire("coffee", cfg, ledger=ledger(cfg))
    cfg = _curriculum(tmp_path, l2="Cho mình ly cà phê.", units=["Cho mình", "ly cà phê"])
    added = learn("Cho mình cà phê.", "Coffee.", cfg, now=NOW, ledger=ledger(cfg))
    assert added["id"].startswith("learn-")
    listed = {p["id"]: p for p in phrases(cfg, ledger=ledger(cfg))}
    assert listed["coffee"]["retired"] is True and listed[added["id"]]["retired"] is False
    assert sorted(r["id"] for r in ledger(cfg).learner_state(cfg.learner_id)[1]) == sorted(["coffee", added["id"]])
    # Marks on the new learner phrase go to its own row, never to the shipped phrase's stub.
    set_mode(added["id"], "recall", cfg, ledger=ledger(cfg))
    listed = {p["id"]: p for p in phrases(cfg, ledger=ledger(cfg))}
    assert (listed[added["id"]]["mode"], listed["coffee"]["mode"], listed["coffee"]["retired"]) == (
        "recall", "learning", True)
