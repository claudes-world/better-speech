from dataclasses import replace

from better_speech import compile
from better_speech.transcript import present
from tests.test_model_free_audio import EmptyLedger, profile


def test_only_executed_lesson_has_numbered_collapsible_block_and_links():
    plan = compile("Hello.\n\n[Docs](https://example.org/docs).", profile(), ledger=EmptyLedger())
    assert "Vietnamese lesson" not in present(plan)[1]
    sections, markdown = present(replace(plan, executed=True))
    assert [s.kind for s in sections] == ["title", "lesson", "transcript", "links"]
    assert sections[1].collapsible
    assert "<summary>🇻🇳 Vietnamese lesson</summary>\n\n1. **" in markdown
    assert "[Docs](<https://example.org/docs>)" in markdown
    assert plan.source in sections[2].text


def test_source_html_cannot_close_collapsed_transcript():
    source = "Hello.\n\n</details><script>bad</script>"
    plan = compile(source, profile(enabled=False), ledger=EmptyLedger())
    sections, markdown = present(plan)
    assert sections[-1].text == source
    assert "&lt;/details&gt;&lt;script&gt;bad&lt;/script&gt;" in markdown
    assert markdown.count("</details>") == 1


def test_public_render_numbers_lessons_in_first_audible_order(tmp_path):
    import json

    from better_speech import render
    from tests.test_model_free_audio import ToneProvider

    cfg = replace(profile(state_dir=str(tmp_path / "state")), model="claude")

    def select(payload, *, timeout):
        # Reservation is the closing an-thoi lesson. Proposals arrive in reverse
        # playback order; repeating an ID must not duplicate its manifest entry.
        middle = next(slot for slot in payload["slots"] if slot.startswith("start:") and slot != "start:0")
        return json.dumps(
            {
                "placements": [
                    {"phraseId": "hieu-roi", "slotId": middle},
                    {"phraseId": "khong-sao-dau", "slotId": "start:0"},
                    {"phraseId": "khong-sao-dau", "slotId": middle},
                ]
            }
        )

    # Both contextual phrases are already known so the new-phrase cap still
    # allows two contextual lessons plus the unseen reserved closing lesson.
    from better_speech.curriculum import runtime_curriculum

    records = {
        row["ledger_identity"]: {"dueAt": "2099-01-01T00:00:00.000Z"}
        for row in runtime_curriculum(cfg)
        if row["id"] in {"hieu-roi", "khong-sao-dau"}
    }

    class KnownPhrases(EmptyLedger):
        def snapshot(self, *args, **kwargs):
            return records

    cfg = replace(cfg, intensity=2)
    plan = compile("# First paragraph.\n\nSecond paragraph.", cfg, ledger=KnownPhrases(), model_call=select)
    provider = ToneProvider()
    result = render(plan, {"google": provider, "elevenlabs": provider}, tmp_path / "cache")
    assert result.audio_path and result.status == "bilingual", result.diagnostics
    expected = ["khong-sao-dau", "hieu-roi", "an-thoi"]
    assert [lesson.id for lesson in result.plan.lessons if lesson.record.get("category") not in ("closing", "bookend")] == expected
    audible_keys = list(dict.fromkeys(c.phrase_key for c in provider.calls if c.phrase_key))
    assert [lesson.phrase_key for lesson in result.plan.lessons] == audible_keys
    lesson_block = next(section.text for section in result.sections if section.kind == "lesson")
    rows = [line for line in lesson_block.splitlines() if line[:1].isdigit()]
    assert len(rows) == 4  # the closing is numbered as the next item
    for number, phrase, line in zip((1, 2, 3, 4), ("Không sao đâu", "Hiểu rồi", "Ăn thôi", "Đi thôi"), rows):
        assert line.startswith(f"{number}. **") and phrase in line
    assert result.markdown.index(rows[0]) < result.markdown.index(rows[1]) < result.markdown.index(rows[2]) < result.markdown.index(rows[3])


