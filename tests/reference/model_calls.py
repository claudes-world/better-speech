"""Shared subscription-helper response validation and bounded fallback.

This module never invokes a model directly. The authenticated local IPC adapter
must enforce the supplied timeout/cancellation and return only bounded text.
There is intentionally no API-key or paid-model adapter fallback.
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from typing import Callable

from .stages import StageContext

ROUTES = ("agy:flash", "claude:haiku-4.5", "codex:gpt-5.6-luna")


def response_items(raw: str) -> list[dict]:
    """Normalize JSON envelopes, bare arrays and NDJSON; tolerate bad items.

    CLI adapters extract their textual response first. These envelopes are
    stage response shapes, not permission to recursively interpret tool output.
    """
    text = raw.strip()
    if text.startswith("```") and text.endswith("```"):
        lines = text.splitlines()
        text = "\n".join(lines[1:-1])
    try:
        value = json.loads(text)
    except (ValueError, TypeError):
        values = []
        for line in text.splitlines():
            try:
                values.append(json.loads(line))
            except ValueError:
                continue
        value = values
    if isinstance(value, dict):
        if "id" in value:
            value = [value]
        else:
            value = value.get("items", value.get("results", []))
    if not isinstance(value, list):
        return []
    return [item for item in value if isinstance(item, dict)]


def validated_choices(
    raw: str, allowed: dict[str, frozenset[str]], *, field: str = "sense_id"
) -> dict[str, str]:
    """Conflicting duplicates invalidate that ID, including invalid choices."""
    answers: dict[str, str] = {}
    seen: dict[str, object] = {}
    conflicts: set[str] = set()
    for item in response_items(raw):
        identifier, choice = item.get("id"), item.get(field)
        if not isinstance(identifier, str) or identifier not in allowed:
            continue
        if identifier in seen and seen[identifier] != choice:
            conflicts.add(identifier)
        seen[identifier] = choice
        if isinstance(choice, str) and choice in allowed[identifier]:
            answers[identifier] = choice
    return {key: value for key, value in answers.items() if key not in conflicts}


@dataclass(frozen=True)
class ModelAttempt:
    route: str
    ids: tuple[str, ...]
    timeout: float
    outcome: str


@dataclass(frozen=True)
class ChoiceResult:
    choices: dict[str, str]
    unresolved: tuple[str, ...]
    attempts: tuple[ModelAttempt, ...]


def resolve_choices(
    allowed: dict[str, frozenset[str]],
    context: StageContext,
    *,
    build_payload: Callable[[tuple[str, ...]], dict],
    call: Callable[[str, dict, float], str],
    field: str = "sense_id",
) -> ChoiceResult:
    """Keep valid per-item selections and retry only unresolved candidates.

    An expired or cancelled response is discarded even when the transport
    returned successfully. Durable publication must also fence the owning job.
    Exception text/model output is not copied into diagnostics: it can contain
    source text, helper paths or credentials.
    """
    choices: dict[str, str] = {}
    attempts: list[ModelAttempt] = []
    for route in ROUTES:
        pending = tuple(key for key in allowed if key not in choices)
        timeout = min(90, context.remaining())
        if not pending or timeout <= 0:
            break
        started = context.clock()
        try:
            raw = call(route, build_payload(pending), timeout)
            if context.remaining() <= 0:
                attempts.append(ModelAttempt(route, pending, timeout, "expired"))
                break
            if context.clock() - started >= timeout:
                attempts.append(ModelAttempt(route, pending, timeout, "attempt_expired"))
                continue
            selected = validated_choices(raw, {key: allowed[key] for key in pending}, field=field)
            choices.update(selected)
            outcome = "complete" if len(selected) == len(pending) else "partial"
        except Exception:  # Optional enrichment must not prevent source delivery.
            outcome = "unavailable"
        attempts.append(ModelAttempt(route, pending, timeout, outcome))
    return ChoiceResult(choices, tuple(key for key in allowed if key not in choices), tuple(attempts))
