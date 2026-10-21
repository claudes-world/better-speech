from dataclasses import replace
from datetime import datetime, timedelta, timezone

import pytest

from better_speech import compile
from better_speech.ledger import Ledger, record_delivered
from better_speech.transcript import present
from tests.test_model_free_audio import profile

NOW = datetime(2026, 10, 21, 14, tzinfo=timezone.utc)


def greetings(plan):
    return [r for r in plan.lessons if r.record.get('category') == 'greeting']


@pytest.mark.parametrize('gap,expected', [(None, 0), (1.99, 0), (2, 1), (8, 1), (-1, 0)])
@pytest.mark.parametrize('text', ['Body without a heading.', '# Heading\n\nBody.'])
def test_greeting_gap_and_first_audible_clip(tmp_path, gap, expected, text):
    cfg = profile(timezone='America/New_York')
    last = NOW - timedelta(hours=gap) if gap is not None else None
    plan = compile(text, cfg, ledger=Ledger(tmp_path / 'ledger'), now=NOW, last_learner_message_at=last)
    assert len(greetings(plan)) == expected
    assert len(plan.lessons) == 1 + expected
    assert ''.join(c.text for c in plan.clips if c.purpose == 'source') == plan.speech_text
    if expected:
        greeting = greetings(plan)[0]
        assert plan.clips[0].phrase_key == greeting.phrase_key
        assert plan.clips[0].language == 'vi'
        assert greeting.record['time_of_day'] in ('morning', 'any')
        assert greeting.record['phrase'].rstrip('!') in present(replace(plan, executed=True))[1]


@pytest.mark.parametrize('zone,hour,period', [('UTC', 6, 'morning'), ('UTC', 15, 'afternoon'),
                                           ('UTC', 21, 'evening'), ('Asia/Ho_Chi_Minh', 2, 'morning')])
def test_greeting_profile_timezone(tmp_path, zone, hour, period):
    now = NOW.replace(hour=hour)
    for seed in range(12):
        plan = compile('Note.', profile(timezone=zone), ledger=Ledger(tmp_path / 'ledger'), now=now,
                       seed=seed, last_learner_message_at=(now - timedelta(hours=3)).isoformat())
        assert greetings(plan)[0].record['time_of_day'] in (period, 'any')


def test_rotation_is_per_learner_and_delivered_only(tmp_path):
    ledger = Ledger(tmp_path / 'ledger')
    cfg = profile()
    def prepare(cfg=cfg):
        return compile('Note.', cfg, ledger=ledger, now=NOW, seed=5,
                       last_learner_message_at=NOW - timedelta(hours=3))
    first = prepare()
    assert greetings(prepare()) == greetings(first)
    assert not record_delivered(replace(first, executed=True), 'first', NOW, ledger=ledger).diagnostics
    assert greetings(prepare())[0].phrase_key != greetings(first)[0].phrase_key
    assert greetings(prepare(replace(cfg, learner_id='other'))) == greetings(first)
    key = greetings(first)[0].phrase_key
    assert ledger.snapshot()[key]['lastGreetingKey'] == key


def test_custom_gap_and_invalid_gap_input_fail_open(tmp_path):
    cfg = profile(greeting_gap_hours=1)
    plan = compile('Note.', cfg, ledger=Ledger(tmp_path / 'ledger'), now=NOW,
                   last_learner_message_at=NOW - timedelta(hours=1))
    assert greetings(plan)
    invalid = compile('Note.', cfg, ledger=Ledger(tmp_path / 'ledger'), now=NOW,
                      last_learner_message_at='unknown')
    assert invalid.status == 'bilingual' and not greetings(invalid)
    assert any(d.stage == 'greeting' for d in invalid.diagnostics)
