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
    return [r for r in plan.lessons if r.record.get('category') in ('greeting', 'opener')]


@pytest.mark.parametrize('flag,expected', [(False, 0), (True, 1)])
@pytest.mark.parametrize('text', ['Body without a heading.', '# Heading\n\nBody.'])
def test_greeting_flag_and_first_audible_clip(tmp_path, flag, expected, text):
    cfg = profile(timezone='America/New_York')
    plan = compile(text, cfg, ledger=Ledger(tmp_path / 'ledger'), now=NOW, greeting=flag)
    assert len(greetings(plan)) == expected
    assert len(plan.lessons) == 2 + expected
    assert ''.join(c.text for c in plan.clips if c.purpose == 'source') == plan.speech_text
    if expected:
        greeting = greetings(plan)[0]
        assert plan.clips[0].phrase_key == greeting.phrase_key
        assert plan.clips[0].language == 'vi'
        assert greeting.record['time_of_day'] in ('morning', 'any')
        assert greeting.record['phrase'].rstrip('!.?') in present(replace(plan, executed=True))[1]


@pytest.mark.parametrize('zone,hour,period', [('UTC', 6, 'morning'), ('UTC', 15, 'afternoon'),
    ('UTC', 21, 'evening'), ('UTC', 23, 'night'), ('UTC', 3, 'night'), ('Asia/Ho_Chi_Minh', 2, 'morning')])
def test_greeting_profile_timezone(tmp_path, zone, hour, period):
    now = NOW.replace(hour=hour)
    seen = set()
    for seed in range(80):
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
    assert any('"category":"greeting"' in r['record_json'] or '"category":"opener"' in r['record_json']
               for r in plan['lessons'])


def opener_rows():
    from better_speech.curriculum import runtime_curriculum
    return [r for r in runtime_curriculum(profile()) if r.get('category') == 'opener']


def test_opener_pool_shape():
    rows = opener_rows()
    assert len(rows) == 20
    assert all(r['time_of_day'] == 'any' and r['hint'] and r['l1_literal'] for r in rows)
    assert len({r['id'] for r in rows}) == 20


def test_openers_never_closers_or_model_offers(tmp_path):
    for seed in range(8):
        plan = compile('Note.', profile(), ledger=Ledger(tmp_path / 'ledger'), now=NOW, seed=seed)
        assert all(r.record.get('category') not in ('greeting', 'opener') for r in plan.lessons)


def test_greeting_flag_mixes_openers_and_greetings(tmp_path):
    kinds = set()
    for seed in range(60):
        plan = compile('Note.', profile(), ledger=Ledger(tmp_path / 'ledger'), now=NOW, seed=seed, greeting=True)
        row = plan.clips[0]
        record = next(r.record for r in plan.lessons if r.phrase_key == row.phrase_key)
        assert record['category'] in ('greeting', 'opener')
        kinds.add(record['category'])
    assert kinds == {'greeting', 'opener'}


def test_opener_rotation_no_repeat_and_ledger_key(tmp_path):
    ledger = Ledger(tmp_path / 'ledger')
    previous, seen = None, set()
    for index in range(12):
        plan = compile('Note.', profile(), ledger=ledger, now=NOW, seed=index, greeting=True)
        key = plan.clips[0].phrase_key
        assert key != previous
        record_delivered(replace(plan, executed=True), f'd-{index}', NOW, ledger=ledger)
        assert ledger.snapshot()[key]['lastGreetingKey'] == key
        previous = key
        seen.add(key)
    assert len(seen) > 2


def test_category_is_drawn_fifty_fifty(tmp_path):
    counts = {'greeting': 0, 'opener': 0}
    for seed in range(400):
        plan = compile('Note.', profile(), ledger=Ledger(tmp_path / 'ledger'), now=NOW, seed=seed, greeting=True)
        counts[greetings(plan)[0].record['category']] += 1
    assert 140 < counts['greeting'] < 260


def closings(plan):
    return [r for r in plan.lessons if r.record.get('category') == 'closing']


