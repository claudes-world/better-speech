import json
from dataclasses import asdict
from pathlib import Path

import pytest

from tests.reference.voices import (
    LOCKED_LEAD_WEIGHTS,
    LOCKED_POOL,
    OPENAI_L2_POOL,
    VOICE_POOL,
    VoiceRef,
    choose_lesson_voices,
    exposure_role,
    is_valid_voice_id,
    l2_rate_for,
    resolve_l1_voice,
    resolve_l2_voice,
    resolve_repeat_voice,
    resolve_role_settings,
    sentence_position,
)

FIXTURES = Path(__file__).parent / "fixtures/enrichment"
VOICES = json.loads((FIXTURES / "voices.json").read_text())
ECHO = json.loads((FIXTURES / "echo.json").read_text())


def serialized(voice):
    return {key: value for key, value in asdict(voice).items() if value is not None}


@pytest.mark.parametrize("case", VOICES["cases"])
def test_original_voice_golden_and_exact_rng_consumption(case):
    draws = iter(case["rng"])
    voice, fallback = resolve_l2_voice(case["config"], random=lambda: next(draws))
    assert serialized(voice) == case["expected"]
    assert not fallback
    assert l2_rate_for(voice, case["config"]) == case["rate"]
    assert list(draws) == []


@pytest.mark.parametrize("case", ECHO["cases"])
def test_original_bun_extracted_echo_golden(case):
    primary, _ = resolve_l2_voice({}, random=lambda: case["rng"])
    echo = resolve_repeat_voice(primary, repeat=True, random=lambda: case["rng"])
    assert serialized(primary) == case["primary"]
    assert serialized(echo) == case["echo"]


@pytest.mark.parametrize("case", VOICES["roles"])
def test_original_voice_role_resolution_golden(case):
    assert exposure_role(case["segment"], case["config"]) is case["expected"]


@pytest.mark.parametrize("bad", [None, 123, {}, ["x"], "../../history", "onyx?admin=1", ""])
def test_original_invalid_voice_falls_back(bad):
    assert not is_valid_voice_id(bad)
    assert resolve_l2_voice({"l2Voice": bad, "l1InPool": True}, random=lambda: 0) == (VOICE_POOL[0], True)
    assert resolve_l1_voice({"l1Voice": bad}) == (VoiceRef("openai", "onyx"), True)


def test_original_explicit_provider_detection_and_narrator():
    def no_rng():
        pytest.fail("explicit voice should not consume RNG")

    assert resolve_l2_voice({"l2Voice": "vi-VN-Custom"}, random=no_rng) == (
        VoiceRef("google", "vi-VN-Custom"),
        False,
    )
    assert resolve_l2_voice({"l2Voice": "opaque"}, random=no_rng) == (VoiceRef("elevenlabs", "opaque"), False)
    assert resolve_l2_voice({"l2Voice": "opaque", "l2Provider": "google"}, random=no_rng) == (
        VoiceRef("google", "opaque"),
        False,
    )
    # A known pool voice wins over a conflicting explicit provider, as V1 does.
    assert (
        resolve_l2_voice({"l2Voice": VOICE_POOL[0].voice, "l2Provider": "google"}, random=no_rng)[0]
        == VOICE_POOL[0]
    )
    assert resolve_l1_voice({}) == (VoiceRef("openai", "onyx"), False)
    assert all(v.voice != "onyx" for v in OPENAI_L2_POOL)


@pytest.mark.parametrize(
    "rest,expected",
    [
        (None, "tail"),
        ("", "tail"),
        ("   ", "tail"),
        ("!", "tail"),
        ("…", "tail"),
        ("còn nữa", "mid"),
        ("中文", "mid"),
        ("٣", "mid"),
        ("⟦PGa1b2c3d4.0⟧", "tail"),
        ("⟦PGa1b2c3d4.0⟧ and more", "mid"),
    ],
)
def test_original_sentence_position(rest, expected):
    assert sentence_position({"rest": rest}) == expected


def test_original_echo_disabled_does_not_consume_rng():
    def no_rng():
        pytest.fail("disabled echo must not consume RNG")

    assert resolve_repeat_voice(VOICE_POOL[0], random=no_rng) is None
    assert resolve_repeat_voice(VOICE_POOL[0], repeat=True, second_voice=False, random=no_rng) is None


def test_role_precedence_preserves_false_and_omission():
    assert resolve_role_settings(
        {"tailL1Lead": False, "midL1Lead": True}, {"midL1Lead": False, "tailL1Lead": None}
    ) == {"tailL1Lead": False, "midL1Lead": False, "midL1Close": True, "l1InPool": False}


