"""0.2.18 polyglot cue voice and recall mode (PLAN.md section 4)."""

import json
from dataclasses import replace

from better_speech import compile
from better_speech.planner import LessonPlacement, sequence_clips
from better_speech.types import Profile, Voice, canonical_json
from tests.test_model_free_audio import EmptyLedger, profile
from tests.test_plan_identity_v0217 import BASELINE_DEFAULT, NOW, corpus_digest

CUE = Voice("en-US-Chirp3-HD-Leda", "google", "chirp3", 1.0, "mp3")
MAIN = Voice("narrator", "google", "neural2", 1.05)
LEAD, ECHO = Voice("lead"), Voice("echo")
RECORD = {
    "id": "coffee",
    "lesson_type": "connector",
    "phrase": "Cho mình cà phê.",
    "translation": "Coffee, please.",
    "units": ["Cho mình", "cà phê"],
}


def spoken(clips):
    return [(c.purpose, c.text, c.voice, c.pause_after_s) for c in clips if c.purpose != "source"]


def sequence(record, *, level=1, cue=None, **settings):
    clips, reason = sequence_clips(
        "Done.", inventory=[record], placements=[LessonPlacement("coffee", 5, "end", LEAD, ECHO, level, "open")],
        main=MAIN, cue=cue, settings={"repeatPauseS": 1.3, "pauseS": 0.7, **settings},
    )
    assert reason is None
    return spoken(clips)


def test_explicit_default_cue_and_recall_fields_keep_v0217_plans():
    explicit = replace(profile(), cue=None, recall=False, recall_pause_s=1.5)
    assert corpus_digest(explicit) == BASELINE_DEFAULT


def test_recall_marks_are_ignored_unless_the_lane_opts_in():
    marked = dict(RECORD, recall=True)
    learning = [("lesson", "Cho mình, cà phê.", "lead", 0.25), ("translation", "Coffee, please.", "narrator", 1.3),
                ("lesson", "Cho mình, cà phê.", "echo", 0.25)]
    assert sequence(marked) == sequence(RECORD) == learning
    assert sequence(marked, recall=False) == learning
    assert sequence(RECORD, recall=True) == learning


def test_recall_plays_cue_then_pause_then_vietnamese_once():
    assert sequence(dict(RECORD, recall=True), recall=True, cue=CUE, recallPauseS=1.5) == [
        ("translation", "Coffee, please.", CUE.voice, 1.5),
        ("lesson", "Cho mình, cà phê.", "lead", 0.7),
    ]
    # Without a cue voice the narrator reads the cue; landed (level 2) rows follow the same order.
    assert sequence(dict(RECORD, recall=True), recall=True, level=2) == [
        ("translation", "Coffee, please.", "narrator", 1.5),
        ("lesson", "Cho mình, cà phê.", "lead", 0.7),
    ]


def test_cue_voice_reads_english_in_learning_mode_too():
    assert sequence(RECORD, cue=CUE) == [
        ("lesson", "Cho mình, cà phê.", "lead", 0.25), ("translation", "Coffee, please.", CUE.voice, 1.3),
        ("lesson", "Cho mình, cà phê.", "echo", 0.25),
    ]


def test_compile_routes_cue_and_recall_through_the_profile():
    rows = []

    class MarkedLedger(EmptyLedger):
        def learner_state(self, learner_id, **kwargs):
            return {}, rows

    from better_speech.curriculum import runtime_curriculum

    shipped = [r for r in runtime_curriculum(Profile()) if r.get("category") is None]
    rows.extend({k: v for k, v in r.items() if k != "ledger_identity"} | {"recall": True} for r in shipped)
    cfg = replace(profile(), cue=CUE, recall=True, closer_chance=0.0)
    plan = compile("Source.", cfg, ledger=MarkedLedger(), now=NOW, seed=1)
    lessons = [c for c in plan.clips if c.purpose in ("lesson", "translation")]
    assert plan.status == "bilingual" and plan.lessons, plan.diagnostics
    assert [c.purpose for c in lessons] == ["translation", "lesson"]
    assert lessons[0].voice == CUE.voice and lessons[0].pause_after_s == 1.5
    assert all(c.voice == profile().english.voice for c in plan.clips if c.purpose == "source")
    assert plan.profile_digest != compile("Source.", profile(), ledger=EmptyLedger(), now=NOW).profile_digest
    # The same marks on a lane without recall keep the learning order.
    off = compile("Source.", replace(cfg, recall=False), ledger=MarkedLedger(), now=NOW, seed=1)
    assert [c.purpose for c in off.clips if c.purpose in ("lesson", "translation")][:2] == ["lesson", "translation"]


