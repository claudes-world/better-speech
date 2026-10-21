import json

import pytest

from better_speech import Plan, compile, render
from tests.test_model_free_audio import EmptyLedger, ToneProvider, profile


@pytest.mark.parametrize("byte_limit", [4500, 4])
def test_nfc_executed_source_spans_match_speech_after_roundtrip(tmp_path, byte_limit):
    plan = compile("Cafe\u0301.", profile(enabled=False), ledger=EmptyLedger())
    provider = ToneProvider()
    provider.max_request_bytes = byte_limit
    result = render(plan, {"google": provider}, tmp_path)
    assert result.audio_path and result.status == "english", result.diagnostics
    executed = Plan.from_dict(json.loads(json.dumps(result.plan.to_dict())))
    assert all(c.text == executed.speech_text[c.source_start:c.source_end] for c in executed.clips)
    assert "".join(c.text for c in executed.clips) == executed.speech_text
    assert executed.clips[0].source_start == 0
    assert executed.clips[-1].source_end == len(executed.speech_text)
    assert all(a.source_end == b.source_start for a, b in zip(executed.clips, executed.clips[1:]))


@pytest.mark.parametrize("unavailable", ["missing", "invalid_toml"])
def test_unavailable_profile_refuses_learner_writes(tmp_path, monkeypatch, unavailable):
    from better_speech.cli import main

    monkeypatch.setenv("XDG_DATA_HOME", str(tmp_path / "default"))
    path = tmp_path / "alice.toml"
    if unavailable == "invalid_toml":
        path.write_text('learner_id="alice"\nstate_dir="alice-state"\nmodel = none\n')
    assert main(["learn", "Học tiếp nhé.", "--gloss", "Keep learning.", "--profile", str(path)]) == 2
    assert main(["learned", "cam-on-nha", "--profile", str(path)]) == 2
    assert not (tmp_path / "default").exists()
    assert not (tmp_path / "alice-state").exists()


def test_executed_blank_span_rerender_preserves_plan_identity_and_audio(tmp_path):
    from dataclasses import replace

    from better_speech.curriculum import runtime_curriculum

    class SeenLedger(EmptyLedger):
        def snapshot(self, *args, **kwargs):
            return {r["ledger_identity"]: {"dueAt": "2099-01-01T00:00:00Z"}
                    for r in runtime_curriculum(profile())}

    def choose(payload, **kwargs):
        return json.dumps({"placements": [
            {"phraseId": payload["inventory"][0]["id"], "slotId": "end:6"},
            {"phraseId": payload["inventory"][1]["id"], "slotId": "start:7"},
        ]})

    plan = compile("# First.\n\nSecond.", replace(profile(), model="fake"),
                   ledger=SeenLedger(), model_call=choose)
    assert [c.pause_after_s for c in plan.clips if not c.text.strip()] == [0]
    provider = ToneProvider()
    providers = {"google": provider, "elevenlabs": provider}
    first = render(plan, providers, tmp_path)
    assert first.status == "bilingual" and first.audio_path, first.diagnostics
    assert [c.pause_after_s for c in first.plan.clips if not c.text.strip()] == [0.25]
    stored = first.plan
    for _ in range(2):
        restored = Plan.from_dict(json.loads(json.dumps(stored.to_dict())))
        again = render(restored, providers, tmp_path)
        assert again.status == "bilingual" and again.audio_path, again.diagnostics
        assert again.plan == first.plan
        assert again.plan.executed_digest == first.plan.executed_digest
        assert again.artifact_identity == first.artifact_identity
        assert again.audio_sha256 == first.audio_sha256
        assert again.audio_path.read_bytes() == first.audio_path.read_bytes()
        stored = again.plan
