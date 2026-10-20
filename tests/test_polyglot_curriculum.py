"""Original V1 fixtures stay separate from explicitly labelled owner deltas."""

import hashlib
import json
import unicodedata
from pathlib import Path

import pytest

from better_speech.curriculum import (
    canonical_phrase,
    grouped_literal,
    identity_aliases,
    js_collapse_whitespace,
    js_trim,
    phrase_identity,
    strip_tiers,
    validate_inventory,
)
from tests.reference.curriculum import (
    aligned_gloss_provenance,
    bundled_curriculum,
    concept_groups,
    load_curriculum,
    load_curriculum_with_aligned_glosses,
)

FIXTURES = Path(__file__).parent / "fixtures/enrichment"


def test_original_inventory_golden_and_roundtrip():
    golden = json.loads((FIXTURES / "curriculum.json").read_text())
    import tests.reference.curriculum as module

    raw = Path(module.__file__).parent / "data/polyglot/curriculum-v1.json"
    assert hashlib.sha256(raw.read_bytes()).hexdigest() == golden["asset_sha256"]
    inventory = load_curriculum()
    assert len(inventory) == golden["inventory_count"] == len({p["id"] for p in inventory})
    assert next(p for p in inventory if p["id"] == golden["phrase_id"])["l2"] == golden["phrase"]
    assert list(inventory) == bundled_curriculum()["focusPhrases"]
    validate_inventory(inventory)
    assert load_curriculum({"focusPhrases": list(inventory)}) == inventory
    assert [p["id"] for p in load_curriculum(golden["override"])] == golden["expected_override_ids"]
    assert all(grouped_literal(p) is None for p in inventory)  # source asset gap, not fabricated


@pytest.mark.parametrize("raw", [None, "bad json", [], {}, {"focusPhrases": []}, {"focusPhrases": [None]}])
def test_custom_invalid_falls_back(raw):
    assert load_curriculum(raw) == load_curriculum()


def test_inventory_copies_inputs_and_rejects_annotation_drift():
    raw = {"focusPhrases": [{"id": "x", "l2": "Dạ.", "l1": "Yes."}]}
    loaded = load_curriculum(raw)
    loaded[0]["l1"] = "changed"
    assert raw["focusPhrases"][0]["l1"] == "Yes."
    with pytest.raises(ValueError):
        validate_inventory([{**raw["focusPhrases"][0], "l2Tiered": "wrong"}])
    with pytest.raises(ValueError):
        validate_inventory(raw["focusPhrases"] * 2)


def test_original_tier_strip_invariant():
    annotated = 0
    for entry in load_curriculum():
        if "l2Tiered" not in entry:
            continue
        annotated += 1
        plain = entry.get("units", entry["l2"])
        tiered = entry["l2Tiered"]
        if isinstance(plain, str):
            plain, tiered = [plain], [tiered]
        assert [canonical_phrase(strip_tiers(t)) for t in tiered] == [canonical_phrase(p) for p in plain]
    assert annotated > 30
    assert strip_tiers("ba⟨1 mảnh1⟩!") == "ba mảnh !"


def test_owner_explicit_alias_does_not_casefold_or_drop_punctuation():
    original = "Cho mình một cà phê sữa đá nha"
    display = "Cho mình, một cà phê sữa đá, nha."
    assert phrase_identity(display) != original
    assert phrase_identity(display, {display: original}) == original
    assert phrase_identity(unicodedata.normalize("NFD", display), {display: original}) == original
    assert canonical_phrase("Chào") != canonical_phrase("chào")
    with pytest.raises(ValueError):
        identity_aliases({"a": "b", "b": "a"})
    with pytest.raises(ValueError):
        identity_aliases({"a": "b", "b": "c"})


def test_owner_concepts_are_explicit_not_gloss_based():
    entries = load_curriculum()
    assert concept_groups(entries, {"di-thoi": "motion", "dung-o-day": "motion"}) == {
        "motion": ("di-thoi", "dung-o-day")
    }
    assert concept_groups(entries, {}) == {}
    with pytest.raises(ValueError):
        concept_groups(entries, {"model-invented": "motion"})


def test_owner_aligned_pairs_cover_coffee_particles_and_units():
    entry = {
        "phrase": "Cho mình, một cà phê sữa đá, nha.",
        "units": ["Cho mình", "một", "cà phê sữa đá", "nha"],
        "l1_literal": [
            ["Cho", "give"],
            ["mình", "me"],
            ["một", "one"],
            ["cà phê", "coffee"],
            ["sữa", "milk"],
            ["đá", "ice"],
            ["nha", "[softener]"],
        ],
    }
    assert grouped_literal(entry) == ("give — me", "one", "coffee — milk — ice", "[softener]")
    for pairs in (entry["l1_literal"][:-1], entry["l1_literal"][1:], entry["l1_literal"] * 2):
        with pytest.raises(ValueError):
            grouped_literal({**entry, "l1_literal": pairs})


