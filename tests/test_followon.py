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