def test_invalid_cue_voice_falls_back_to_narrator_and_keeps_lessons():
    from better_speech.config import resolve_profile

    bad = resolve_profile(replace(profile(), cue=Voice("", "google", "chirp3", 1.0, "mp3")))
    assert bad.cue is None and bad.enabled and "invalid_cue_voice" in bad.config_diagnostics
    assert resolve_profile(replace(profile(), recall_pause_s=-1)).enabled is False
    assert resolve_profile(replace(profile(), recall="yes")).enabled is False


def test_profile_round_trip_keeps_cue_voice():
    cfg = replace(profile(), cue=CUE, recall=True, recall_pause_s=2.0)
    assert Profile.from_dict(json.loads(canonical_json(cfg.to_dict()))) == cfg


def test_recall_pause_setting_reaches_the_cue_clip():
    rows = []

    class MarkedLedger(EmptyLedger):
        def learner_state(self, learner_id, **kwargs):
            return {}, rows

    from better_speech.curriculum import runtime_curriculum

    rows.extend({k: v for k, v in r.items() if k != "ledger_identity"} | {"recall": True}
                for r in runtime_curriculum(Profile()))
    cfg = replace(profile(), recall=True, recall_pause_s=2.0, closer_chance=0.0)
    plan = compile("Source.", cfg, ledger=MarkedLedger(), now=NOW, seed=1)
    cue = next(c for c in plan.clips if c.purpose == "translation")
    assert cue.pause_after_s == 2.0 and cue.voice == profile().english.voice


def test_recall_stays_echo_free_when_learning_would_force_an_echo():
    pending = dict(RECORD, recall=True, pending_learning=True)
    assert sequence(pending, recall=True, repeat=False, recallPauseS=1.5) == [
        ("translation", "Coffee, please.", "narrator", 1.5),
        ("lesson", "Cho mình, cà phê.", "lead", 0.7),
    ]
    # Without a translation a recall item is just the Vietnamese.
    assert sequence(dict(RECORD, recall=True, translation=""), recall=True) == [
        ("lesson", "Cho mình, cà phê.", "lead", 0.7),
    ]


def test_invalid_recall_pause_setting_falls_back_to_plain_source():
    for bad in (-1, float("nan"), "1"):
        clips, reason = sequence_clips(
            "Done.", inventory=[dict(RECORD, recall=True)], main=MAIN, settings={"recall": True, "recallPauseS": bad},
            placements=[LessonPlacement("coffee", 5, "end", LEAD, ECHO, 1, "open")],
        )
        assert reason.startswith("invalid optional") and [c.purpose for c in clips] == ["source"]


def test_switch_constants_cover_the_open_questions(monkeypatch):
    import better_speech.planner as planner

    monkeypatch.setattr(planner, "CUE_IN_LEARNING", False)
    assert sequence(RECORD, cue=CUE)[1][2] == "narrator"
    assert sequence(dict(RECORD, recall=True), recall=True, cue=CUE)[0][2] == CUE.voice
    monkeypatch.setattr(planner, "RECALL_ECHO", True)
    assert sequence(dict(RECORD, recall=True), recall=True, recallPauseS=1.5) == [
        ("translation", "Coffee, please.", "narrator", 1.5),
        ("lesson", "Cho mình, cà phê.", "lead", 1.3),
        ("lesson", "Cho mình, cà phê.", "echo", 0.25),
    ]


def test_single_recall_pause_constant():
    from better_speech import compiler, planner
    from better_speech.types import RECALL_PAUSE_S

    assert Profile().recall_pause_s == planner.DEFAULTS["recallPauseS"] == RECALL_PAUSE_S
    assert compiler._LANE_CONTROL_DEFAULTS["recall_pause_s"] == RECALL_PAUSE_S


def test_malformed_cue_object_drops_only_the_cue():
    from better_speech.config import resolve_profile

    loaded = resolve_profile(Profile.from_dict({**profile().to_dict(), "cue": {"voice": "x", "bogus": 1}}))
    assert loaded.enabled and loaded.cue is None and "invalid_cue_voice" in loaded.config_diagnostics
