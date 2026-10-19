import json
from dataclasses import FrozenInstanceError, replace
from pathlib import Path

import pytest

from tests.reference.planner import (
    LessonPlacement,
    RenderVoice,
    build_speech_plan,
    legacy_sequence,
)

FIXTURES = Path(__file__).parent / "fixtures/enrichment"
GOLDEN = json.loads((FIXTURES / "sequences.json").read_text())
MAIN = RenderVoice("google", "google-main", "en-US-main", 1.15, '{"pitch": 1}')
LEAD = RenderVoice("elevenlabs", "eleven_turbo_v2_5", "lead-id", 0.9)
ECHO = RenderVoice("elevenlabs", "eleven_turbo_v2_5", "echo-id", 0.85)
LESSON = {
    "id": "coffee",
    "lesson_type": "learning_sentence",
    "phrase": "Cho mình, một cà phê sữa đá, nha.",
    "translation": "One iced milk coffee, please.",
    "units": ["Cho mình", "một", "cà phê sữa đá", "nha"],
    "literal": ["give me", "one", "coffee milk ice", "[softener]"],
    "vocabulary": [],
    "structure_pauses": [],
}


@pytest.mark.parametrize("case", GOLDEN["cases"], ids=lambda c: c["name"])
def test_actual_v1_sequence_matrix(case):
    assert legacy_sequence(case["document"], case["config"], random=lambda: 0) == tuple(case["steps"])


@pytest.mark.parametrize("style,expected", [("16", "Xin – chào"), ("15", "Xin - chào"), ("off", "Xin chào")])
def test_legacy_annotation_renderer_wired_per_resolved_voice(style, expected):
    document = {"segments": [{"l2": "Xin chào", "l2Tiered": "Xin⟨3⟩chào", "l1": "Hello", "rest": ""}]}
    steps = legacy_sequence(document, {"pauseStyle": style}, random=lambda: 0)
    assert [s["text"] for s in steps if s.get("lang") == "l2"] == [expected, expected]


def placed(source, *, level=1, mode="open"):
    return LessonPlacement("coffee", len(source), "end", LEAD, ECHO, level, mode)


@pytest.mark.parametrize(
    "case", json.loads((FIXTURES / "sequences-owner.json").read_text())["cases"], ids=lambda c: c["name"]
)
def test_portable_owner_sequence_vectors(case):
    source = "Done."
    plan = build_speech_plan(
        source,
        inventory=[LESSON],
        placements=[placed(source, level=case["level"], mode=case["mode"])],
        main=MAIN,
        settings=case["settings"],
    )
    voiced = [s for s in plan.steps if s.kind == "tts"]
    roles = {"main": MAIN.voice, "lead": LEAD.voice, "echo": ECHO.voice}
    assert plan.status == "applied"
    assert [s.voice for s in voiced] == [roles[r] for r in case["voices"]]
    assert [s.purpose for s in voiced] == case["purposes"]
    assert [s.text for s in voiced if s.purpose == "lesson"] == case["lesson_texts"]
    assert "".join(s.text for s in voiced if s.purpose == "source") == source


def test_owner_main_plain_specialist_compiled_and_actual_request_fields():
    source = "The original message.\n"
    plan = build_speech_plan(
        source, inventory=[LESSON], placements=[placed(source)], main=MAIN, settings={"repeat": True}
    )
    assert plan.status == "applied"
    voiced = [s for s in plan.steps if s.kind == "tts"]
    assert [s.purpose for s in voiced] == ["source", "lesson", "translation", "lesson"]
    assert voiced[0].text == source
    assert voiced[1].text == LESSON["phrase"]
    assert voiced[1].provider == MAIN.provider and voiced[1].model == MAIN.model
    assert voiced[1].voice == MAIN.voice and voiced[1].rate == 0.9
    assert voiced[2].rate == MAIN.rate
    assert voiced[3].text == "Cho mình – một, cà phê sữa đá – nha."
    assert voiced[3].display_text == LESSON["phrase"]
    assert voiced[3].voice == LEAD.voice and voiced[3].model == LEAD.model
    assert all(s.input_mode == "text" and s.audio_format == "ogg" for s in voiced)
    # Role block supersedes repeat, never a third Vietnamese occurrence.
    assert len([s for s in voiced if s.purpose == "lesson"]) == 2