@pytest.mark.parametrize('text', ['Body.', 'First paragraph.\n\nSecond paragraph.'])
@pytest.mark.parametrize('greeting', [False, True])
def test_closing_only_after_complete_source(tmp_path, text, greeting):
    plan = compile(text, profile(), ledger=Ledger(tmp_path / 'ledger'), now=NOW, greeting=greeting)
    assert len(closings(plan)) == 1
    key = closings(plan)[0].phrase_key
    source_indices = [i for i, c in enumerate(plan.clips) if c.purpose == 'source']
    closing_indices = [i for i, c in enumerate(plan.clips) if c.phrase_key == key]
    assert min(closing_indices) > max(source_indices)
    sections, _ = present(replace(plan, executed=True))
    lines = next(s.text for s in sections if s.kind == 'lesson').splitlines()
    assert any(line.startswith('**') and 'Cảm ơn' in line for line in lines)
    assert not any(line[0].isdigit() and 'Cảm ơn' in line for line in lines if line)


def test_closing_rotation_is_confirmed_and_per_learner(tmp_path):
    ledger = Ledger(tmp_path / 'ledger')
    cfg = profile()
    def prepare(cfg=cfg):
        return compile('Note.', cfg, ledger=ledger, now=NOW, seed=5)
    first = closings(prepare())[0]
    assert closings(prepare())[0] == first
    previous = None
    for index in range(6):
        plan = prepare()
        key = closings(plan)[0].phrase_key
        if index:
            assert key != previous
        record_delivered(replace(plan, executed=True), f'closing-{index}', NOW, ledger=ledger)
        assert ledger.snapshot()[key]['lastClosingKey'] == key
        previous = key
    assert closings(prepare(replace(cfg, learner_id='other')))[0] == first


@pytest.mark.parametrize('offset,position,mode', [(0, 'start', 'open'), (4, 'end', 'close'),
                                                (10, 'end', 'open')])
def test_planner_rejects_closing_at_other_slots(offset, position, mode):
    from better_speech.curriculum import runtime_curriculum
    from better_speech.planner import LessonPlacement, sequence_clips
    cfg = profile()
    row = next(r for r in runtime_curriculum(cfg) if r.get('category') == 'closing')
    clips, error = sequence_clips('One.\n\nTwo.', inventory=[row],
        placements=[LessonPlacement(row['id'], offset, position, cfg.voices[0], None, mode=mode)],
        main=cfg.english, settings={})
    assert error
    assert all(c.purpose == 'source' for c in clips)


def test_closings_never_offered_to_context_model(tmp_path):
    def model(payload, **kwargs):
        assert all(not row['id'].startswith('cam-on-') for row in payload['inventory'])
        return '{"placements": []}'
    plan = compile('One.\n\nTwo.', replace(profile(), model='claude'), model_call=model,
                   ledger=Ledger(tmp_path / 'ledger'), now=NOW)
    assert len(closings(plan)) == 1
    assert not any(d.stage == 'model' for d in plan.diagnostics)


@pytest.mark.parametrize('intensity,known_connectors,expected_regular', [(1, False, 2), (2, True, 4)])
def test_bookends_do_not_consume_placement_or_new_phrase_caps(tmp_path, intensity, known_connectors, expected_regular):
    import json

    from better_speech.curriculum import runtime_curriculum
    cfg = replace(profile(), intensity=intensity, model='claude')
    class Known(Ledger):
        def learner_state(self, *args, **kwargs):
            records = ({r['ledger_identity']: {'dueAt': '2099-01-01T00:00:00Z'}
                        for r in runtime_curriculum(cfg) if r['lesson_type'] == 'connector'}
                       if known_connectors else {})
            return records, []
    def select(payload, **kwargs):
        return json.dumps({'placements': [
            {'phraseId': row['id'], 'slotId': slot}
            for row, slot in zip(payload['inventory'], payload['slots'])]})
    plan = compile('# Heading\n\nFirst.\n\nSecond.\n\nThird.', cfg, ledger=Known(tmp_path / 'ledger'),
                   now=NOW, greeting=True, model_call=select)
    assert len(greetings(plan)) == len(closings(plan)) == 1
    regular = [r for r in plan.lessons if r.record.get('category') not in ('greeting', 'opener', 'closing')]
    assert len(regular) == expected_regular
    assert not any(d.reason == 'contextual_plan_rejected' for d in plan.diagnostics)