def _three_lesson_plan():
    import json

    from better_speech.curriculum import runtime_curriculum
    from better_speech.types import Lesson

    cfg = profile()
    rows = {row['id']: row for row in runtime_curriculum(cfg)}
    lessons = tuple(
        Lesson(key, rows[key]['ledger_identity'], json.dumps(rows[key]))
        for key in ('an-thoi', 'hieu-roi', 'cho-minh-mot-cai-khan-a')
    )
    return replace(compile('Note.', cfg, ledger=EmptyLedger()), lessons=lessons, executed=True)


def test_hint_fences_stand_alone_between_numbered_items():
    import re

    from markdown_it import MarkdownIt

    sections, _ = present(_three_lesson_plan())
    text = next(section.text for section in sections if section.kind == 'lesson')
    tokens = MarkdownIt().parse(text)
    assert len([token for token in tokens if token.type == 'ordered_list_open']) == 3
    assert [token.attrGet('start') for token in tokens if token.type == 'ordered_list_open'] == [None, 2, 3]
    assert len([token for token in tokens if token.type == 'list_item_open']) == 3
    fences = [token for token in tokens if token.type == 'fence']
    assert all(token.level == 0 for token in fences)
    assert [token.content for token in fences] == [
        'ahng - thoy\n', 'hee-uh-oo⌄ - roy↓\n',
        'chaw - min↓ - mohk• - kye↑ - khahng tahm↑ - ah•\n',
    ]
    assert len([token for token in tokens if token.type == 'hr']) == 2
    items = text.split('\n\n---\n\n')
    assert len(items) == 3
    for number, item in enumerate(items, 1):
        assert item.startswith(f'{number}. **')
        before, fence = item.split('\n\n')
        phrase, translation, literal = before.split('\n')  # phrase, translation, literal
        assert phrase.endswith('**  ') and translation.endswith('  ') and not translation.endswith('   ')
        assert literal.startswith('   _') and literal.endswith('_') and '—' not in literal
        assert fence.startswith('```\n') and fence.endswith('\n```')


def test_host_render_preserves_standalone_hint_fences_and_numbering():
    import importlib.util
    import json
    from pathlib import Path

    import pytest
    from markdown_it import MarkdownIt

    host_path = Path('/opt/worldos/current/.venv/lib/python3.13/site-packages/'
                     'worldos_control_plane_app/speech_transcript.py')
    if not host_path.is_file():
        pytest.skip('WorldOS host speech_transcript is not installed')
    spec = importlib.util.spec_from_file_location('host_speech_transcript', host_path)
    host = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(host)
    plan = _three_lesson_plan()
    parts = host._with_lessons(
        [{'sections': [{'kind': 'transcript', 'text': plan.source}]}], plan.to_dict()
    )
    assert len(parts) == 1
    rich, plain = host.render(parts[0])
    lesson = parts[0]['sections'][0]['text']
    assert '\n```  \nahng - thoy  \n```' in rich
    assert lesson in plain
    # Parse the actual host output, including its two-space line joins and wrapper.
    tokens = MarkdownIt('commonmark', {'html': True}).parse(rich)
    assert len([token for token in tokens if token.type == 'ordered_list_open']) == 3
    assert [token.attrGet('start') for token in tokens if token.type == 'ordered_list_open'] == [None, 2, 3]
    assert len([token for token in tokens if token.type == 'list_item_open']) == 3
    fences = [token for token in tokens if token.type == 'fence']
    assert all(token.level == 0 for token in fences)
    assert [token.content.rstrip() for token in fences] == [
        'ahng - thoy', 'hee-uh-oo⌄ - roy↓',
        'chaw - min↓ - mohk• - kye↑ - khahng tahm↑ - ah•',
    ]

    row = plan.lessons[0].record
    row['hint'] = 'dee\ntoy ```'
    custom = replace(plan, lessons=(replace(plan.lessons[0], record_json=json.dumps(row)),) + plan.lessons[1:])
    custom_parts = host._with_lessons(
        [{'sections': [{'kind': 'transcript', 'text': plan.source}]}], custom.to_dict()
    )
    custom_rich, _ = host.render(custom_parts[0])
    custom_tokens = MarkdownIt('commonmark', {'html': True}).parse(custom_rich)
    assert len([token for token in custom_tokens if token.type == 'list_item_open']) == 3
    custom_fences = [token for token in custom_tokens if token.type == 'fence']
    assert len(custom_fences) == 3
    assert all(token.level == 0 for token in custom_fences)
    assert custom_fences[0].content.rstrip() == 'dee toy ```'


