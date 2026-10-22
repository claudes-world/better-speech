import json
from dataclasses import replace
from importlib.resources import files

from better_speech import compile
from better_speech.cli import main
from better_speech.curriculum import runtime_curriculum
from better_speech.ledger import Ledger
from better_speech.transcript import present
from tests.test_model_free_audio import EmptyLedger, profile


def test_every_record_has_short_draft_hint():
    rows = json.loads(files('better_speech').joinpath('data/curriculum.vi.json').read_text())['focusPhrases']
    assert len(rows) == 84
    assert all(isinstance(r['hint'], str) and 0 < len(r['hint']) < 60 for r in rows)
    assert all(r['hint'] == r['hint'].lower() for r in rows)
    metadata = files('better_speech').joinpath('data/curriculum.vi.metadata.json').read_text()
    assert 'Founder-editable drafts' in metadata
    assert all(r['hint'] for r in runtime_curriculum(profile()))


def test_hint_rendering_below_literal_and_never_spoken():
    plan = compile('Note.', profile(), ledger=EmptyLedger())
    sections, _ = present(replace(plan, executed=True))
    lines = next(s.text for s in sections if s.kind == 'lesson').splitlines()
    assert 'go' in lines[1] and '[just/let' in lines[1].replace('\\', '')
    assert lines[2] == '   *say: dee\\-thoy*'
    assert all('dee-thoy' not in clip.text for clip in plan.clips)


def test_cli_hint_persists_and_renders(tmp_path, capsys):
    config = tmp_path / 'profile.toml'
    config.write_text('model="none"\nstate_dir="state"\n[[voices]]\nvoice="native"\n')
    assert main(['learn', 'Học nhé.', '--gloss', 'Let us learn.', '--hint', 'hawk-nyeh',
                 '--profile', str(config)]) == 0
    row = json.loads(capsys.readouterr().out)
    assert row['hint'] == 'hawk-nyeh'
    from better_speech.config import load_profile
    cfg = load_profile(config)
    plan = compile('Note.', cfg, ledger=Ledger.for_profile(cfg))
    assert 'say: hawk\\-nyeh' in present(replace(plan, executed=True))[1]
    assert all('hawk-nyeh' not in c.text for c in plan.clips)
