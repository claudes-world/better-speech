import json
from datetime import datetime, timezone

import pytest

from better_speech import compile
from better_speech.curriculum import learner_path
from better_speech.learning import learn, learned
from tests.test_model_free_audio import EmptyLedger, profile

NOW = datetime(2026, 10, 20, 12, tzinfo=timezone.utc)


@pytest.mark.parametrize('phrase,gloss', [('!!!', 'Hello'), ('👍', 'Hello'), ('Học nhé.', 'x' * 4001)])
def test_reject_unteachable_before_writing(tmp_path, phrase, gloss):
    cfg = profile(state_dir=str(tmp_path))
    with pytest.raises(ValueError):
        learn(phrase, gloss, cfg, now=NOW)
    assert not learner_path(cfg).exists()
    assert compile('An update.', cfg, ledger=EmptyLedger()).lessons


@pytest.mark.parametrize('landed', [False, True])
def test_skip_invalid_persisted_candidate(tmp_path, landed):
    cfg = profile(state_dir=str(tmp_path))
    path = learner_path(cfg)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps({'focusPhrases': [{'id': 'poison', 'l2': '!!!', 'l1': 'Hello'}]}))
    if landed:
        learned('poison', cfg)
    plan = compile('An update.', cfg, ledger=EmptyLedger(), now=NOW)
    assert plan.lessons and all(l.id != 'poison' for l in plan.lessons)
    assert any(d.reason == 'rejected_candidate:poison' for d in plan.diagnostics)
