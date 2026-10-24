import json
from dataclasses import replace

import pytest

from better_speech import compile
from tests.test_model_free_audio import EmptyLedger, profile


@pytest.mark.parametrize('source,starts', [
    ('First.\n\nSecond.\n\nThird.', {0}),
    ('# Heading\n\nFirst.\n\nSecond.', {0, 8}),
    ('Intro.\n## Part\nBody.\n### Next\nTail.', {0, 12, 23}),
    ('#### Small\n\nBody.', {0}),
    ('```\n# Code\n```\n\nBody.', {0}),
    ('~~~\n# Code\n~~~\n\nBody.', {0}),
])
def test_offered_and_executed_opener_slots(source, starts):
    captured = []
    def select(payload, **kwargs):
        captured.append(payload)
        return json.dumps({'placements': [{'phraseId': payload['inventory'][0]['id'],
                                          'slotId': f'start:{max(starts)}'}]})
    plan = compile(source, replace(profile(), model='fake'), ledger=EmptyLedger(), model_call=select, seed=0)
    assert {int(s.split(':')[1]) for s in captured[0]['slots'] if s.startswith('start:')} == starts
    assert len([r for r in plan.lessons if r.record.get("category") not in ("closing", "bookend")]) == 2, plan.diagnostics
    assert ''.join(c.text for c in plan.clips if c.purpose == 'source') == plan.speech_text
    opener = plan.lessons[0]
    index = next(i for i, c in enumerate(plan.clips) if c.phrase_key == opener.phrase_key)
    assert sum(len(c.text) for c in plan.clips[:index] if c.purpose == 'source') == max(starts)


def test_body_paragraph_start_is_rejected_and_closers_remain():
    offered = []
    def select(payload, **kwargs):
        offered.extend(payload['slots'])
        return json.dumps({'placements': [{'phraseId': payload['inventory'][0]['id'], 'slotId': 'start:8'}]})
    plan = compile('First.\n\nSecond.', replace(profile(), model='fake'),
                   ledger=EmptyLedger(), model_call=select, seed=0)
    assert 'end:6' in offered and 'start:8' not in offered
    assert len([r for r in plan.lessons if r.record.get("category") not in ("closing", "bookend")]) == 1
    assert any(d.stage == 'model' for d in plan.diagnostics)


@pytest.mark.parametrize('source', ['# Status', 'Intro.\n\n## Status'])
def test_terminal_heading_opener_precedes_reserved_closing_lesson(source):
    def select(payload, **kwargs):
        return json.dumps({'placements': [{'phraseId': payload['inventory'][0]['id'],
            'slotId': max((s for s in payload['slots'] if s.startswith('start:')), key=lambda s: int(s.split(':')[1]))}]})
    plan = compile(source, replace(profile(), model='fake'), ledger=EmptyLedger(), model_call=select, seed=0)
    assert len([r for r in plan.lessons if r.record.get("category") not in ("closing", "bookend")]) == 2
    assert plan.lessons[-2].id == 'an-thoi'
    assert plan.lessons[-1].record['category'] == 'closing'
    assert plan.clips[-1].phrase_key == plan.lessons[-1].phrase_key


@pytest.mark.parametrize('fence,is_code', [('```bad`info', False), ('````bad`info', False),
                                         ('~~~bad`info', True)])
def test_invalid_backtick_info_keeps_prose_in_english_fallback(fence, is_code):
    from better_speech.markdown import markdown_to_speech_structure
    source = f'Before.\n{fence}\nKeep this spoken sentence.\n# Heading\nAfter.'
    speech, openers = markdown_to_speech_structure(source)
    assert ('Keep this spoken sentence.' in speech) is not is_code
    assert ('After.' in speech) is not is_code
    assert len(openers) == (1 if is_code else 2)
    plan = compile(source, profile(enabled=False), ledger=EmptyLedger())
    assert plan.source == source
    assert ''.join(c.text for c in plan.fallback_clips) == speech
