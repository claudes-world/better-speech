"""Historical curriculum import fixtures; only current curriculum ships in the wheel."""

import json
from collections.abc import Mapping, Sequence
from copy import deepcopy
from importlib.resources import files

from better_speech.curriculum import canonical_phrase, grouped_literal, validate_inventory


def bundled_curriculum() -> dict:
    return json.loads(
        files(__package__).joinpath("data/polyglot/curriculum-v1.json").read_text(encoding="utf-8")
    )


def aligned_gloss_provenance() -> dict:
    return json.loads(
        files(__package__)
        .joinpath("data/polyglot/curriculum-pr1010-provenance.json")
        .read_text(encoding="utf-8")
    )


def load_curriculum(custom: object = None) -> tuple[dict, ...]:
    """Snapshot custom inventory, falling back on invalid/empty custom data.

    Parsing/file custody belongs to the caller. Unlike V1's unchecked cast,
    malformed entries cannot become model-selectable Vietnamese authority.
    """
    if isinstance(custom, str):
        try:
            custom = json.loads(custom)
        except (ValueError, RecursionError):
            custom = None
    candidates = custom.get("focusPhrases") if isinstance(custom, dict) else None
    if isinstance(candidates, list) and candidates:
        try:
            validate_inventory(candidates)
            return tuple(deepcopy(candidates))
        except (ValueError, TypeError, RecursionError):
            pass
    return tuple(bundled_curriculum()["focusPhrases"])


def load_curriculum_with_aligned_glosses(custom: object = None) -> tuple[dict, ...]:
    """Opt-in, data-only PR1010 supplement; legacy loader stays byte-faithful.

    Match stable ID and exact NFC Vietnamese/common translation before adding
    approved pairs. Preserve custom explicit pairs and every other base field.
    This does NOT classify lessons or activate the legacy child-register set.
    Missing/malformed optional supplement leaves the base inventory intact.
    """
    base = load_curriculum(custom)
    try:
        supplement = json.loads(
            files(__package__).joinpath("data/polyglot/curriculum-pr1010.json").read_text(encoding="utf-8")
        )
        entries = supplement["focusPhrases"]
        validate_inventory(entries)
        by_id = {entry["id"]: entry for entry in entries}
    except (OSError, ValueError, TypeError, KeyError, RecursionError):
        return base
    output = []
    for original in base:
        entry = deepcopy(original)
        accepted = by_id.get(entry["id"])
        if (
            accepted
            and "l1_literal" in accepted
            and "l1_literal" not in entry
            and all(canonical_phrase(entry[key]) == canonical_phrase(accepted[key]) for key in ("l2", "l1"))
        ):
            proposed = {**entry, "l1_literal": deepcopy(accepted["l1_literal"])}
            try:
                grouped_literal(proposed)
                entry = proposed
            except (ValueError, TypeError):
                pass  # custom units cannot be guessed across aligned pairs
        output.append(entry)
    return tuple(output)


def concept_groups(entries: Sequence[Mapping], assignments: Mapping[str, str]) -> dict[str, tuple[str, ...]]:
    """Explicit curriculum ID groups only; never merge SRS identity by gloss."""
    ids = {entry["id"] for entry in entries}
    if set(assignments) - ids or any(not isinstance(v, str) or not v for v in assignments.values()):
        raise ValueError("invalid curriculum concept assignment")
    groups: dict[str, list[str]] = {}
    for entry in entries:
        if entry["id"] in assignments:
            groups.setdefault(assignments[entry["id"]], []).append(entry["id"])
    return {key: tuple(value) for key, value in groups.items()}