def test_owner_locked_roster_and_weights():
    owner = json.loads((FIXTURES / "voices-owner.json").read_text())
    assert [serialized(voice) for voice in LOCKED_POOL] == [
        {key: row[key] for key in ("provider", "voice", "region")} for row in owner["voices"]
    ]
    assert LOCKED_LEAD_WEIGHTS == {row["voice"]: row["weight"] for row in owner["voices"]}
    assert len(LOCKED_POOL) == 11
    assert all(v.provider == "elevenlabs" for v in LOCKED_POOL)
    assert sum(LOCKED_LEAD_WEIGHTS.values()) == 26
    assert LOCKED_POOL[0].voice == "ZvBK3ITp9pAzXbi1yZZH"
    assert LOCKED_LEAD_WEIGHTS["x4KAhuXs2G8TfK9Zr7Q4"] == 3


@pytest.mark.parametrize(
    "case", json.loads((FIXTURES / "voices-owner.json").read_text())["cases"], ids=lambda c: c["name"]
)
def test_portable_owner_selection_vectors(case):
    draws = iter(case["draws"])
    main = VoiceRef("google", "lane-owned-main")
    choice = choose_lesson_voices(main=main, random=lambda: next(draws), **case["settings"])
    assert choice.main == main
    assert choice.lead == LOCKED_POOL[case["lead_index"]]
    assert choice.echo == LOCKED_POOL[case["echo_index"]]
    assert list(draws) == []


@pytest.mark.parametrize("roll,index", [(0, 0), (1 / 26, 1), (3 / 26, 2), (7 / 26, 3), (0.9999, 10)])
def test_owner_weighted_interval_boundaries(roll, index):
    choice = choose_lesson_voices(main=LOCKED_POOL[0], random=lambda: roll, repeat=False)
    assert choice.lead == LOCKED_POOL[index]


def test_owner_weighted_lead_and_lane_main_not_hardcoded_onyx():
    narrator = VoiceRef("google", "en-US-Authoritative")
    draws = iter([0.99, 0])
    choice = choose_lesson_voices(main=narrator, random=lambda: next(draws))
    assert choice.main == narrator
    assert choice.lead == LOCKED_POOL[-1]
    assert choice.echo == LOCKED_POOL[0]
    assert choice.echo.region != choice.lead.region
    assert list(draws) == []


def test_owner_zero_weights_exclusions_one_voice_and_empty_pool():
    main = VoiceRef("elevenlabs", "Main")
    weights = {voice.voice: 0 for voice in LOCKED_POOL}
    assert (
        choose_lesson_voices(main=main, weights=weights, random=lambda: pytest.fail("empty pool rolled"))
        is None
    )
    weights[LOCKED_POOL[2].voice] = 5
    choice = choose_lesson_voices(main=main, weights=weights, random=lambda: 0.5)
    assert choice.lead == choice.echo == LOCKED_POOL[2]
    assert (
        choose_lesson_voices(
            main=main,
            weights=weights,
            excluded=[LOCKED_POOL[2].voice],
            random=lambda: pytest.fail("excluded pool rolled"),
        )
        is None
    )


def test_owner_one_region_echo_other_voice_then_reuses_primary():
    pool = LOCKED_POOL[:2]
    draws = iter([0, 0])
    choice = choose_lesson_voices(main=pool[0], pool=pool, random=lambda: next(draws))
    assert choice.lead == pool[0] and choice.echo == pool[1]
    assert choice.echo.region == choice.lead.region
    choice = choose_lesson_voices(main=pool[0], pool=pool[:1], random=lambda: 0)
    assert choice.echo == choice.lead


@pytest.mark.parametrize("settings", [{"level": 2}, {"repeat": False}, {"second_voice": False}])
def test_owner_recall_and_disabled_echo_use_one_roll(settings):
    draws = iter([0])
    choice = choose_lesson_voices(main=LOCKED_POOL[0], random=lambda: next(draws), **settings)
    assert choice.echo == choice.lead
    assert list(draws) == []


@pytest.mark.parametrize("weight", [-1, 6, float("nan"), float("inf"), True, "4"])
def test_owner_invalid_weight_rejected_for_plain_stage_fallback(weight):
    with pytest.raises(ValueError):
        choose_lesson_voices(main=LOCKED_POOL[0], weights={LOCKED_POOL[0].voice: weight}, random=lambda: 0)