@pytest.mark.parametrize(
    "main", [MAIN, replace(MAIN, provider="elevenlabs", model="eleven_turbo_v2_5", voice="normal-lane")]
)
def test_owner_main_provider_is_not_fixed_onyx(main):
    plan = build_speech_plan(
        "Done.", inventory=[LESSON], placements=[placed("Done.")], main=main, settings={}
    )
    assert all(
        s.provider == main.provider and s.model == main.model and s.voice == main.voice
        for s in plan.steps
        if s.purpose in ("source", "translation") or s.purpose == "lesson" and s.voice == main.voice
    )


def test_owner_level_two_echo_reuses_lead_and_literal_rung():
    source = "Done."
    config = {"tailL1Lead": False, "repeat": True}
    first = build_speech_plan(
        source, inventory=[LESSON], placements=[placed(source, mode="close")], main=MAIN, settings=config
    )
    second = build_speech_plan(
        source,
        inventory=[LESSON],
        placements=[placed(source, level=2, mode="close")],
        main=MAIN,
        settings=config,
    )
    assert [s.voice for s in first.steps if s.purpose == "lesson"] == [LEAD.voice, ECHO.voice]
    assert [s.voice for s in second.steps if s.purpose == "lesson"] == [LEAD.voice, LEAD.voice]
    assert [s.text for s in second.steps if s.purpose == "literal"] == [
        "give me — one — coffee milk ice — [softener]"
    ]


def test_owner_explicit_second_voice_off_overrides_supplied_echo():
    plan = build_speech_plan(
        "Done.",
        inventory=[LESSON],
        placements=[placed("Done.", mode="close")],
        main=MAIN,
        settings={"tailL1Lead": False, "repeat": True, "repeatSecondVoice": False},
    )
    assert [s.voice for s in plan.steps if s.purpose == "lesson"] == [LEAD.voice, LEAD.voice]


@pytest.mark.parametrize(
    "settings,voices",
    [
        ({"tailL1Lead": False, "repeat": "false"}, [LEAD.voice]),
        ({"tailL1Lead": None}, [MAIN.voice, LEAD.voice]),
        ({"tailL1Lead": False}, [LEAD.voice]),
        ({"tailL1Lead": False, "repeat": True, "repeatSecondVoice": None}, [LEAD.voice, ECHO.voice]),
    ],
)
def test_owner_only_boolean_overrides_replace_defaults(settings, voices):
    plan = build_speech_plan(
        "Done.", inventory=[LESSON], placements=[placed("Done.")], main=MAIN, settings=settings
    )
    assert [s.voice for s in plan.steps if s.purpose == "lesson"] == voices


def test_owner_all_four_slots_actual_pacing_compiler():
    record = {**LESSON, "lesson_type": "connector"}
    profiles = {
        "start_1": {"p0": " / "},
        "start_2": {"p0": " + "},
        "end_1": {"p0": " : "},
        "end_2": {"p0": " ; "},
    }
    settings = {
        "tailL1Lead": False,
        "midL1Close": False,
        "repeat": True,
        "pacing": {"default": {"p1": ""}, "slots": profiles},
    }
    for position, offset, expected in (("start", 0, (" / ", " + ")), ("end", 5, (" : ", " ; "))):
        plan = build_speech_plan(
            "Done.",
            inventory=[record],
            placements=[LessonPlacement("coffee", offset, position, LEAD, ECHO)],
            main=MAIN,
            settings=settings,
        )
        lessons = [s for s in plan.steps if s.purpose == "lesson"]
        assert len(lessons) == 2
        assert expected[0] in lessons[0].text and expected[1] in lessons[1].text
        assert all(s.display_text == record["phrase"] for s in lessons)


