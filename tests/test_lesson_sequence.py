from copy import deepcopy

import pytest

from better_speech.planner import LessonPlacement, sequence_clips
from better_speech.types import Voice


@pytest.mark.parametrize("position", ["start", "end"])
@pytest.mark.parametrize("mode", ["open", "close"])
@pytest.mark.parametrize("repeat", [False, True])
@pytest.mark.parametrize("level", [1, 2])
@pytest.mark.parametrize("literal", [None, "give — me — coffee", ["give me", "coffee"]])
def test_every_placement_speaks_translation_without_literal(position, mode, repeat, level, literal):
    source = "Done."
    record = {
        "id": "coffee",
        "lesson_type": "connector",
        "phrase": "Cho mình cà phê.",
        "translation": "Coffee, please.",
        "units": ["Cho mình", "cà phê"],
        "l1_literal": [["Cho", "give"], ["mình", "me"], ["cà phê", "coffee"]],
    }
    if literal is not None:
        record["literal"] = literal
    original = deepcopy(record)
    main = Voice("narrator", "google", "neural2", 1.05)
    lead, echo = Voice("lead"), Voice("echo")
    offset = 0 if position == "start" else len(source)
    clips, reason = sequence_clips(
        source,
        inventory=[record],
        placements=[LessonPlacement("coffee", offset, position, lead, echo, level, mode)],
        main=main,
        settings={"repeat": repeat, "repeatPauseS": 1.3, "pauseS": 0.7},
    )
    assert reason is None
    spoken = [clip for clip in clips if clip.purpose != "source"]
    expected = [("lesson", "Cho mình, cà phê.", "lead", 0.25),
                ("translation", "Coffee, please.", "narrator", 0.7)]
    if repeat and level == 1:
        expected[1] = ("translation", "Coffee, please.", "narrator", 1.3)
        expected.append(("lesson", "Cho mình, cà phê.", "echo", 0.25))
    assert [(c.purpose, c.text, c.voice, c.pause_after_s) for c in spoken] == expected
    assert "".join(c.text for c in clips if c.purpose == "source") == source
    assert record == original
