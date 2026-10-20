import json
from dataclasses import replace

import pytest

from better_speech.exposure import record_exposures
from tests.reference.model_calls import ROUTES
from tests.reference.polyglot import TrustedSlot, prepare, structural_slots
from tests.reference.stages import StageContext, StageInput

TEXT = "Keep the original negation: do not deploy.\n\nURL https://example.test/a and `code` stay unchanged."
SOURCE = StageInput(TEXT, "revision-1", "settings-1", "inventory-1", "heteronyms-1", "spans-1")
INVENTORY = [
    {"id": "ack", "lesson_type": "connector", "phrase": "Hiểu rồi", "translation": "Understood"},
    {"id": "bye", "lesson_type": "connector", "phrase": "Hẹn gặp lại", "translation": "See you later"},
]
SLOTS = (TrustedSlot("start", "acknowledgement", 0, "start"), TrustedSlot("end", "closer", len(TEXT), "end"))
GOOD = '{"placements":[{"phraseId":"ack","slotId":"start"},{"phraseId":"bye","slotId":"end"}]}'


def run(call=lambda *args: GOOD, **overrides):
    arguments = dict(
        settings={"enabled": True},
        inventory=INVENTORY,
        exposure={},
        lane_id="test-lane",
        slots=SLOTS,
        eligible_ids={"start": ["ack"], "end": ["bye"]},
        selection_time="2026-09-25T12:00:00Z",
        seed=17,
        call=call,
    )
    source = overrides.pop("source", SOURCE)
    context = overrides.pop("context", StageContext(180, lambda: 0))
    arguments.update(overrides)
    return prepare(source, context, **arguments)


def assert_source(result, source=TEXT):
    assert result.plan.source == source
    assert "".join(source[s.start : s.end] for s in result.plan.spans if s.kind == "source") == source


def test_ids_only_source_preserving_plan_and_scoped_payload():
    calls = []

    def call(route, payload, timeout):
        calls.append((route, payload, timeout))
        assert set(payload) == {"task", "schema_version", "instruction", "source", "inventory", "slots"}
        assert payload["inventory"] == INVENTORY
        assert "exposure" not in payload and "settings" not in payload and "seed" not in payload
        return GOOD

    result = run(call)
    assert result.status == "applied"
    assert [(p.phrase_id, p.slot_id, p.offset) for p in result.plan.placements] == [
        ("ack", "start", 0),
        ("bye", "end", len(TEXT)),
    ]
    assert calls[0][0] == ROUTES[0] and calls[0][2] == 90
    assert_source(result)


def test_valid_empty_selection_is_success_not_retry():
    calls = []

    def call(*args):
        calls.append(args)
        return '{"placements":[]}'

    result = run(call)
    assert result.status == "applied" and result.reason == "no_lesson_selected"
    assert not result.plan.placements and len(calls) == 1
    assert_source(result)


@pytest.mark.parametrize(
    "raw",
    [
        '[{"phraseId":"ack","slotId":"start"},{"phraseId":"bye","slotId":"end"}]',
        '{"phraseId":"ack","slotId":"start"}\n{"phraseId":"bye","slotId":"end"}',
        '{"items":[{"phraseId":"ack","slotId":"start"},{"phraseId":"bye","slotId":"end"}]}',
        '{"results":[{"phraseId":"ack","slotId":"start"},{"phraseId":"bye","slotId":"end"}]}',
        '```json\n[{"phraseId":"ack","slotId":"start"},{"phraseId":"bye","slotId":"end"}]\n```',
    ],
)
def test_shared_helper_response_shapes_normalize_then_validate(raw):
    result = run(lambda *args: raw)
    assert result.status == "applied" and len(result.plan.placements) == 2
    assert len(result.plan.attempts) == 1
    assert_source(result)


@pytest.mark.parametrize(
    "raw",
    [
        "[" * 16000 + "0" + "]" * 16000,
        '{"items":[{"phraseId":"ack","slotId":"start","rest":"invented"}]}',
        '{"items":[],"rest":"invented"}',
        "[NaN]",
    ],
)
def test_normalization_never_accepts_extra_prose_or_crashes_on_deep_json(raw):
    result = run(lambda *args: raw)
    assert result.status == "skipped" and not result.plan.placements
    assert_source(result)