def test_owner_explicit_long_break_generates_units_not_timestamp_splice():
    plan = build_speech_plan(
        "Done.",
        inventory=[LESSON],
        placements=[placed("Done.")],
        main=MAIN,
        settings={"longBreakSeconds": 1.5},
    )
    lessons = [s for s in plan.steps if s.purpose == "lesson"]
    assert lessons[0].text == LESSON["phrase"]  # narrator stays whole/plain
    assert [s.text for s in lessons[1:]] == LESSON["units"]
    assert [s.seconds for s in plan.steps if s.kind == "silence"].count(1.5) == 3


def test_owner_invalid_units_fall_back_even_with_explicit_long_break():
    record = {**LESSON, "units": ["invented source"]}
    plan = build_speech_plan(
        "Done.",
        inventory=[record],
        placements=[placed("Done.")],
        main=MAIN,
        settings={"longBreakSeconds": 1.5},
    )
    assert plan.status == "plain" and [s.text for s in plan.steps] == ["Done."]


@pytest.mark.parametrize("bad", [{"translation": 7}, {"literal": [7]}, {"phrase": 7}])
def test_invalid_lesson_speech_fields_preserve_source(bad):
    plan = build_speech_plan(
        "Done.", inventory=[{**LESSON, **bad}], placements=[placed("Done.")], main=MAIN, settings={}
    )
    assert plan.status == "plain" and "".join(s.text for s in plan.steps) == "Done."


@pytest.mark.parametrize(
    "settings", [{"pauseS": float("nan")}, {"pauseS": float("inf")}, {"pacing": object()}, []]
)
def test_invalid_optional_snapshot_preserves_complete_main_source(settings):
    source = "Complete source. " * 1000
    plan = build_speech_plan(
        source, inventory=[LESSON], placements=[placed(source)], main=MAIN, settings=settings
    )
    assert plan.status == "plain" and plan.reason == "invalid optional settings snapshot"
    assert plan.settings_json == "{}"
    assert "".join(s.text for s in plan.steps) == source
    assert all(s.voice == MAIN.voice for s in plan.steps)


def test_owner_source_coverage_at_paragraph_boundary_and_frozen_inputs():
    source = "First paragraph.\n\nSecond paragraph.\n"
    record = {**LESSON, "lesson_type": "connector"}
    offset = source.index("Second")
    settings = {"pacing": {"default": {"p0": " + "}}}
    plan = build_speech_plan(
        source,
        inventory=[record],
        placements=[LessonPlacement("coffee", offset, "start", LEAD, ECHO)],
        main=MAIN,
        settings=settings,
    )
    chunks = [s for s in plan.steps if s.purpose == "source"]
    assert "".join(s.text for s in chunks) == source
    for chunk in chunks:
        assert source[chunk.source_start : chunk.source_end] == chunk.text == chunk.display_text
    digest = plan.digest()
    settings["pacing"]["default"]["p0"] = "changed"
    record["phrase"] = "changed"
    assert plan.digest() == digest
    assert "following source" not in "".join(s.text for s in plan.steps)
    with pytest.raises(FrozenInstanceError):
        plan.steps[0].text = "changed"


@pytest.mark.parametrize("offset", [1, -1, 99])
def test_owner_invalid_offset_falls_back_whole_source(offset):
    record = {**LESSON, "lesson_type": "connector"}
    plan = build_speech_plan(
        "Original.",
        inventory=[record],
        placements=[LessonPlacement("coffee", offset, "start", LEAD, ECHO)],
        main=MAIN,
        settings={},
    )
    assert plan.status == "plain"
    assert "".join(s.text for s in plan.steps) == "Original."


def test_owner_code_fence_boundary_cannot_host_lesson():
    source = "```\nfirst\n\nsecond\n```"
    record = {**LESSON, "lesson_type": "connector"}
    plan = build_speech_plan(
        source,
        inventory=[record],
        placements=[LessonPlacement("coffee", source.index("second"), "start", LEAD, ECHO)],
        main=MAIN,
        settings={},
    )
    assert plan.status == "plain" and plan.steps[0].text == source