def test_custom_hint_newlines_backticks_and_html_stay_in_one_code_line():
    import json

    from markdown_it import MarkdownIt

    plan = _three_lesson_plan()
    row = plan.lessons[0].record
    row['hint'] = 'dee\ntoy ``` <soft> & </details>'
    plan = replace(plan, lessons=(replace(plan.lessons[0], record_json=json.dumps(row)),) + plan.lessons[1:])
    sections, markdown = present(plan)
    text = next(section.text for section in sections if section.kind == 'lesson')
    tokens = MarkdownIt().parse(text)
    assert len([token for token in tokens if token.type == 'list_item_open']) == 3
    fences = [token for token in tokens if token.type == 'fence']
    assert all(token.level == 0 for token in fences)
    assert len(fences) == 3
    assert fences[0].content == 'dee toy ``` <soft> & </details>\n'
    rendered = MarkdownIt('commonmark', {'html': True}).render(markdown)
    assert 'dee toy ``` &lt;soft&gt; &amp; &lt;/details&gt;' in rendered
    assert '&amp;lt;' not in rendered
    assert '<summary>Transcript</summary>' in rendered


def test_item_layout_orders_translation_pronunciation_literal_without_stray_blanks():
    from dataclasses import dataclass

    @dataclass
    class L:
        record: dict

    @dataclass
    class P:
        source: str
        executed: bool
        lessons: tuple

    rows = (
        {"phrase": "Xin chào", "translation": "Hello", "hint": "sin chow", "literal": "ask — hello"},
        {"phrase": "Cảm ơn", "translation": "Thanks", "literal": None},
        {"phrase": "Cảm ơn", "translation": "Thanks", "hint": "kahm⌄ uhng"},
        {"phrase": "Đi thôi", "translation": "Let us go", "category": "closing", "literal": "go — let's"},
    )
    sections, _ = present(P("Note.", True, tuple(L(r) for r in rows)))
    text = next(s.text for s in sections if s.kind == "lesson")
    assert text == (
        "1. **Xin chào**  \n   Hello  \n   _ask hello_\n\n```\nsin chow\n```\n\n---\n\n"
        "2. **Cảm ơn**  \n   Thanks\n\n---\n\n"
        "3. **Cảm ơn**  \n   Thanks\n\n```\nkahm⌄ uhng\n```\n\n---\n\n"
        "4. **Đi thôi**  \n   Let us go  \n   _go let's_"
    )


def test_item_layout_matches_liam_preview_exactly():
    from better_speech.curriculum import runtime_curriculum
    from better_speech.config import Profile

    row = next(r for r in runtime_curriculum(Profile()) if r.get("id") == "minh-co-tin-nay")
    plan = P2("Note.", True, (L2(dict(row, phrase=row["l2"], translation=row["l1"])),
                              L2({"phrase": "Cái này bao nhiêu tiền?", "translation": "How much is this?",
                                  "units": ["Cái này", "bao nhiêu", "tiền"],
                                  "l1_literal": [["Cái", "[classifier]"], ["này", "this"],
                                                 ["bao nhiêu", "how much"], ["tiền", "money"]],
                                  "hint": "kye↑ - nigh↓ - bow nyee-uh-oo - teeyen↓"})))
    text = next(s.text for s in present(plan)[0] if s.kind == "lesson")
    assert text == (
        "1. **Mình có tin này\\.**  \n"
        "   I have some news\\.  \n"
        "   _I have news this_\n"
        "\n"
        "```\n"
        "min↓ - kaw↑ - tin - nigh↓\n"
        "```\n"
        "\n"
        "---\n"
        "\n"
        "2. **Cái này • bao nhiêu • tiền?**  \n"
        "   How much is this?  \n"
        "   _\\[classifier\\] this how much money_\n"
        "\n"
        "```\n"
        "kye↑ - nigh↓ - bow nyee-uh-oo - teeyen↓\n"
        "```"
    )
    assert "—" not in text and not text.endswith("---")


from dataclasses import dataclass as _dc


@_dc
class L2:
    record: dict


@_dc
class P2:
    source: str
    executed: bool
    lessons: tuple
