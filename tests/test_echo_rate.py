
import pytest

from better_speech import compile, render
from tests.test_model_free_audio import EmptyLedger, ToneProvider, profile


@pytest.mark.parametrize('settings', [{}, {'echo_rate': 0.8}, {'repeat_second_voice': False},
                                    {'long_break_seconds': 0.01}])
def test_echo_rate_changes_only_second_occurrence(settings):
    plan = compile('Note.', profile(**settings), ledger=EmptyLedger(), greeting=True)
    lead = [c for c in plan.clips if c.language == 'vi' and not c.echo]
    echoes = [c for c in plan.clips if c.language == 'vi' and c.echo]
    assert len(lead) == len(echoes) == 2
    assert all(c.rate == 0.9 for c in lead)
    assert all(c.rate == settings.get('echo_rate', 0.75) for c in echoes)
    assert all(c.rate == 1.05 for c in plan.clips if c.language == 'en')
    if settings.get('repeat_second_voice') is False:
        assert [c.voice for c in lead] == [c.voice for c in echoes]


@pytest.mark.parametrize("minimum,wrapped", [(0.7, True), (0.8, False)])
def test_provider_minimum_clamps_echo_before_attempt_and_cache_key(tmp_path, minimum, wrapped):
    class Minimum(ToneProvider):
        minimum_rate = minimum
        def synthesize(self, clip, **kwargs):
            assert clip.rate >= self.minimum_rate
            return super().synthesize(clip, **kwargs)
    provider = Minimum()
    from types import SimpleNamespace
    resolved = SimpleNamespace(languages=provider.languages, formats=provider.formats,
        max_request_bytes=provider.max_request_bytes, synthesize=provider.synthesize) if wrapped else provider
    plan = compile('Note.', profile(echo_rate=0.5), ledger=EmptyLedger())
    result = render(plan, {'google': resolved, 'elevenlabs': resolved}, tmp_path)
    assert result.audio_path and result.status == 'bilingual', result.diagnostics
    assert any(d.reason == 'echo_rate_clamped_to_provider_minimum' for d in result.diagnostics)
    assert [c.rate for c in result.plan.clips if c.language == 'vi'] == [0.9, minimum]
    purchases = len(provider.calls)
    again = render(plan, {'google': resolved, 'elevenlabs': resolved}, tmp_path)
    assert again.cache_hits and len(provider.calls) == purchases


def test_landed_phrase_has_no_echo(tmp_path):
    from better_speech.learning import learn, learned
    from better_speech.ledger import Ledger
    cfg = profile()
    ledger = Ledger(tmp_path / 'ledger')
    row = learn('Học nhé.', 'Learn.', cfg, ledger=ledger)
    learned(row['id'], cfg, ledger=ledger)
    # A landed phrase can still be due; it speaks only its original lead rate.
    from better_speech.curriculum import runtime_curriculum
    class Due(EmptyLedger):
        def learner_state(self, *args, **kwargs):
            additions = ledger.learner_state(cfg.learner_id)[1]
            return {r['ledger_identity']: {'dueAt': '2020-01-01' if r['id'] == row['id'] else '2099-01-01'}
                    for r in runtime_curriculum(cfg, additions=additions)}, additions
    plan = compile('Note.', cfg, ledger=Due())
    assert not any(c.echo for c in plan.clips)
    assert [c.rate for c in plan.clips if c.language == 'vi'] == [0.9]