@pytest.mark.parametrize(
    "override,reason",
    [
        ({"settings": {"enabled": False}}, "disabled"),
        ({"settings": {}}, "disabled"),
        ({"eligible_ids": {}}, "no_eligible_slots"),
        ({"inventory": [{"id": "legacy", "l2": "Chào", "l1": "Hello"}]}, "no_eligible_slots"),
        ({"context": StageContext(0, lambda: 1)}, "deadline_or_cancelled"),
        ({"context": StageContext(180, lambda: 0, lambda: True)}, "deadline_or_cancelled"),
        ({"settings": {"enabled": True, "rewriteBudgetMs": 0}}, "deadline_or_cancelled"),
    ],
)
def test_optional_short_circuits_never_invent_eligibility_or_call(override, reason):
    result = run(lambda *args: pytest.fail("must not call helper"), **override)
    assert result.status == "skipped" and result.reason == reason
    assert_source(result)


def test_shared_route_fallback_remaining_budget_and_diagnostic_scope():
    now, calls = [10.0], []

    def call(route, payload, timeout):
        calls.append((route, timeout))
        now[0] += 20
        if len(calls) == 1:
            raise RuntimeError("secret error text never copied")
        if len(calls) == 2:
            return '{"placements":[{"phraseId":"ack","slotId":"start","rest":"invented"}]}'
        return GOOD

    context = StageContext.from_enqueue(0, lambda: now[0], custody_deadline=80)
    result = run(call, context=context)
    assert calls == [(ROUTES[0], 70), (ROUTES[1], 50), (ROUTES[2], 30)]
    assert [a.outcome for a in result.plan.attempts] == ["unavailable", "invalid_plan", "complete"]
    assert "secret" not in repr(result) and result.status == "applied"
    assert_source(result)


def test_explicit_rewrite_budget_never_restarts_shared_deadline():
    now, calls = [20.0], []

    def call(route, payload, timeout):
        calls.append(timeout)
        now[0] += timeout
        return GOOD

    result = run(
        call, context=StageContext(180, lambda: now[0]), settings={"enabled": True, "rewriteBudgetMs": 5000}
    )
    assert calls == [5]
    assert result.status == "skipped" and not result.plan.placements
    assert result.plan.attempts[0].outcome == "expired"
    assert_source(result)


def test_per_attempt_ninety_seconds_and_late_response_discard():
    now, calls = [0.0], []

    def call(route, payload, timeout):
        calls.append(timeout)
        if len(calls) == 1:
            now[0] += 90
            return GOOD
        return '{"placements":[]}'

    result = run(call, context=StageContext(180, lambda: now[0]))
    assert calls == [90, 90]
    assert result.reason == "no_lesson_selected"
    assert [a.outcome for a in result.plan.attempts] == ["attempt_expired", "complete"]


def test_cancellation_during_response_discards_it():
    cancelled = [False]

    def call(*args):
        cancelled[0] = True
        return GOOD

    result = run(call, context=StageContext(180, lambda: 0, lambda: cancelled[0]))
    assert result.status == "skipped" and not result.plan.placements
    assert len(result.plan.attempts) == 1
    assert_source(result)


def test_invalid_or_extra_prose_never_becomes_source():
    result = run(lambda *args: '{"placements":[],"rest":"Okay, okay. Deploy it."}')
    assert result.status == "skipped" and len(result.plan.attempts) == 3
    assert_source(result)


def test_unknown_ids_wrong_semantics_duplicates_drop_locally():
    result = run(
        lambda *args: json.dumps(
            {
                "placements": [
                    {"phraseId": "ack", "slotId": "start"},
                    {"phraseId": "ack", "slotId": "end"},
                    {"phraseId": "invented", "slotId": "end"},
                    {"phraseId": "bye", "slotId": "end"},
                ]
            }
        )
    )
    assert result.status == "partial" and len(result.plan.placements) == 2
    assert len(result.plan.attempts) == 1
    assert_source(result)


def test_model_cannot_introduce_mid_sentence_or_code_fence_slot():
    text = "first\n\n```\ncode\n\nmore code\n```"
    slots = (
        TrustedSlot("start", "opener", 1, "start"),
        TrustedSlot("end", "closer", text.index("more"), "end"),
    )
    result = run(
        lambda *args: pytest.fail("invalid trusted boundaries must not be offered"),
        source=replace(SOURCE, source=text),
        slots=slots,
    )
    assert result.reason == "no_eligible_slots"
    assert_source(result, text)


