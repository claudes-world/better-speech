from dataclasses import replace
from datetime import datetime, timezone

import pytest

from better_speech import compile
from better_speech.cli import main
from better_speech.ledger import Ledger, record_delivered
from better_speech.transcript import present
from tests.test_model_free_audio import profile

NOW = datetime(2026, 10, 21, 14, tzinfo=timezone.utc)


def greetings(plan):
    return [r for r in plan.lessons if r.record.get('category') == 'greeting']


@pytest.mark.parametrize('flag,expected', [(False, 0), (True, 1)])
@pytest.mark.parametrize('text', ['Body without a heading.', '# Heading\n\nBody.'])
def test_greeting_flag_and_first_audible_clip(tmp_path, flag, expected, text):
    cfg = profile(timezone='America/New_York')
    plan = compile(text, cfg, ledger=Ledger(tmp_path / 'ledger'), now=NOW, greeting=flag)
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
    ('UTC', 21, 'evening'), ('UTC', 23, 'night'), ('UTC', 3, 'night'), ('Asia/Ho_Chi_Minh', 2, 'morning')])
def test_greeting_profile_timezone(tmp_path, zone, hour, period):
    now = NOW.replace(hour=hour)
    seen = set()
    for seed in range(16):
        plan = compile('Note.', profile(timezone=zone), ledger=Ledger(tmp_path / 'ledger'), now=now,
                       seed=seed, greeting=True)
        assert greetings(plan)[0].record['time_of_day'] in (period, 'any')
        seen.add(greetings(plan)[0].record['time_of_day'])
    assert period in seen


def test_rotation_is_per_learner_and_delivered_only(tmp_path):
    ledger = Ledger(tmp_path / 'ledger')
    cfg = profile()
    def prepare(cfg=cfg):
        return compile('Note.', cfg, ledger=ledger, now=NOW, seed=5, greeting=True)
    first = prepare()
    assert greetings(prepare()) == greetings(first)
    assert not record_delivered(replace(first, executed=True), 'first', NOW, ledger=ledger).diagnostics
    assert greetings(prepare())[0].phrase_key != greetings(first)[0].phrase_key
    assert greetings(prepare(replace(cfg, learner_id='other'))) == greetings(first)
    key = greetings(first)[0].phrase_key
    assert ledger.snapshot()[key]['lastGreetingKey'] == key


def test_rotation_on_equal_confirmation_timestamps(tmp_path):
    ledger = Ledger(tmp_path / 'ledger')
    previous = None
    for index in range(8):
        plan = compile('Note.', profile(), ledger=ledger, now=NOW, seed=5, greeting=True)
        key = greetings(plan)[0].phrase_key
        assert key != previous
        record_delivered(replace(plan, executed=True), f'delivery-{index}', NOW, ledger=ledger)
        previous = key


def test_default_flag_and_cli_plan_flag(tmp_path, capsys):
    cfg = profile()
    assert not greetings(compile('Note.', cfg, ledger=Ledger(tmp_path / 'ledger')))
    config = tmp_path / 'profile.toml'
    config.write_text('model="none"\nstate_dir="state"\n[[voices]]\nvoice="native"\n')
    source = tmp_path / 'note.md'
    source.write_text('Note.')
    assert main(['plan', str(source), '--profile', str(config), '--greeting']) == 0
    import json
    plan = json.loads(capsys.readouterr().out)
    assert any('"category":"greeting"' in r['record_json'] for r in plan['lessons'])
