import json
from pathlib import Path

import pytest

from better_speech.curriculum import strip_tiers
from tests.reference.legacy_pacing import (
    DEFAULT_PAUSE_STYLE,
    DEFAULT_PAUSE_TEXT_MODE,
    PAUSE_STYLES,
    WORD_SEAM_MARKER,
    has_tier_sentinels,
    is_known_pause_style,
    is_pause_text_mode,
    render_pauses_for_audio,
    render_pauses_for_text,
)

TIERED = "Cà phê⟨1⟩nóng⟨3⟩hay là⟨3⟩cà phê⟨1⟩đá?"
PLAIN = "Cà phê nóng hay là cà phê đá?"

GOLDENS = json.loads((Path(__file__).parent / "fixtures/enrichment/legacy-pauses.json").read_text())

OPERATIONS = {
    "stripTiers": strip_tiers,
    "hasTierSentinels": has_tier_sentinels,
    "renderPausesForAudio": render_pauses_for_audio,
    "renderPausesForText": render_pauses_for_text,
    "isKnownPauseStyle": is_known_pause_style,
    "isPauseTextMode": is_pause_text_mode,
}


@pytest.mark.parametrize("case", GOLDENS["contract_cases"])
def test_original_portable_contracts(case):
    assert case["source_test"] in GOLDENS["original_test_names"]
    op, args = case["op"], case["args"]
    if op == "idempotence":
        render = OPERATIONS[args[0]]
        once = render(*args[1:])
        actual = {"once": once, "twice": render(once, *args[2:])}
    elif op == "registry":
        # Expose the current flat Python map in the original registry's shape.
        styles = {}
        for name, marks in PAUSE_STYLES.items():
            tiers = {str(i): marks[i] for i in (1, 2, 3)}
            styles[name] = {
                "id": name,
                "text": tiers,
                "audio": {p: tiers for p in ("elevenlabs", "openai", "google")},
            }
        actual = {
            "styles": styles,
            "default_style": DEFAULT_PAUSE_STYLE,
            "default_mode": DEFAULT_PAUSE_TEXT_MODE,
            "word_marker": WORD_SEAM_MARKER,
            "banned": ["—"],
        }
    elif op == "bannedSeams":
        actual = any("—" in mark for marks in PAUSE_STYLES.values() for mark in marks)
    else:
        actual = OPERATIONS[op](*args)
    assert actual == case["expected"]


@pytest.mark.parametrize("case", GOLDENS["cases"], ids=lambda case: case["name"])
def test_original_bun_goldens(case):
    render = render_pauses_for_audio if case["op"] == "audio" else render_pauses_for_text
    assert render(case["text"], case["arg"], case["style"]) == case["expected"]


def test_every_original_pause_test_has_portable_evidence():
    covered = {case.get("source_test") for case in GOLDENS["cases"] + GOLDENS["contract_cases"]}
    assert set(GOLDENS["original_test_names"]) <= covered


@pytest.mark.parametrize("provider", ["elevenlabs", "openai", "google"])
@pytest.mark.parametrize(
    "style,expected",
    [
        ("16", "Cà phê • nóng – hay là – cà phê • đá?"),
        ("15", "Cà phê • nóng - hay là - cà phê • đá?"),
        ("off", PLAIN),
    ],
)
def test_original_styles_and_provider_maps(provider, style, expected):
    assert render_pauses_for_audio(TIERED, provider, style) == expected
    assert render_pauses_for_text(TIERED, "pause", style) == expected
    assert render_pauses_for_audio("một⟨0⟩cà phê", provider, style) == "một cà phê"


@pytest.mark.parametrize("style", ["nope", "toString", "constructor", "hasOwnProperty", "__proto__"])
def test_unknown_and_prototype_style_falls_open(style):
    assert render_pauses_for_audio(TIERED, "elevenlabs", style) == PLAIN
    assert render_pauses_for_text(TIERED, "pause", style) == PLAIN
    assert not is_known_pause_style(style)


@pytest.mark.parametrize("text", ["a⟨7⟩b⟨1⟩c", "a⟨1b⟨1⟩c", "a1⟩b⟨1⟩c"])
def test_malformed_tier_discards_all_styling(text):
    assert render_pauses_for_audio(text, "elevenlabs") == "a b c"
    for mode in ("none", "pause", "word"):
        assert render_pauses_for_text(text, mode) == "a b c"


def test_original_tier_two_text_modes_and_idempotence():
    assert render_pauses_for_audio("a⟨2⟩b", "elevenlabs") == "a ... b"
    assert render_pauses_for_text(TIERED) == PLAIN
    assert render_pauses_for_text("một⟨0⟩cà phê⟨1⟩sữa", "word") == "một • cà phê • sữa"
    for mode in ("none", "pause", "word"):
        once = render_pauses_for_text(TIERED, mode)
        assert not has_tier_sentinels(once)
        assert render_pauses_for_text(once, mode) == once
    assert render_pauses_for_audio(TIERED, "unknown") == PLAIN


def test_registry_and_ascii_sentinel_ecmascript_whitespace():
    assert is_known_pause_style("16") and is_pause_text_mode("word")
    assert not is_known_pause_style({}) and not is_pause_text_mode("loud")
    assert all("—" not in mark for marks in PAUSE_STYLES.values() for mark in marks)
    assert render_pauses_for_audio("\ufeffa⟨1⟩b\ufeff", "openai") == "a • b"
    assert render_pauses_for_audio("a⟨١⟩b", "openai") == "a١b"
    assert render_pauses_for_audio("\u0085a⟨1⟩b\u0085", "openai") == "\u0085a • b\u0085"


def test_warning_deduplication_reset_and_logging_failure(monkeypatch):
    import tests.reference.legacy_pacing as pacing

    messages = []
    monkeypatch.setattr(pacing._LOGGER, "warning", lambda *args: messages.append(args))
    pacing.reset_pause_warnings()
    for _ in range(2):
        assert render_pauses_for_audio(TIERED, "openai", "bad") == PLAIN
        assert render_pauses_for_text(TIERED, "pause", "bad") == PLAIN
    assert len(messages) == 1  # the same style key across text/audio
    pacing.reset_pause_warnings()
    render_pauses_for_audio(TIERED, "openai", "bad")
    assert len(messages) == 2

    def broken_log(*args):
        raise RuntimeError("diagnostic sink unavailable")

    monkeypatch.setattr(pacing._LOGGER, "warning", broken_log)
    pacing.reset_pause_warnings()
    assert render_pauses_for_audio("a⟨9⟩b", "openai") == "a b"
    assert render_pauses_for_text("a⟨9⟩b", "word") == "a b"
    assert render_pauses_for_audio(TIERED, "unknown") == PLAIN
    pacing.reset_pause_warnings()
