import json
from datetime import datetime, timezone

import pytest

from better_speech import compile
from better_speech.learning import learn, learned
from better_speech.ledger import Ledger
from tests.test_model_free_audio import EmptyLedger, profile

NOW = datetime(2026, 10, 20, 12, tzinfo=timezone.utc)


@pytest.mark.parametrize('phrase,gloss', [('!!!', 'Hello'), ('👍', 'Hello'), ('Học nhé.', 'x' * 4001)])
def test_reject_unteachable_before_writing(tmp_path, phrase, gloss):
    cfg = profile(state_dir=str(tmp_path))
    with pytest.raises(ValueError):
        learn(phrase, gloss, cfg, now=NOW, ledger=Ledger.for_profile(cfg))
    assert not Ledger.for_profile(cfg).path.exists()
    assert compile('An update.', cfg, ledger=EmptyLedger()).lessons


@pytest.mark.parametrize('landed', [False, True])
def test_skip_invalid_persisted_candidate(tmp_path, landed):
    cfg = profile(state_dir=str(tmp_path))
    ledger = Ledger.for_profile(cfg)
    ledger.edit_phrases(cfg.learner_id, lambda rows: rows.append({'id': 'poison', 'l2': '!!!', 'l1': 'Hello'}))
    if landed:
        learned('poison', cfg, ledger=Ledger.for_profile(cfg))
    plan = compile('An update.', cfg, ledger=Ledger.for_profile(cfg), now=NOW)
    assert plan.lessons and all(lesson.id != 'poison' for lesson in plan.lessons)
    assert any(d.reason == 'rejected_candidate:poison' for d in plan.diagnostics)


def test_render_inside_running_loop_keeps_paid_audio(tmp_path):
    import asyncio

    from better_speech import render
    from tests.test_model_free_audio import ToneProvider

    plan = compile('An update.', profile(), ledger=EmptyLedger())
    provider = ToneProvider()

    async def run():
        return render(plan, {'google': provider, 'elevenlabs': provider}, tmp_path)

    result = asyncio.run(run())
    assert result.status == 'bilingual'
    assert result.audio_path.read_bytes().startswith(b'OggS')
    assert not any(d.stage == 'render' for d in result.diagnostics)


@pytest.mark.parametrize('source', [
    '[Read](https://example.org/><script>alert(1)</script>)',
    '[Read](https://example.org/><img/src=x/onerror=alert(1)>)',
    'https://example.org/><script>alert(1)</script>',
])
def test_links_do_not_inject_html(source):
    from markdown_it import MarkdownIt

    from better_speech.transcript import present

    plan = compile(source, profile(enabled=False), ledger=EmptyLedger())
    _, markdown = present(plan)
    rendered = MarkdownIt('commonmark', {'html': True}).render(markdown)
    assert '<script>' not in rendered and '<img' not in rendered


@pytest.mark.parametrize('broken', ['modle="none"', '[voices]\nvoice="broken"'])
def test_bad_profile_preserves_routing_and_refuses_learner_writes(tmp_path, monkeypatch, broken):
    from better_speech.cli import main
    from better_speech.config import load_profile

    monkeypatch.setenv('XDG_DATA_HOME', str(tmp_path / 'default'))
    path = tmp_path / 'profile.toml'
    path.write_text('learner_id="alice"\nstate_dir="alice-state"\ncurriculum_path="custom.json"\n'
                    'timezone="America/New_York"\n' + broken)
    cfg = load_profile(path)
    assert cfg.learner_id == 'alice'
    assert cfg.state_dir == str(tmp_path / 'alice-state')
    assert cfg.curriculum_path == str(tmp_path / 'custom.json')
    assert cfg.timezone == 'America/New_York'
    assert 'invalid_profile' in cfg.config_diagnostics
    assert main(['learn', 'Học nhé.', '--gloss', 'Learn.', '--profile', str(path)]) == 2
    assert main(['learned', 'cam-on-nha', '--profile', str(path)]) == 2
    assert not (tmp_path / 'alice-state').exists()
    assert not (tmp_path / 'default').exists()


def test_two_boosted_phrases_rotate_until_local_midnight(tmp_path):
    from dataclasses import replace
    from datetime import timedelta

    from better_speech.ledger import Ledger

    cfg = profile(state_dir=str(tmp_path), timezone='America/New_York')
    ledger = Ledger.for_profile(cfg)
    a = learn('Cảm ơn nhiều.', 'Many thanks.', cfg, now=NOW, ledger=Ledger.for_profile(cfg))
    first = compile('Update.', cfg, ledger=ledger, now=NOW)
    ledger.record(replace(first, executed=True), 'first', NOW)
    b = learn('Hẹn gặp lại.', 'See you again.', cfg, now=NOW + timedelta(hours=1), ledger=Ledger.for_profile(cfg))
    selected = []
    for hour in range(1, 6):
        at = NOW + timedelta(hours=hour)
        plan = compile('Update.', cfg, ledger=ledger, now=at)
        selected.append(plan.lessons[0].id)
        ledger.record(replace(plan, executed=True), str(hour), at)
    assert selected == [b['id'], a['id'], b['id'], a['id'], b['id']]


