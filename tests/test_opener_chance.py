"""Opener chance (0.2.14): probability that a requested greeting is placed."""

from dataclasses import replace
from datetime import datetime, timezone

import pytest

from better_speech import compile
from better_speech.config import resolve_profile
from better_speech.ledger import Ledger
from tests.test_model_free_audio import profile

NOW = datetime(2026, 10, 21, 14, tzinfo=timezone.utc)
OPENING = ("greeting", "opener", "bookend")


def openers(plan):
    return [r for r in plan.lessons if r.record.get("category") in OPENING and r.record["lesson_type"] == "connector"]


def test_default_keeps_plan_identity_and_greeting(tmp_path):
    cfg = profile()
    base = compile("Source.", cfg, ledger=Ledger(tmp_path / "ledger"), now=NOW, greeting=True)
    same = compile("Source.", replace(cfg, opener_chance=1.0), ledger=Ledger(tmp_path / "ledger"), now=NOW, greeting=True)
    assert same.identity == base.identity and same.profile_digest == base.profile_digest
    assert [c.text for c in same.clips] == [c.text for c in base.clips]
    assert len(openers(base)) == 1
    changed = compile("Source.", replace(cfg, opener_chance=0.5), ledger=Ledger(tmp_path / "ledger"), now=NOW, greeting=True)
    assert changed.profile_digest != base.profile_digest


def test_zero_chance_never_opens(tmp_path):
    cfg = replace(profile(), opener_chance=0.0)
    for seed in range(20):
        plan = compile("Body.", cfg, ledger=Ledger(tmp_path / "ledger"), now=NOW, seed=seed, greeting=True)
        assert not openers(plan)


def test_half_chance_opens_some_notes(tmp_path):
    cfg = replace(profile(), opener_chance=0.5)
    counts = [len(openers(compile("Body.", cfg, ledger=Ledger(tmp_path / "ledger"), now=NOW, seed=seed, greeting=True)))
              for seed in range(40)]
    assert 0 < sum(counts) < 40


def test_chance_ignored_without_greeting(tmp_path):
    plan = compile("Body.", replace(profile(), opener_chance=1.0), ledger=Ledger(tmp_path / "ledger"), now=NOW)
    assert not openers(plan)


@pytest.mark.parametrize("value", [1.5, -0.1, "often", float("nan")])
def test_invalid_opener_chance_disables_lessons(value):
    resolved = resolve_profile(replace(profile(), opener_chance=value))
    assert not resolved.enabled and "invalid_profile" in resolved.config_diagnostics
