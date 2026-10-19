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


def test_public_render_numbers_lessons_in_first_audible_order(tmp_path):
    import json

    from better_speech import render
    from tests.test_model_free_audio import ToneProvider

    cfg = replace(profile(state_dir=str(tmp_path / "state")), model="claude")

    def select(payload, *, timeout):
        # Reservation is the closing di-thoi lesson. Proposals arrive in reverse
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

    class KnownPhrases:
        def snapshot(self, *args, **kwargs):
            return records

    cfg = replace(cfg, intensity=2)
    plan = compile("First paragraph.\n\nSecond paragraph.", cfg, ledger=KnownPhrases(), model_call=select)
    provider = ToneProvider()
    result = render(plan, {"google": provider, "elevenlabs": provider}, tmp_path / "cache")
    assert result.audio_path and result.status == "bilingual", result.diagnostics
    expected = ["khong-sao-dau", "hieu-roi", "di-thoi"]
    assert [lesson.id for lesson in result.plan.lessons] == expected
    audible_keys = list(dict.fromkeys(c.phrase_key for c in provider.calls if c.phrase_key))
    assert [lesson.phrase_key for lesson in result.plan.lessons] == audible_keys
    lesson_block = next(section.text for section in result.sections if section.kind == "lesson")
    rows = [line for line in lesson_block.splitlines() if line[:1].isdigit()]
    assert len(rows) == 3
    for number, phrase, line in zip((1, 2, 3), ("Không sao đâu", "Hiểu rồi", "Đi thôi"), rows):
        assert line.startswith(f"{number}. **") and phrase in line
    assert result.markdown.index(rows[0]) < result.markdown.index(rows[1]) < result.markdown.index(rows[2])