@pytest.mark.parametrize(
    "changed", ["seed", "selection_time", "settings", "exposure", "upstream", "eligible", "inventory"]
)
def test_cache_identity_contains_all_pinned_context(changed):
    first = run()
    options = {
        "seed": {"seed": 18},
        "selection_time": {"selection_time": "2026-09-26T12:00:00Z"},
        "settings": {"settings": {"enabled": True, "intensity": 2}},
        "exposure": {"exposure": {"bad-record": {}}},
        "upstream": {"source": replace(SOURCE, upstream_plan_digest="different")},
        "eligible": {"eligible_ids": {"start": ["ack"]}},
        "inventory": {"inventory": [{**INVENTORY[0], "translation": "Understood."}, INVENTORY[1]]},
    }
    second = run(**options[changed])
    assert first.cache_identity != second.cache_identity
    assert first.cache_identity == run().cache_identity


def test_callback_mutation_cannot_change_snapshot_or_retry_payload():
    count = [0]

    def call(route, payload, timeout):
        count[0] += 1
        assert payload["source"] == TEXT
        payload["source"] = "mutated"
        if count[0] == 1:
            return "invalid"
        return GOOD

    result = run(call)
    assert "mutated" not in result.plan.snapshot_json
    assert_source(result)


@pytest.mark.parametrize(
    "settings",
    [{"enabled": True, "rewriteBudgetMs": float("nan")}, [], {"enabled": True, "unused": object()}],
)
def test_invalid_optional_snapshot_keeps_complete_source(settings):
    result = run(lambda *args: pytest.fail("invalid snapshot must not call"), settings=settings)
    assert result.status == "skipped"
    assert_source(result)


def test_pinned_exposure_due_order_is_used_without_mutation():
    records, _ = record_exposures(
        {},
        [{"l2": INVENTORY[1]["phrase"], "l1": "See you later"}],
        now="2026-09-23T12:00:00Z",
        lane_id="test-lane",
    )
    before = json.dumps(records, sort_keys=True)
    result = run(exposure=records)
    assert result.plan.candidate_ids == ("ack", "bye")  # all offered; ranking happens after fit
    assert json.dumps(records, sort_keys=True) == before


@pytest.mark.parametrize("when,count", [("2026-09-24T03:59:59Z", 0), ("2026-09-24T04:00:00Z", 1)])
def test_coffee_deferral_uses_pinned_eastern_date(when, count):
    inventory = [
        {
            "id": "cho-minh-ca-phe",
            "lesson_type": "learning_sentence",
            "phrase": "Cho mình cà phê",
            "translation": "Coffee please",
        }
    ]
    result = run(
        lambda *args: '{"placements":[{"phraseId":"cho-minh-ca-phe","slotId":"end"}]}',
        inventory=inventory,
        slots=(TrustedSlot("end", "closing_lesson", len(TEXT), "end"),),
        eligible_ids={"end": ["cho-minh-ca-phe"]},
        selection_time=when,
    )
    assert len(result.plan.placements) == count
    assert_source(result)


def test_long_source_is_never_truncated_when_helper_fails():
    text = "Original source 😀.\n\n" * 1000
    result = run(
        lambda *args: "bad response",
        source=replace(SOURCE, source=text),
        slots=(TrustedSlot("start", "opener", 0, "start"),),
    )
    assert result.status == "skipped"
    assert_source(result, text)


def test_semantic_fit_precedes_unrelated_review_debt():
    unrelated = [
        {
            "id": f"other-{i}",
            "lesson_type": "connector",
            "phrase": f"Other {i}",
            "translation": f"Other meaning {i}",
        }
        for i in range(4)
    ]
    records, _ = record_exposures(
        {}, [{"l2": row["phrase"]} for row in unrelated], now="2026-09-20T12:00:00Z", lane_id="test-lane"
    )
    result = run(inventory=unrelated + INVENTORY, exposure=records)
    assert result.plan.candidate_ids == ("ack", "bye")
    assert [p.phrase_id for p in result.plan.placements] == ["ack", "bye"]


@pytest.mark.parametrize("intensity,expected", [(1, 2), (2, 4), (2.0, 4), (3.0, 6), (True, 2)])
def test_integral_numeric_intensity_matches_resolved_settings(intensity, expected):
    inventory = [
        {"id": f"id-{i}", "lesson_type": "connector", "phrase": f"Phrase {i}", "translation": f"Meaning {i}"}
        for i in range(6)
    ]
    records, _ = record_exposures(
        {}, [{"l2": row["phrase"]} for row in inventory], now="2026-09-20T12:00:00Z", lane_id="test-lane"
    )
    text = "First.\n\nSecond.\n\nThird."
    slots = structural_slots(text)

    def call(route, payload, timeout):
        return json.dumps(
            {"placements": [{"phraseId": r["id"], "slotId": s.id} for r, s in zip(inventory, slots)]}
        )

    result = run(
        call,
        source=replace(SOURCE, source=text),
        slots=None,
        eligible_ids=None,
        inventory=inventory,
        exposure=records,
        settings={"enabled": True, "intensity": intensity},
    )
    assert len(result.plan.candidate_ids) == 6
    assert len(result.plan.placements) == min(expected, 3)  # independent connector cap


