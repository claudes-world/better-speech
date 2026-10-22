import json
import re
import unicodedata
from dataclasses import replace
from importlib.resources import files

import pytest

from better_speech import compile
from better_speech.cli import main
from better_speech.curriculum import runtime_curriculum
from better_speech.ledger import Ledger
from better_speech.transcript import present
from tests.test_model_free_audio import EmptyLedger, profile


def test_southern_hints_have_no_northern_z_syllables():
    rows = json.loads(files('better_speech').joinpath('data/curriculum.vi.json').read_text())['focusPhrases']
    # No genuine z-words in the shipped curriculum; keep any future exceptions explicit.
    z_word_exceptions = set()
    checked = set()
    for row in rows:
        # Strip vowel tone marks so gì matches gi, while keeping đ distinct from d.
        plain = ''.join(
            char for char in unicodedata.normalize('NFD', row['l2'].lower())
            if not unicodedata.combining(char)
        )
        words = re.findall(r'[^\W\d_]+', plain)
        if any(word.startswith(('d', 'gi', 'r')) for word in words):
            checked.add(row['id'])
            if row['id'] not in z_word_exceptions:
                assert not re.search(r'\bz[a-z]*', row['hint']), row['id']
    assert {'da', 'khong-co-gi', 'giup-minh-voi', 'giu-tien-thua', 'duoc-roi'} <= checked
    assert z_word_exceptions <= checked


def test_southern_initials_and_merged_tones():
    rows = json.loads(files('better_speech').joinpath('data/curriculum.vi.json').read_text())['focusPhrases']
    for row in rows:
        assert not re.search(r'\bv[a-z]*', row['hint']), row['id']
        assert '[broken rising]' not in row['hint'], row['id']
        # Every hỏi/ngã word keeps the shared tone, including sữa in the coffee phrase.
        words = re.findall(r'[^\W\d_]+', row['l2'])
        merged_tones = sum(
            any(mark in unicodedata.normalize('NFD', word) for mark in ('\u0303', '\u0309'))
            for word in words
        )
        assert row['hint'].count('[dipping]') == merged_tones, row['id']
    metadata = json.loads(files('better_speech').joinpath('data/curriculum.vi.metadata.json').read_text())
    assert metadata['_meta']['accent'] == 'southern'


def test_every_syllable_has_its_lexical_tone_and_southern_initial():
    rows = json.loads(files('better_speech').joinpath('data/curriculum.vi.json').read_text())['focusPhrases']
    tones = {
        '\u0301': 'rising', '\u0300': 'falling',
        '\u0309': 'dipping', '\u0303': 'dipping', '\u0323': 'low',
    }
    for row in rows:
        words = re.findall(r'[^\W\d_]+', row['l2'].lower())
        syllables = row['hint'].split('-')
        assert len(words) == len(syllables), row['id']
        for word, syllable in zip(words, syllables, strict=True):
            decomposed = unicodedata.normalize('NFD', word)
            expected = next((tone for mark, tone in tones.items() if mark in decomposed), None)
            label = re.fullmatch(r'[a-z]+(?: \[(rising|falling|dipping|low)(, clipped)?\])?', syllable)
            assert label is not None, (row['id'], word, syllable)
            assert label[1] == expected, (row['id'], word, syllable)
            if label[2]:
                assert expected == 'low' and syllable.split()[0].endswith(('p', 't', 'k'))
            plain = ''.join(char for char in decomposed if not unicodedata.combining(char))
            if plain.startswith(('d', 'gi', 'v')):
                assert syllable.startswith('y'), (row['id'], word, syllable)
            if plain.startswith('đ'):
                assert syllable.startswith('d'), (row['id'], word, syllable)
            if plain.startswith('th'):
                assert syllable.startswith('t') and not syllable.startswith('th'), row['id']
            if plain.startswith('tr'):
                assert syllable.startswith('tr'), row['id']
            if word == 'gì':
                assert syllable == 'yee [falling]', row['id']
            if word == 'có':
                assert syllable == 'kaw [rising]', row['id']


@pytest.mark.parametrize(('phrase_id', 'syllable'), [
    ('da', 'yah [low]'),
    ('khong-co-gi', 'yee [falling]'),
    ('goi-la-gi', 'yee [falling]'),
    ('giup-minh-voi', 'yoop [rising]'),
    ('giu-tien-thua', 'yuu [dipping]'),
    ('vui-qua', 'yooy'),
    ('tieng-viet-mot-chut', 'yeek [low, clipped]'),
    ('mang-ve', 'yay [falling]'),
    ('duoc-roi', 'roy [falling]'),
])
def test_southern_pronunciation_examples(phrase_id, syllable):
    rows = json.loads(files('better_speech').joinpath('data/curriculum.vi.json').read_text())['focusPhrases']
    row = next(row for row in rows if row['id'] == phrase_id)
    assert syllable in row['hint'].split('-')


@pytest.mark.parametrize(('phrase_id', 'hint'), [
    ('xin-loi', 'sin-loy [dipping]'),
    ('giu-tien-thua', 'yuu [dipping]-teeng [falling]-tuuh [falling]-nyeh [rising]'),
    ('san-sang-chua', 'sahng [dipping]-sahng [falling]-chuuh'),
    ('doi-qua', 'doy [rising]-kwah [rising]'),
    ('tin-vui-ne', 'tin-yooy-neh [falling]'),
    ('cai-nay-bao-nhieu', 'kye [rising]-nigh [falling]-bow-nyeeoo-teeng [falling]'),
    ('hieu-roi', 'heeoo [dipping]-roy [falling]'),
    ('thich-qua', 'tit [rising]-kwah [rising]'),
    ('tinh-tien', 'tin [rising]-teeng [falling]'),
])
def test_reviewed_southern_hint_tones_and_syllables(phrase_id, hint):
    rows = json.loads(files('better_speech').joinpath('data/curriculum.vi.json').read_text())['focusPhrases']
    assert next(row for row in rows if row['id'] == phrase_id)['hint'] == hint


def test_every_record_has_draft_hint():
    rows = json.loads(files('better_speech').joinpath('data/curriculum.vi.json').read_text())['focusPhrases']
    assert len(rows) == 85
    # Full syllable-by-syllable tone labels need up to 100 characters.
    assert all(isinstance(r['hint'], str) and 0 < len(r['hint']) <= 100 for r in rows)
    assert all(r['hint'] == r['hint'].lower() for r in rows)
    metadata = files('better_speech').joinpath('data/curriculum.vi.metadata.json').read_text()
    assert 'Founder-editable drafts' in metadata
    assert all(r['hint'] for r in runtime_curriculum(profile()))


def test_hint_rendering_below_literal_and_never_spoken():
    plan = compile('Note.', profile(), ledger=EmptyLedger())
    sections, _ = present(replace(plan, executed=True))
    lines = next(s.text for s in sections if s.kind == 'lesson').splitlines()
    assert 'go' in lines[1] and '[just/let' in lines[1].replace('\\', '')
    assert lines[2] == '   *say: dee\\-toy*'
    assert all('dee-toy' not in clip.text for clip in plan.clips)


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
