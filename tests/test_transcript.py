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
    assert "<summary>Vietnamese lesson</summary>\n\n1. **" in markdown
    assert "[Docs](<https://example.org/docs>)" in markdown
    assert plan.source in sections[2].text


def test_source_html_cannot_close_collapsed_transcript():
    source = "Hello.\n\n</details><script>bad</script>"
    plan = compile(source, profile(enabled=False), ledger=EmptyLedger())
    sections, markdown = present(plan)
    assert sections[-1].text == source
    assert "&lt;/details&gt;&lt;script&gt;bad&lt;/script&gt;" in markdown
    assert markdown.count("</details>") == 1