def test_runtime_derives_slots_without_external_semantic_prerequisite():
    calls = []

    def call(route, payload, timeout):
        calls.append(payload)
        assert all(set(s) == {"id", "kind", "offset", "eligible_ids"} for s in payload["slots"])
        return json.dumps({"placements": [{"phraseId": "ack", "slotId": "opener:0"}]})

    result = run(call, slots=None, eligible_ids=None)
    assert len(calls) == 1 and result.plan.placements[0].offset == 0
    assert_source(result)


@pytest.mark.parametrize("marker", ["```", "~~~~", "````"])
@pytest.mark.parametrize("closed", [False, True])
def test_derived_slots_exclude_code_and_preserve_paragraph_boundaries(marker, closed):
    text = f"First.\n\n{marker}python\ninside\n\ncode\n" + (f"{marker}\n\nLast." if closed else "")
    slots = structural_slots(text)
    code_start = text.index(marker)
    code_end = text.rindex(marker) + len(marker) if closed else len(text) + 1
    assert all(not code_start <= s.offset < code_end for s in slots)
    assert any(s.offset == 0 for s in slots)
    assert all(
        s.offset in (0, len(text))
        or text[s.offset : s.offset + 2] == "\n\n"
        or text[max(0, s.offset - 2) : s.offset] == "\n\n"
        for s in slots
    )
    assert any(s.kind == "closing_lesson" for s in slots) == closed


def test_semantic_helper_can_pick_seen_not_due_despite_unrelated_review_debt():
    inventory = [
        {
            "id": f"unrelated-{i}",
            "lesson_type": "connector",
            "phrase": f"Unrelated {i}",
            "translation": "Other",
        }
        for i in range(6)
    ] + INVENTORY[:1]
    records, _ = record_exposures(
        {}, [{"l2": r["phrase"]} for r in inventory[:-1]], now="2026-09-20T12:00:00Z", lane_id="test-lane"
    )
    records, _ = record_exposures(
        records, [{"l2": INVENTORY[0]["phrase"]}], now="2026-09-25T11:59:59Z", lane_id="test-lane"
    )

    def call(route, payload, timeout):
        assert len(payload["inventory"]) == 7  # no debt shortlist before meaning
        return '{"placements":[{"phraseId":"ack","slotId":"opener:0"}]}'

    result = run(call, slots=None, eligible_ids=None, inventory=inventory, exposure=records)
    assert [p.phrase_id for p in result.plan.placements] == ["ack"]
    assert_source(result)


def test_new_limit_applies_only_after_helper_fit():
    inventory = INVENTORY + [
        {"id": "third", "lesson_type": "connector", "phrase": "Third", "translation": "Third"}
    ]

    def call(route, payload, timeout):
        assert len(payload["inventory"]) == 3
        return json.dumps(
            {
                "placements": [
                    {"phraseId": r["id"], "slotId": s["id"]} for r, s in zip(inventory, payload["slots"])
                ]
            }
        )

    result = run(
        call, slots=None, eligible_ids=None, inventory=inventory, settings={"enabled": True, "intensity": 3}
    )
    assert len(result.plan.placements) == 2
    assert_source(result)


def test_due_preference_applies_among_fitting_proposals_not_response_order():
    inventory = INVENTORY + [
        {"id": "due", "lesson_type": "connector", "phrase": "Due phrase", "translation": "Due"}
    ]
    records, _ = record_exposures({}, [{"l2": "Due phrase"}], now="2026-09-20T12:00:00Z", lane_id="test-lane")

    def call(route, payload, timeout):
        return json.dumps(
            {
                "placements": [
                    {"phraseId": r["id"], "slotId": s["id"]} for r, s in zip(inventory, payload["slots"])
                ]
            }
        )

    result = run(call, slots=None, eligible_ids=None, inventory=inventory, exposure=records)
    assert {p.phrase_id for p in result.plan.placements} == {"ack", "due"}
    assert_source(result)
