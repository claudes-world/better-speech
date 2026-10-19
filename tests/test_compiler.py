import json
from dataclasses import replace
from datetime import datetime, timezone
from importlib.resources import files

import pytest

from better_speech import Plan, compile
from better_speech.curriculum import runtime_curriculum
from better_speech.learning import learn, learned
from better_speech.models import REGISTRY, register_model
from better_speech.types import digest
from tests.test_model_free_audio import EmptyLedger, profile

NOW = datetime(2026, 10, 19, 12, tzinfo=timezone.utc)


def test_empty_model_response_keeps_highest_boosted_phrase(tmp_path):
    cfg = replace(profile(state_dir=str(tmp_path)), model="claude")
    item = learn("Tôi muốn học.", "I want to learn.", cfg, now=NOW)
    calls = []

    def model(payload, *, timeout):
        calls.append(payload)
        return '{"placements":[]}'

    plan = compile("First paragraph.\n\nSecond paragraph.", cfg, model_call=model, now=NOW)
    assert calls
    assert [v.id for v in plan.lessons] == [item["id"]]
    assert sum(c.language == "vi" for c in plan.clips) == 2


@pytest.mark.parametrize(
    "answer",
    [
        "{}",
        '{"placements":[{"phraseId":"made-up","slotId":"start:0"}]}',
        '{"placements":[{"phraseId":"hieu-roi","slotId":"start:0","text":"invented"}]}',
    ],
)
def test_bad_model_output_keeps_deterministic_lesson(tmp_path, answer):
    cfg = replace(profile(state_dir=str(tmp_path)), model="claude")
    plan = compile("Original source.", cfg, model_call=lambda *a, **k: answer, now=NOW)
    assert plan.status == "bilingual" and len(plan.lessons) == 1
    assert any(d.stage == "model" for d in plan.diagnostics)
    assert "".join(c.text for c in plan.clips if c.purpose == "source") == plan.speech_text


def test_fourth_adapter_registration_without_compiler_changes(tmp_path, monkeypatch):
    calls = []

    class FakeAdapter:
        def __init__(self, cfg):
            pass

        def __call__(self, payload, *, timeout):
            calls.append(payload)
            row = payload["inventory"][0]
            return json.dumps({"placements": [{"phraseId": row["id"], "slotId": payload["slots"][0]}]})

    monkeypatch.setattr("better_speech.models.REGISTRY", dict(REGISTRY))
    register_model("fourth", FakeAdapter)
    plan = compile("The update is ready.", replace(profile(state_dir=str(tmp_path)), model="fourth"), now=NOW)
    assert calls and len(plan.lessons) == 2, plan.diagnostics


def test_failed_adapter_does_not_try_another(tmp_path, monkeypatch):
    calls = []

    def factory(cfg):
        def call(*args, **kwargs):
            calls.append(cfg.model)
            raise FileNotFoundError()

        return call

    monkeypatch.setattr(
        "better_speech.models.REGISTRY", {name: factory for name in ("claude", "codex", "agy")}
    )
    result = compile("An update.", replace(profile(state_dir=str(tmp_path)), model="codex"), now=NOW)
    assert calls == ["codex"]
    assert len(result.lessons) == 1


def test_identity_exact_contract_and_json_round_trip(tmp_path):
    cfg = profile(state_dir=str(tmp_path))
    first = compile("Source.", cfg, now=NOW)
    assert first.identity == digest(
        [cfg.learner_id, first.curriculum_digest, "Source.", first.profile_digest]
    )
    assert Plan.from_dict(json.loads(json.dumps(first.to_dict()))) == first
    other = compile("Source.", replace(cfg, learner_id="other"), now=NOW)
    assert first.identity != other.identity
    learn("Cảm ơn.", "Thanks.", cfg, now=NOW)
    changed = compile("Source.", cfg, now=NOW)
    assert first.identity != changed.identity


def test_learning_echo_survives_boost_expiry_until_landed(tmp_path):
    cfg = profile(state_dir=str(tmp_path), repeat=False)
    row = learn("Cảm ơn.", "Thanks.", cfg, now=NOW)
    assert row["boost_until"] == "2026-10-19T17:00:00.000Z"
    plan = compile("Source.", cfg, now=NOW)
    assert sum(c.language == "vi" for c in plan.clips) == 2
    learned(row["id"], cfg)

    # Another unseen phrase can win after the boost is cleared; explicitly due it below.
    class DueLedger:
        def snapshot(self, *a, **k):
            return {"Cảm ơn.": {"dueAt": "2026-10-18T00:00:00.000Z"}}

    landed = compile("Source.", cfg, ledger=DueLedger(), now=NOW)
    assert landed.lessons[0].id == row["id"]
    assert sum(c.language == "vi" for c in landed.clips) == 1


def test_due_learner_phrase_reserved_over_fresh_sample(tmp_path):
    cfg = profile(state_dir=str(tmp_path))
    item = learn("Học nữa.", "Learn more.", cfg, now=NOW)

    class DueLedger:
        def snapshot(self, *a, **k):
            return {"Học nữa.": {"dueAt": "2026-10-20T00:00:00.000Z"}}

    plan = compile("Source.", cfg, ledger=DueLedger(), now="2026-10-21T00:00:00Z")
    assert plan.lessons[0].id == item["id"]


def test_long_multibyte_source_and_pacing_do_not_change_transcript(tmp_path):
    cfg = profile(state_dir=str(tmp_path))
    source = "界😀" * 3500
    plan = compile(source, cfg, ledger=EmptyLedger())
    assert "".join(c.text for c in plan.fallback_clips) == source
    assert all(len(c.text.encode()) <= 4500 for c in plan.fallback_clips)
    assert plan.source == source


def test_invalid_lesson_config_is_english_and_detached(tmp_path):
    cfg = profile(state_dir=str(tmp_path), pacing={"default": {"p0": " / "}})
    plan = compile("Source.", cfg)
    frozen = plan.profile_json
    cfg.pacing["default"]["p0"] = "changed"
    assert plan.profile_json == frozen
    broken = compile("Source.", replace(cfg, pause_s=-1))
    assert not broken.lessons and broken.diagnostics


@pytest.mark.parametrize(
    "item",
    json.loads(files("better_speech").joinpath("data/curriculum.vi.json").read_text())["focusPhrases"],
    ids=lambda item: item["id"],
)
def test_every_shipped_phrase_can_be_reserved_and_taught(item, tmp_path):
    cfg = profile(state_dir=str(tmp_path))

    class DueOnly:
        def snapshot(self, *a, **k):
            return {
                row["ledger_identity"]: {
                    "dueAt": "2026-10-18T00:00:00.000Z"
                    if row["id"] == item["id"]
                    else "2027-01-01T00:00:00.000Z"
                }
                for row in runtime_curriculum(cfg)
            }

    plan = compile("Original source.", cfg, ledger=DueOnly(), now=NOW)
    assert plan.status == "bilingual", plan.diagnostics
    assert [lesson.id for lesson in plan.lessons] == [item["id"]]
    assert all(c.provider == "elevenlabs" for c in plan.clips if c.language == "vi")
    assert "".join(c.text for c in plan.clips if c.purpose == "source") == plan.speech_text