def test_unheard_boost_tie_prefers_newest(tmp_path):
    from datetime import timedelta
    cfg = profile(state_dir=str(tmp_path))
    learn('Học nữa.', 'Learn more.', cfg, now=NOW, ledger=Ledger.for_profile(cfg))
    latest = learn('Học tiếp nhé.', 'Keep learning.', cfg, now=NOW + timedelta(minutes=1), ledger=Ledger.for_profile(cfg))
    plan = compile('Update.', cfg, ledger=Ledger.for_profile(cfg), now=NOW + timedelta(minutes=2))
    assert plan.lessons[0].id == latest['id']


@pytest.mark.parametrize('version', [None, True, 1.0, '1', 0, 2])
def test_plan_rejects_unsupported_exact_version(version):
    from better_speech import Plan
    value = compile('Update.', profile(), ledger=EmptyLedger()).to_dict()
    value['schema_version'] = version
    with pytest.raises(ValueError, match='schema_version'):
        Plan.from_dict(value)


def test_plan_version_roundtrip_and_ledger_unknown_version(tmp_path):
    import sqlite3

    from better_speech import Ledger, Plan
    plan = compile('Update.', profile(), ledger=EmptyLedger())
    assert Plan.from_dict(plan.to_dict()) == plan
    ledger = Ledger(tmp_path / 'state.db')
    ledger._connect().close()
    with sqlite3.connect(ledger.path) as db:
        assert db.execute('PRAGMA user_version').fetchone()[0] == 2
        db.execute('PRAGMA user_version=999')
    with pytest.raises(ValueError, match='user_version'):
        ledger.snapshot()
    with sqlite3.connect(ledger.path) as db:
        assert db.execute('PRAGMA user_version').fetchone()[0] == 999


def test_provider_lowering_preserves_multibyte_source_ranges_and_final_pause():
    from better_speech.chunking import lower_clip
    plan = compile('Tiếng Việt ' * 20, profile(enabled=False), ledger=EmptyLedger())
    from dataclasses import replace
    clip = replace(plan.clips[0], pause_after_s=1.2)
    parts = list(lower_clip(clip, 25))
    assert ''.join(c.text for c in parts) == clip.text
    assert all(len(c.text.encode()) <= 25 for c in parts)
    assert all(c.text == plan.speech_text[c.source_start:c.source_end] for c in parts)
    assert all(c.pause_after_s == 0 for c in parts[:-1]) and parts[-1].pause_after_s == 1.2


def test_plan_has_no_operational_authority_and_delivery_uses_explicit_store(tmp_path):
    from dataclasses import replace

    from better_speech import Plan, record_delivered
    cfg = profile(state_dir=str(tmp_path / 'unused'), provider_timeout_s=123, claude_model='private-model')
    plan = compile('Update.', cfg, ledger=EmptyLedger())
    encoded = json.dumps(plan.to_dict())
    assert all(field not in encoded for field in ('profile_json', 'state_dir', 'timeout_s', 'private-model'))
    ledger = Ledger(tmp_path / 'explicit.db')
    restored = Plan.from_dict(replace(plan, executed=True).to_dict())
    assert record_delivered(restored, 'send', NOW, ledger=ledger).added == 1
    assert not (tmp_path / 'unused').exists()
    tampered = {**plan.to_dict(), 'profile_json': json.dumps({'state_dir': str(tmp_path / 'injected')})}
    with pytest.raises(TypeError):
        Plan.from_dict(tampered)
    assert not (tmp_path / 'injected').exists()


def test_learner_edit_failure_rolls_back_and_learners_share_one_store(tmp_path):
    ledger = Ledger(tmp_path / 'ledger.db')
    cfg = profile(learner_id='alice')
    learn('Học nhé.', 'Learn.', cfg, ledger=ledger, now=NOW)
    before = ledger.learner_state('alice')

    def broken(rows):
        rows[0]['l1'] = 'Changed'
        raise ValueError('abort')

    with pytest.raises(ValueError, match='abort'):
        ledger.edit_phrases('alice', broken)
    assert ledger.learner_state('alice') == before
    assert ledger.learner_state('bob') == ({}, [])
    assert ledger.snapshot('alice') == {}


def test_custom_curriculum_does_not_inherit_bundled_metadata(tmp_path):
    path = tmp_path / 'custom.json'
    path.write_text(json.dumps({'focusPhrases': [{'id': 'cam-on-nha', 'l2': 'Học tiếp nhé.', 'l1': 'Keep learning.'}]}))
    plan = compile('Update.', profile(curriculum_path=str(path)), ledger=EmptyLedger())
    assert plan.lessons[0].record['units'] == ['Học tiếp nhé.']
    assert plan.status == 'bilingual'


def test_version_one_ledger_migrates_without_losing_exposures(tmp_path):
    import sqlite3
    from dataclasses import replace
    ledger = Ledger(tmp_path / 'v1.db')
    plan = replace(compile('Update.', profile(), ledger=EmptyLedger()), executed=True)
    ledger.record(plan, 'sent', NOW)
    before = ledger.snapshot()
    with sqlite3.connect(ledger.path) as db:
        db.execute('DROP TABLE learner_phrases')
        db.execute('PRAGMA user_version=1')
    assert ledger.snapshot() == before
    assert ledger.learner_state(plan.learner_id)[1] == []
    with sqlite3.connect(ledger.path) as db:
        assert db.execute('PRAGMA user_version').fetchone()[0] == 2
