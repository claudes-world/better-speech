"""Lane controls (0.2.13): closer chance, paragraph slots, end-of-note lesson."""

from dataclasses import replace
from datetime import datetime, timezone

import pytest

from better_speech import compile
from better_speech.config import resolve_profile
from better_speech.ledger import Ledger
from tests.test_model_free_audio import profile

NOW = datetime(2026, 10, 21, 14, tzinfo=timezone.utc)
CLOSING = ("closing", "bookend")


def closers(plan):
    return [r for r in plan.lessons if r.record.get("category") in CLOSING and r.record["lesson_type"] == "learning_sentence"]


def end_lessons(plan):
    return [r for r in plan.lessons if r.record.get("category") not in (*CLOSING, "greeting", "opener")
            and r.record["lesson_type"] == "learning_sentence"]


def test_defaults_keep_plan_identity(tmp_path):
    cfg = profile()
    base = compile("Source.", cfg, ledger=Ledger(tmp_path / "ledger"), now=NOW)
    explicit = replace(cfg, closer_chance=0.5, paragraph_start_lessons=True, paragraph_end_lessons=True, end_lesson=True)
    same = compile("Source.", explicit, ledger=Ledger(tmp_path / "ledger"), now=NOW)
    assert same.identity == base.identity and same.profile_digest == base.profile_digest
    changed = compile("Source.", replace(cfg, closer_chance=0.0), ledger=Ledger(tmp_path / "ledger"), now=NOW)
    assert changed.profile_digest != base.profile_digest


@pytest.mark.parametrize("chance,expected", [(0.0, 0), (1.0, 1)])
def test_closer_chance_bounds(tmp_path, chance, expected):
    cfg = replace(profile(), closer_chance=chance)
    for seed in range(20):
        plan = compile("Body.", cfg, ledger=Ledger(tmp_path / "ledger"), now=NOW, seed=seed)
        assert len(closers(plan)) == expected


def test_end_lesson_off_skips_learning_sentence(tmp_path):
    for seed in range(10):
        on = compile("Body.", profile(), ledger=Ledger(tmp_path / "ledger"), now=NOW, seed=seed)
        off = compile("Body.", replace(profile(), end_lesson=False, closer_chance=0.0),
                      ledger=Ledger(tmp_path / "ledger"), now=NOW, seed=seed)
        assert len(end_lessons(on)) == 1
        assert not end_lessons(off) and not off.lessons


@pytest.mark.parametrize("start,end,prefixes", [
    (True, True, {"start", "end"}), (False, True, {"end"}), (True, False, {"start"}), (False, False, set()),
])
def test_paragraph_flags_limit_offered_slots(tmp_path, start, end, prefixes):
    calls = []

    def model(payload, *, timeout):
        calls.append(payload)
        return '{"placements":[]}'

    cfg = replace(profile(), model="claude", paragraph_start_lessons=start, paragraph_end_lessons=end)
    compile("# One\n\nFirst paragraph.\n\n# Two\n\nSecond paragraph.", cfg, model_call=model,
            ledger=Ledger(tmp_path / "ledger"), now=NOW, seed=3)
    seen = {slot.split(":")[0] for call in calls for slot in call["slots"]}
    assert seen == prefixes


@pytest.mark.parametrize("field,value", [("closer_chance", 1.5), ("closer_chance", "often"), ("end_lesson", "yes")])
def test_invalid_control_disables_lessons(field, value):
    resolved = resolve_profile(replace(profile(), **{field: value}))
    assert not resolved.enabled and "invalid_profile" in resolved.config_diagnostics
