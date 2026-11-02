"""Unset profiles must keep producing byte-identical plans to better-speech v0.2.17."""

import json
from dataclasses import replace
from datetime import datetime, timezone

from better_speech import compile
from better_speech.types import digest
from tests.test_model_free_audio import EmptyLedger, profile

NOW = datetime(2026, 10, 21, 14, tzinfo=timezone.utc)
TEXTS = (
    "Source.",
    "The build passed.\n\nThe deploy is next.\n\nI will report back.",
    "# Status\n\nAll green.\n\n- one\n- two\n\n[Docs](https://example.org).",
)
# Pinned from better-speech v0.2.17 (5608d83); v0.2.18 (3a5fe97) reproduces both digests.
BASELINE_DEFAULT = "fb56c56ddeeb3b3e743cde854b7ededd0c5bdd52b647a13de5d06fb7a1734f6e"
BASELINE_INTENSE = "90d1779688ce6008ca5559e0e8355be6fff316a125675db1184130796088ace1"


def _placing_model(payload, *, timeout):
    slots, ids = payload["slots"], [row["id"] for row in payload["inventory"]]
    return json.dumps({"placements": [{"phraseId": i, "slotId": s} for i, s in zip(ids, slots)]})


def corpus_digest(cfg, ledger_factory=EmptyLedger):
    """Digest of complete serialized plans across texts, seeds, greetings and model placements."""
    plans = []
    for text in TEXTS:
        for seed in range(6):
            for greeting in (False, True):
                for model in (None, _placing_model):
                    plan = compile(text, cfg, ledger=ledger_factory(), now=NOW, seed=seed,
                                   greeting=greeting, model_call=model)
                    plans.append(plan.to_dict())
    return digest(plans)


def test_unset_profile_plans_are_byte_identical_to_v0217():
    assert corpus_digest(profile()) == BASELINE_DEFAULT
    assert corpus_digest(replace(profile(), intensity=2, closer_chance=1.0)) == BASELINE_INTENSE