def test_source_derived_ecmascript_whitespace_not_python_whitespace():
    assert js_trim("\ufeff a \ufeff") == "a"
    assert js_trim("\u0085") == "\u0085"
    assert js_trim("\u001c") == "\u001c"
    assert js_collapse_whitespace("\ufeffa\u2003\t b\ufeff") == "a b"
    assert strip_tiers("\ufeffa⟨1⟩\u0085b\ufeff") == "a \u0085b"
    validate_inventory([{"id": "x", "l2": "\u0085", "l1": "x"}])
    with pytest.raises(ValueError):
        validate_inventory([{"id": "x", "l2": "\ufeff", "l1": "x"}])


def test_recovered_pr1010_asset_hash_and_all_entries_roundtrip():
    import tests.reference.curriculum as module

    golden = json.loads((FIXTURES / "curriculum-aligned.json").read_text())
    raw = Path(module.__file__).parent / "data/polyglot/curriculum-pr1010.json"
    assert hashlib.sha256(raw.read_bytes()).hexdigest() == golden["asset_sha256"]
    original = json.loads(raw.read_text())["focusPhrases"]
    loaded = load_curriculum_with_aligned_glosses()
    assert list(loaded) == original
    assert len(loaded) == golden["inventory_count"] == 54
    assert aligned_gloss_provenance()["source_sha256"] == golden["asset_sha256"]
    assert load_curriculum_with_aligned_glosses({"focusPhrases": list(loaded)}) == loaded
    for base, supplemented in zip(load_curriculum(), loaded):
        assert {k: v for k, v in supplemented.items() if k != "l1_literal"} == base
        assert grouped_literal(supplemented) is not None
        assert "lesson_type" not in supplemented
    coffee = next(p for p in loaded if p["id"] == golden["coffee_id"])
    assert coffee["l1_literal"] == golden["coffee_pairs"]
    assert list(grouped_literal(coffee)) == golden["coffee_unit_glosses"]
    assert (
        next(p for p in loaded if p["id"] == golden["hometown_time_example_id"])["l1_literal"]
        == golden["hometown_time_example_pairs"]
    )


def test_pr1010_original_particle_and_coverage_expectations():
    seen, bracketed = {}, 0
    for entry in load_curriculum_with_aligned_glosses():
        pairs = entry["l1_literal"]
        assert pairs
        for vi, en in pairs:
            assert js_trim(vi) and js_trim(en)
            assert all(marker not in vi + en for marker in ("⟨", "⟩"))
            if en.startswith("["):
                assert en.endswith("]")
                bracketed += 1
                key = canonical_phrase(vi).lower()
                assert key not in seen or seen[key] == en
                seen[key] = en
            if canonical_phrase(vi).lower() in ("nha", "nhé"):
                assert en.startswith("[") and en.endswith("]")
        # grouped_literal proves original exact token coverage and additionally
        # enforces the owner unit grouping without dropping any particles.
        assert grouped_literal(entry)
    assert bracketed > 10


@pytest.mark.parametrize("change", [{"l2": "Dạ!"}, {"l1": "Changed meaning"}, {"id": "custom-da"}])
def test_supplement_never_guesses_custom_identity_or_translation(change):
    entry = {"id": "da", "l2": "Dạ.", "l1": "Yes (polite).", **change}
    assert load_curriculum_with_aligned_glosses({"focusPhrases": [entry]}) == (entry,)


def test_explicit_custom_pairs_win_and_returned_pairs_are_isolated():
    entry = {"id": "da", "l2": "Dạ.", "l1": "Yes (polite).", "l1_literal": [["Dạ", "yes politely"]]}
    loaded = load_curriculum_with_aligned_glosses({"focusPhrases": [entry]})
    assert loaded == (entry,)
    loaded[0]["l1_literal"][0][1] = "mutated"
    assert entry["l1_literal"][0][1] == "yes politely"
    assert load_curriculum_with_aligned_glosses()[0]["l1_literal"][0][1] != "mutated"


def test_deep_custom_json_falls_back_without_exception():
    raw = "[" * 16000 + "0" + "]" * 16000
    assert load_curriculum(raw) == load_curriculum()
    assert load_curriculum_with_aligned_glosses(raw) == load_curriculum_with_aligned_glosses()


def test_optional_supplement_missing_pairs_preserves_base(monkeypatch):
    import tests.reference.curriculum as module

    original = module.files
    base = load_curriculum()

    class Asset:
        def read_text(self, **kwargs):
            return json.dumps({"focusPhrases": list(base)})

    class Root:
        def joinpath(self, path):
            return (
                Asset()
                if path.endswith("curriculum-pr1010.json")
                else original(module.__package__).joinpath(path)
            )

    monkeypatch.setattr(module, "files", lambda *args: Root())
    assert load_curriculum_with_aligned_glosses() == base
