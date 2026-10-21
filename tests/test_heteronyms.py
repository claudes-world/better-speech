import hashlib
import json
import time
from dataclasses import replace
from importlib.resources import files
from types import SimpleNamespace
from xml.etree import ElementTree

import pytest

from better_speech import Plan, compile, render
from better_speech.cache import ClipCache
from better_speech.chunking import lower_clip
from better_speech.config import resolve_profile
from better_speech.heteronyms import heteronym_table, scan
from better_speech.pronunciation import speech_input
from better_speech.providers.google import GoogleProvider
from tests.test_model_free_audio import EmptyLedger, ToneProvider, profile
from tests.test_providers import clip


def answer(payload, **kwargs):
    if 'candidates' not in payload:
        return '{"placements":[]}'
    return json.dumps({'results': [{'id': c['id'], 'sense_id': c['senses'][-1]['id']}
                                   for c in payload['candidates']]})


def test_shipped_heteronym_table_and_detect_only_whole_words_without_sense_choice():
    lex = heteronym_table()
    assert hashlib.sha256(files('better_speech').joinpath('data/heteronyms.en.json').read_bytes()).hexdigest() == (
        '139ba2b5898c5c9520937a5b43b3dc276c84677a328aa95062b58f45ca84b5d1')
    assert '_meta' in lex and 'suppressors' in lex['_meta']
    text = 'We read LIVE; misread and lively are different. A bass, and a bass.'
    candidates = scan(text)
    assert [c['word'] for c in candidates] == ['read', 'LIVE', 'bass', 'bass']
    assert all(text[s:e] == c['word'] for c in candidates for s, e in [c['word_span']])
    assert all('chosen' not in c and 'ipa' not in c for c in candidates)
    assert files('better_speech').joinpath('data/heteronyms.en.json').is_file()


def test_morphology_and_capitalized_proper_noun_suppressors():
    assert not scan('Reading, living, closed, recorded, polishing, objected.')
    text = 'They speak Polish in August. Polish the table. August is a word too.'
    candidates = scan(text)
    assert [c['word'] for c in candidates] == ['Polish', 'August']
    assert [c['sentence'] for c in candidates] == ['Polish the table.', 'August is a word too.']
    assert scan('  "Polish it."')[0]['word'] == 'Polish'


def test_one_batch_english_only_trusted_ipa_roundtrip():
    calls = []
    def model(payload, **kwargs):
        calls.append(payload)
        return answer(payload, **kwargs)
    cfg = replace(profile(), model='fake')
    plan = compile('We read it. A live broadcast.', cfg, ledger=EmptyLedger(), model_call=model)
    batches = [c for c in calls if 'candidates' in c]
    assert len(batches) == 1
    assert len(batches[0]['candidates']) == 2
    corrections = [c for c in plan.clips if c.pronunciations]
    assert corrections and all(c.language == 'en' for c in corrections)
    assert all(not c.pronunciations for c in plan.clips if c.language == 'vi')
    assert corrections[0].text == 'We read it. A live broadcast.'
    assert corrections[0].pronunciations == [
        {'word_span': [3, 7], 'ipa': 'ɹɛd'}, {'word_span': [14, 18], 'ipa': 'laɪv'}]
    assert Plan.from_dict(json.loads(json.dumps(plan.to_dict()))) == plan
    assert all(not c.pronunciations for c in plan.fallback_clips)


@pytest.mark.parametrize('setting', [None, True, False])
def test_model_none_never_calls_model_and_notes_plain_path(setting):
    def forbidden(*args, **kwargs):
        pytest.fail('model-none invoked a model')
    plan = compile('A live broadcast.', profile(heteronyms=setting), ledger=EmptyLedger(), model_call=forbidden)
    assert not any(c.pronunciations for c in plan.clips)
    assert any(d.stage == 'heteronym' and d.reason == 'model_none' for d in plan.diagnostics)
    assert resolve_profile(profile()).heteronyms is False
    assert resolve_profile(replace(profile(), model='fake')).heteronyms is True


def test_explicit_disable_and_registry_selection(monkeypatch):
    calls = []
    from better_speech.models import register_model
    monkeypatch.setattr('better_speech.models.REGISTRY', {})
    register_model('fourth', lambda cfg: lambda payload, **kwargs: calls.append(payload) or answer(payload))
    cfg = replace(profile(enabled=False), model='fourth')
    plan = compile('We read it.', cfg, ledger=EmptyLedger())
    assert len(calls) == 1 and plan.clips[0].pronunciations
    calls.clear()
    plain = compile('We read it.', replace(cfg, heteronyms=False), ledger=EmptyLedger())
    assert not calls and not plain.clips[0].pronunciations


@pytest.mark.parametrize('response', ['{}', '{"results":[]}', 'not JSON',
    '{"results":[{"id":"h0","sense_id":"invented"}]}',
    '{"results":[{"id":"h0","sense_id":"past","ipa":"injected"}]}',
    '{"results":[{"id":"other","sense_id":"past"}]}'])
