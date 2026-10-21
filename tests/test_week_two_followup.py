import random
import unicodedata
from dataclasses import replace
from datetime import datetime, timedelta

import pytest

from better_speech import Voice, compile, record_delivered
from better_speech.config import load_profile, resolve_profile
from better_speech.curriculum import runtime_curriculum, validate_inventory
from better_speech.learning import due, learn, learned
from better_speech.ledger import Ledger
from better_speech.voices import choose_lesson_voices
from tests.test_model_free_audio import EmptyLedger, profile


def test_example_echo_pool_excludes_weight_one_and_keeps_variety():
    cfg = load_profile("examples/profile.toml")
    echoes, leads = set(), set()
    for seed in range(2000):
        choice = choose_lesson_voices(
            main=cfg.english, pool=cfg.voices,
            weights={v.voice: v.weight for v in cfg.voices}, random=random.Random(seed).random,
        )
        echoes.add(choice.echo.voice)
        leads.add(choice.lead.voice)
    assert echoes == {v.voice for v in cfg.voices if v.weight > 1}
    assert len(echoes) == 7
    assert leads == {v.voice for v in cfg.voices}


@pytest.mark.parametrize("weight", [0, 0.5, 1])
def test_small_echo_pool_never_reuses_low_weight_lead(weight):
    pool = (Voice("low", weight=weight), Voice("high", weight=2))
    choice = choose_lesson_voices(
        main=profile().english, pool=pool, weights={v.voice: v.weight for v in pool}, random=lambda: 0,
    )
    assert choice.echo.voice == "high"
    assert choice.lead.voice == ("low" if weight else "high")


def test_no_eligible_echo_preserves_lead_without_a_low_weight_repeat():
    cfg = replace(profile(), voices=(Voice("low", weight=1),))
    plan = compile("Note.", cfg, ledger=EmptyLedger())
    assert plan.lessons
    assert [c.voice for c in plan.clips if c.language == "vi"] == ["low"]
    assert not any(c.echo for c in plan.clips)


def test_polite_bill_phrase_and_metadata():
    row = next(r for r in runtime_curriculum(profile()) if r["id"] == "cho-minh-tinh-tien-a")
    assert row["l2"] == "Cho mình tính tiền ạ"
    assert row["l1"] == "The bill, please"
    assert 0 < len(row["hint"]) < 60
    assert row["l1_literal"][-1] == ["ạ", "[polite]"]
    assert row["units"] == [row["l2"]]
    assert row["lesson_type"] == "learning_sentence"
    validate_inventory([row])


def test_existing_landed_learner_bill_phrase_wins_without_duplicate_scheduling(tmp_path):
    cfg = profile()
    phrase = "Cho mình tính tiền ạ"
    ledger = Ledger(tmp_path / "ledger.db")
    ledger.edit_phrases(cfg.learner_id, lambda rows: rows.append({
        "id": "learn-80c0c3193294e6c5", "l2": unicodedata.normalize("NFD", phrase),
        "l1": "The bill, please", "landed": True, "pending_learning": False,
    }))
    additions = ledger.learner_state(cfg.learner_id)[1]
    rows = runtime_curriculum(cfg, additions=additions)
    matches = [r for r in rows if r["ledger_identity"] == phrase]
    assert len(matches) == 1
    assert matches[0]["id"] == "learn-80c0c3193294e6c5"
    assert matches[0]["landed"]

    class DueBill(EmptyLedger):
        def learner_state(self, *args, **kwargs):
            return {r["ledger_identity"]: {"dueAt": "2020-01-01" if r in matches else "2099-01-01"}
                    for r in rows}, additions

    plan = compile("Note.", cfg, ledger=DueBill(), now="2026-10-21T12:00:00Z")
    assert [lesson.id for lesson in plan.lessons] == ["learn-80c0c3193294e6c5"]
    assert [c.phrase_key for c in plan.clips if c.language == "vi"] == [phrase]


@pytest.mark.parametrize("zone,start,midnight", [
    ("Asia/Ho_Chi_Minh", "2026-10-21T12:00:00+00:00", "2026-10-21T17:00:00+00:00"),
    ("America/New_York", "2026-11-01T04:30:00+00:00", "2026-11-02T05:00:00+00:00"),
    ("UTC", "2026-10-21T12:00:00+00:00", "2026-10-22T00:00:00+00:00"),
])
def test_boost_caps_confirmed_deliveries_resets_at_local_midnight_and_ends_when_landed(
    tmp_path, zone, start, midnight,
):
    cfg = profile(timezone=zone)
    ledger = Ledger(tmp_path / "ledger.db")
    now, next_day = datetime.fromisoformat(start), datetime.fromisoformat(midnight)
    row = learn("Học tiếp nhé.", "Keep learning.", cfg, ledger=ledger, now=now)
    for index in range(3):
        # Replanning or acknowledging an unexecuted plan never consumes the cap.
        for _ in range(2):
            plan = compile("Note.", cfg, ledger=ledger, now=now)
            assert [lesson.id for lesson in plan.lessons] == [row["id"]]
            assert record_delivered(plan, "unexecuted", now, ledger=ledger).added == 0
        executed = replace(plan, executed=True)
        assert sum(c.echo for c in executed.clips) == 1
        for expected in (1, 0):
            receipt = record_delivered(executed, str(index), now, ledger=ledger)
            assert receipt.added == expected and not receipt.diagnostics
    # Reloading the ledger retains the count; echoes and replays counted once.
    ledger = Ledger(ledger.path)
    fourth = compile("Note.", cfg, ledger=ledger, now=now)
    assert fourth.lessons and row["id"] not in [lesson.id for lesson in fourth.lessons]
    assert row["id"] not in [r["id"] for r in due(cfg, ledger=ledger, now=now)]
    before_midnight = compile("Note.", cfg, ledger=ledger, now=next_day - timedelta(milliseconds=1))
    assert row["id"] not in [lesson.id for lesson in before_midnight.lessons]
    tomorrow = compile("Note.", cfg, ledger=ledger, now=next_day)
    assert [lesson.id for lesson in tomorrow.lessons] == [row["id"]]
    assert row["id"] in [r["id"] for r in due(cfg, ledger=ledger, now=next_day)]
    assert record_delivered(replace(tomorrow, executed=True), "tomorrow", next_day, ledger=ledger).added == 1
    learned(row["id"], cfg, ledger=ledger)
    landed = compile("Note.", cfg, ledger=ledger, now=next_day)
    assert landed.lessons and row["id"] not in [lesson.id for lesson in landed.lessons]
    assert row["id"] not in [r["id"] for r in due(cfg, ledger=ledger, now=next_day)]


