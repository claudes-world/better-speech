import copy
import hashlib
import json
import unicodedata
from pathlib import Path

import pytest

from better_speech.cache import (
    CLIP_SCHEMA,
    _encode,
    _number,
    clip_identity,
)
from tests.reference.identity import legacy_phrase_bytes, legacy_phrase_key, rendition_identity

FIXTURES = Path(__file__).parent / "fixtures/enrichment"
LEGACY = json.loads((FIXTURES / "cache-keys.json").read_text())
OWNER = json.loads((FIXTURES / "cache-keys-owner.json").read_text())


def inputs(case=None):
    value = copy.deepcopy((case or OWNER["cases"][0])["input"])
    assert value.pop("schema_version") == CLIP_SCHEMA
    value["response_format"] = value.pop("format")
    return value


@pytest.mark.parametrize("case", LEGACY["cases"])
def test_original_v1_bun_bytes_and_hash(case):
    assert legacy_phrase_bytes(**case["input"]) == case["canonical"].encode()
    assert legacy_phrase_key(**case["input"]) == case["sha256"]


@pytest.mark.parametrize("case", OWNER["cases"])
def test_extended_bun_bytes_and_hash(case):
    identity = clip_identity(**inputs(case))
    assert identity.canonical_bytes == case["canonical"].encode()
    assert identity.cache_key == case["sha256"]
    assert identity.input_sha256 == hashlib.sha256(identity.text.encode()).hexdigest()


@pytest.mark.parametrize("case", OWNER["numbers"])
def test_js_number_spellings(case):
    # Values extracted from Bun are binary64 even when JSON has integer spelling.
    assert _number(float(case["input"])) == case["canonical"]


def test_integral_float_negative_zero_and_nested_settings_order_coalesce():
    first = inputs()
    second = inputs()
    first.update(rate=1, voice_settings={"a": [0, {"b": 1, "a": True}]})
    second.update(rate=1.0, voice_settings={"a": [-0.0, {"a": True, "b": 1.0}]})
    assert clip_identity(**first) == clip_identity(**second)
    assert _encode({"a": True}) != _encode({"a": 1})


def test_same_rendered_profiles_and_nfc_share_clip_not_plan_metadata():
    first = {"profile_name": "natural", "request": inputs()}
    second = {"profile_name": "custom-identical", "request": inputs()}
    second["request"]["text"] = unicodedata.normalize("NFD", second["request"]["text"])
    assert clip_identity(**first["request"]) == clip_identity(**second["request"])
    assert clip_identity(**second["request"]).text == "Chào bạn"


@pytest.mark.parametrize(
    "field,value",
    [
        ("provider", "google"),
        ("model", "other"),
        ("voice", "other"),
        ("rate", 0.9),
        ("text", "Chào, bạn"),
        ("text", "chào bạn"),
        ("text", "Chào bạn "),
        ("response_format", "wav"),
        ("voice_settings", {"stability": 0.7}),
        ("input_mode", "ssml"),
    ],
)
def test_every_audible_input_distinguishes_keys(field, value):
    base = inputs()
    assert clip_identity(**base).cache_key != clip_identity(**{**base, field: value}).cache_key


def test_new_keys_never_alias_old_v1_or_existing_rendition_keys():
    base = inputs()
    key = clip_identity(**base).cache_key
    assert key != legacy_phrase_key(
        provider=base["provider"],
        voice=base["voice"],
        rate=base["rate"],
        text=base["text"],
        format=base["response_format"],
    )
    old = {k: v for k, v in base.items() if k not in ("rate", "voice_settings")}
    assert key != rendition_identity(**old)[1]


@pytest.mark.parametrize("value", [float("nan"), float("inf"), -float("inf")])
def test_nonfinite_settings_cannot_collide_with_null(value):
    with pytest.raises(ValueError):
        clip_identity(**{**inputs(), "voice_settings": {"nested": [value]}})


@pytest.mark.parametrize("rate", [0, -1, float("nan"), float("inf"), True, "1"])
def test_invalid_resolved_rate_rejected_for_caller_degradation(rate):
    with pytest.raises(ValueError):
        clip_identity(**{**inputs(), "rate": rate})


def test_input_mode_and_non_json_settings_rejected():
    with pytest.raises(ValueError):
        clip_identity(**{**inputs(), "input_mode": "markdown"})
    with pytest.raises(ValueError):
        clip_identity(**{**inputs(), "voice_settings": {1: "x"}})
    with pytest.raises(ValueError):
        clip_identity(**{**inputs(), "voice_settings": {"x": object()}})
    with pytest.raises(ValueError):
        clip_identity(**{**inputs(), "voice_settings": {"x": 9007199254740993}})


def test_unicode_json_string_and_utf16_key_order():
    assert _encode({"\ue000": 1, "😀": 2}) == '{"😀":2,"\ue000":1}'
    assert _encode("\ud800") == '"\\ud800"'
    assert _encode('line\n"\\\u2028') == '"line\\n\\"\\\\\u2028"'


def test_return_value_is_snapshot_not_mutable_settings_reference():
    base = inputs()
    before = clip_identity(**base)
    base["voice_settings"]["stability"] = 0.123
    assert before.cache_key == OWNER["cases"][0]["sha256"]
    assert before != clip_identity(**base)