@pytest.mark.parametrize("separator", ["\n\n", "\n \n", "\n\t\n"])
@pytest.mark.parametrize("position", ["start", "end"])
def test_preparation_to_planner_accepts_both_sides_of_paragraph_separator(separator, position):
    from tests.reference.polyglot import prepare
    from tests.reference.stages import StageContext, StageInput

    source = f"First.{separator}Second."
    offset = len("First.") + (len(separator) if position == "start" else 0)
    slot_id = f"{'opener' if position == 'start' else 'closer'}:{offset}"
    record = {**LESSON, "lesson_type": "connector"}

    def call(route, payload, timeout):
        assert slot_id in {slot["id"] for slot in payload["slots"]}
        return json.dumps({"placements": [{"phraseId": record["id"], "slotId": slot_id}]})

    prepared = prepare(
        StageInput(source, "r1", "s1", "a1"),
        StageContext(180, lambda: 0),
        settings={"enabled": True},
        inventory=[record],
        exposure={},
        lane_id="test",
        selection_time="2026-09-25T12:00:00Z",
        seed=1,
        call=call,
    )
    assert prepared.status == "applied" and len(prepared.plan.placements) == 1
    placements = [
        LessonPlacement(p.phrase_id, p.offset, p.position, LEAD, ECHO) for p in prepared.plan.placements
    ]
    plan = build_speech_plan(source, inventory=[record], placements=placements, main=MAIN, settings={})
    assert plan.status == "applied", plan.reason
    chunks = [s for s in plan.steps if s.purpose == "source"]
    assert "".join(s.text for s in chunks) == source
    assert all(source[s.source_start : s.source_end] == s.text for s in chunks)
    assert any(s.lesson_id == record["id"] for s in plan.steps)


@pytest.mark.parametrize("source", ["First.\n\n```\ncode\n\ninside\n```", "First.\n \n~~~~\ncode\n\ninside"])
def test_stage_and_planner_share_protected_code_boundaries(source):
    from tests.reference.polyglot import structural_slots

    record = {**LESSON, "lesson_type": "connector"}
    invalid = source.index("inside")
    assert invalid not in {s.offset for s in structural_slots(source)}
    plan = build_speech_plan(
        source,
        inventory=[record],
        placements=[LessonPlacement(record["id"], invalid, "start", LEAD, ECHO)],
        main=MAIN,
        settings={},
    )
    assert plan.status == "plain"
    assert "".join(s.text for s in plan.steps) == source


def test_owner_duplicate_placement_falls_back_not_extra_exposure():
    plan = build_speech_plan(
        "Done.", inventory=[LESSON], placements=[placed("Done.")] * 2, main=MAIN, settings={}
    )
    assert plan.status == "plain"
    assert not any(s.lesson_id for s in plan.steps)


def test_owner_overbudget_fallback_keeps_complete_source_and_codepoints():
    source = "x" * 3999 + "😀" + " continuing\n" * 1400
    plan = build_speech_plan(source, inventory=[LESSON], placements=[placed(source)], main=MAIN, settings={})
    assert plan.status == "plain"
    assert "".join(s.text for s in plan.steps) == source
    assert all(len(s.text.encode("utf-16-le")) // 2 <= 4000 for s in plan.steps)
    assert len(source) > 12000


def test_owner_actual_settings_change_plan_identity():
    kwargs = {"source": "Done.", "inventory": [LESSON], "placements": [placed("Done.")], "settings": {}}
    a = build_speech_plan(main=MAIN, **kwargs)
    b = build_speech_plan(main=replace(MAIN, model="other-model"), **kwargs)
    c = build_speech_plan(main=replace(MAIN, voice_settings_json='{"pitch": 2}'), **kwargs)
    assert len({a.digest(), b.digest(), c.digest()}) == 3