def test_bad_model_result_keeps_exact_plain_clips(response):
    cfg = replace(profile(enabled=False), model='fake')
    plan = compile('We read it.', cfg, ledger=EmptyLedger(), model_call=lambda *a, **k: response)
    assert plan.clips == plan.fallback_clips
    assert any(d.stage == 'heteronym' for d in plan.diagnostics)


def test_skip_and_late_or_failed_model_keep_plain():
    cfg = replace(profile(enabled=False), model='fake', model_timeout_s=0.01)
    for mode in ['skip', 'late', 'failed']:
        def model(payload, **kwargs):
            if mode == 'late':
                time.sleep(0.025)
            if mode == 'failed':
                raise OSError('unavailable')
            return json.dumps({'results': [{'id': 'h0', 'sense_id': 'skip' if mode == 'skip' else 'past'}]})
        plan = compile('We read it.', cfg, ledger=EmptyLedger(), model_call=model)
        assert plan.clips == plan.fallback_clips
        if mode != 'skip':
            assert any(d.stage == 'heteronym' for d in plan.diagnostics)


def pronounced(text='We read & <listen>.'):
    return replace(clip(), text=text, pronunciations=[{'word_span': [3, 7], 'ipa': 'ɹɛd'}])


def test_google_ssml_escapes_text_and_ipa_and_plain_default():
    requests = []
    provider = GoogleProvider(SimpleNamespace(synthesize_speech=lambda **kw:
                              requests.append(kw) or SimpleNamespace(audio_content=b'audio')))
    provider.synthesize(pronounced(), timeout=2)
    ssml = requests[0]['input'].ssml
    assert ssml == '<speak>We <phoneme alphabet="ipa" ph="ɹɛd">read</phoneme> &amp; &lt;listen&gt;.</speak>'
    assert not requests[0]['input'].text
    assert ''.join(ElementTree.fromstring(ssml).itertext()) == pronounced().text
    provider.synthesize(replace(pronounced(), pronunciations=[]), timeout=2)
    assert requests[1]['input'].text == pronounced().text and not requests[1]['input'].ssml
    hostile = replace(pronounced(), pronunciations=[{'word_span': [3, 7], 'ipa': '\"><&'}])
    assert ElementTree.fromstring(speech_input(hostile)[1]).find('phoneme').get('ph') == '\"><&'
    assert speech_input(replace(pronounced(), provider='elevenlabs')) == ('text', pronounced().text)


def test_cache_separates_ipa_and_plain(tmp_path):
    cache = ClipCache(tmp_path)
    purchases = []
    for item in [pronounced(), replace(pronounced(), pronunciations=[]),
                 replace(pronounced(), pronunciations=[{'word_span': [3, 7], 'ipa': 'ɹiːd'}]), pronounced()]:
        cache.get_or_synthesize(item, lambda: purchases.append(item) or b'audio',
                                deadline=time.monotonic() + 2, diagnostics=[])
    assert len(purchases) == 3


def test_chunking_remaps_spans_and_counts_ssml_bytes():
    text = 'Start. ' * 10 + 'We read the record. ' * 8
    corrections = [{'word_span': [m, m+4], 'ipa': 'ɹɛd'}
                   for m in range(len(text)) if text[m:m+4] == 'read']
    original = replace(clip(), text=text, pronunciations=corrections)
    chunks = tuple(lower_clip(original, 110))
    assert ''.join(c.text for c in chunks) == text
    assert sum(len(c.pronunciations) for c in chunks) == len(corrections)
    assert all(len(speech_input(c)[1].encode()) <= 110 for c in chunks)
    assert all(c.text[s:e] == 'read' for c in chunks for p in c.pronunciations for s,e in [p['word_span']])


@pytest.mark.parametrize('mode', ['provider_error', 'small_limit', 'invalid_span'])
def test_render_failure_and_limit_keep_complete_english(tmp_path, mode):
    plan = compile('We read it.', replace(profile(enabled=False), model='fake'),
                   ledger=EmptyLedger(), model_call=answer)
    if mode == 'invalid_span':
        plan = replace(plan, clips=(replace(plan.clips[0], pronunciations=[{'word_span': [0, 500], 'ipa': 'x'}]),))
    class Provider(ToneProvider):
        def synthesize(self, c, **kwargs):
            if mode == 'provider_error' and c.pronunciations:
                raise ValueError('SSML unavailable')
            return super().synthesize(c, **kwargs)
    provider = Provider()
    if mode == 'small_limit':
        provider.max_request_bytes = 4
    result = render(plan, {'google': provider}, tmp_path)
    assert result.audio_path and result.status == 'english', result.diagnostics
    assert ''.join(c.text for c in result.plan.clips) == plan.speech_text
    assert not any(c.pronunciations for c in result.plan.clips)
    assert result.diagnostics


def test_pre_week_two_plan_keeps_execution_identity():
    from better_speech.types import digest
    plan = replace(compile('Note.', profile(enabled=False), ledger=EmptyLedger()), executed=True)
    old = plan.to_dict()
    for row in old['clips'] + old['fallback_clips']:
        row.pop('pronunciations')
        row.pop('echo')
    previous_digest = digest([old['clips'], old['lessons']])
    old['executed_digest'] = previous_digest
    assert Plan.from_dict(old).executed_digest == previous_digest