@pytest.mark.parametrize("cap", [0, 1, 2])
def test_configurable_cap_and_contextual_selection_cannot_bypass_it(tmp_path, cap):
    cfg = replace(profile(boost_daily_cap=cap), model="fake", heteronyms=False)
    ledger = Ledger(tmp_path / "ledger.db")
    now = datetime.fromisoformat("2026-10-21T12:00:00+00:00")
    row = learn("Được rồi.", "All set.", cfg, ledger=ledger, now=now)
    offered = []

    def choose(payload, **kwargs):
        offered.extend(r["id"] for r in payload["inventory"])
        return '{"placements":[]}'

    for index in range(cap):
        plan = compile("First.\n\nSecond.", cfg, ledger=ledger, now=now, model_call=choose)
        assert row["id"] in [lesson.id for lesson in plan.lessons]
        assert record_delivered(replace(plan, executed=True), str(index), now, ledger=ledger).added == 1
    offered.clear()
    after = compile("First.\n\nSecond.", cfg, ledger=ledger, now=now, model_call=choose)
    assert offered and row["id"] not in offered
    assert after.lessons and row["id"] not in [lesson.id for lesson in after.lessons]


def test_daily_count_is_per_phrase_per_learner_and_replays_chronologically(tmp_path):
    cfg = profile(boost_daily_cap=1)
    ledger = Ledger(tmp_path / "ledger.db")
    now = datetime.fromisoformat("2026-10-21T12:00:00+00:00")
    first = learn("Học nữa.", "Learn more.", cfg, ledger=ledger, now=now)
    plan = replace(compile("Note.", cfg, ledger=ledger, now=now), executed=True)
    # An acknowledgement for yesterday arrives after today's confirmed hearing.
    for name, at in [("today", now), ("yesterday", now - timedelta(days=1))]:
        assert record_delivered(plan, name, at, ledger=ledger).added == 1
    other = replace(cfg, learner_id="other")
    same = learn("Học nữa.", "Learn more.", other, ledger=ledger, now=now)
    assert compile("Note.", other, ledger=ledger, now=now).lessons[0].id == same["id"]
    second = learn("Học tiếp nhé.", "Keep learning.", cfg, ledger=ledger, now=now)
    chosen = compile("Note.", cfg, ledger=ledger, now=now)
    assert chosen.lessons[0].id == second["id"] != first["id"]
    records, _ = ledger.learner_state(cfg.learner_id, now=now.isoformat(), timezone=cfg.timezone)
    assert records[plan.lessons[0].phrase_key]["dailyCount"] == 1


@pytest.mark.parametrize("value", [-1, True, 1.5, "3", None])
def test_invalid_daily_cap_fails_open(value):
    cfg = resolve_profile(profile(boost_daily_cap=value))
    assert not cfg.enabled and "invalid_profile" in cfg.config_diagnostics


def test_daily_cap_loads_from_profile(tmp_path):
    path = tmp_path / "profile.toml"
    path.write_text('model="none"\nboost_daily_cap=2\ntimezone="America/New_York"\n')
    cfg = load_profile(path)
    assert cfg.enabled and cfg.boost_daily_cap == 2 and cfg.timezone == "America/New_York"


def test_greeting_cannot_reintroduce_a_capped_learner_phrase(tmp_path):
    cfg = profile(boost_daily_cap=1)
    ledger = Ledger(tmp_path / "ledger.db")
    now = datetime.fromisoformat("2026-10-21T12:00:00+00:00")
    row = learn("Xin chào!", "Hello!", cfg, ledger=ledger, now=now)
    plans = (compile("Note.", cfg, ledger=ledger, now=now, greeting=True, seed=seed) for seed in range(20))
    first = next(plan for plan in plans if row["id"] in [lesson.id for lesson in plan.lessons])
    assert record_delivered(replace(first, executed=True), "hello", now, ledger=ledger).added == len(first.lessons)
    # Confirm another greeting so last-greeting rotation alone cannot hide Xin chào.
    later = now + timedelta(minutes=1)
    other = compile("Note.", cfg, ledger=ledger, now=later, greeting=True)
    assert record_delivered(replace(other, executed=True), "other", later, ledger=ledger).added == len(other.lessons)
    for seed in range(20):
        next_plan = compile("Note.", cfg, ledger=ledger, now=later, greeting=True, seed=seed)
        assert row["id"] not in [lesson.id for lesson in next_plan.lessons]
