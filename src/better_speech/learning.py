"""Atomic learner edits in the delivered-event ledger; edits create no exposure."""

from __future__ import annotations

import hashlib
from datetime import datetime, time, timedelta, timezone
from zoneinfo import ZoneInfo

from .config import resolve_profile
from .curriculum import canonical_phrase, runtime_curriculum
from .exposure import anchor_floor, timestamp
from .pacing import DEFAULT, compile_lesson
from .types import Profile


def _writable(profile):
    profile = resolve_profile(profile)
    if {"invalid_profile", "profile_unavailable"}.intersection(profile.config_diagnostics):
        raise ValueError("invalid_profile: learner writes disabled")
    return profile


def learn(vietnamese: str, gloss: str, profile: Profile = Profile(), *, ledger, now: datetime | None = None, hint: str | None = None) -> dict:
    profile = _writable(profile)
    if not vietnamese.strip() or not gloss.strip():
        raise ValueError("phrase and gloss cannot be blank")
    candidate = {"phrase": vietnamese.strip(), "units": [vietnamese.strip()]}
    paced = compile_lesson(candidate, profile=DEFAULT)
    if any(len(value.encode("utf-16-le")) // 2 > 4000 for value in (paced.rendered, gloss.strip())):
        raise ValueError("lesson step exceeds provider text bound")
    instant = now or datetime.now(timezone.utc)
    if instant.tzinfo is None:
        raise ValueError("learning time must be timezone-aware")
    local = instant.astimezone(ZoneInfo(profile.timezone))
    midnight = datetime.combine(local.date() + timedelta(days=1), time(), ZoneInfo(profile.timezone))
    phrase = canonical_phrase(vietnamese.strip())

    def edit(rows):
        existing = next((r for r in runtime_curriculum(profile, additions=rows)
                         if canonical_phrase(r["l2"]) == phrase), None)
        row = next((r for r in rows if canonical_phrase(r["l2"]) == phrase), None)
        if row is None:
            row = dict(existing) if existing else {
                "id": "learn-" + hashlib.sha256(phrase.encode()).hexdigest()[:16],
                "l2": phrase, "units": [phrase], "lesson_type": "learning_sentence",
                "vocabulary": [], "structure_pauses": [],
            }
            rows.append(row)
        row.update(
            l1=gloss.strip(), translation=gloss.strip(), pending_learning=True, landed=False,
            boost_until=timestamp(midnight.isoformat()), added_at=timestamp(instant.isoformat()),
        )
        if hint is not None:
            row["hint"] = hint.strip()
        # Validate the actual merged record, including existing unit/pacing metadata.
        paced = compile_lesson({**row, "phrase": phrase}, profile=DEFAULT)
        if len(paced.rendered.encode("utf-16-le")) // 2 > 4000:
            raise ValueError("lesson step exceeds provider text bound")
        return dict(row)

    return ledger.edit_phrases(profile.learner_id, edit)


def learned(key: str, profile: Profile = Profile(), *, ledger) -> dict:
    profile = _writable(profile)

    def edit(rows):
        existing = next((r for r in runtime_curriculum(profile, additions=rows)
                         if r["id"] == key or r["ledger_identity"] == key), None)
        if existing is None:
            raise ValueError("unknown phrase key")
        row = next((r for r in rows if r["id"] == existing["id"]), None)
        if row is None:
            row = dict(existing)
            rows.append(row)
        row.update(landed=True, pending_learning=False, boost_until=None)
        return dict(row)

    return ledger.edit_phrases(profile.learner_id, edit)


def due(profile: Profile = Profile(), *, ledger, now: datetime | None = None) -> list[dict]:
    from .compiler import _rank

    at = timestamp((now or datetime.now(timezone.utc)).isoformat())
    records, additions = ledger.learner_state(profile.learner_id)
    rows = runtime_curriculum(profile, additions=additions)
    records = anchor_floor(rows, records, profile.anchor_floor_days)
    return [
        {**r, "exposure": records.get(r["ledger_identity"])}
        for r in _rank(rows, records, at)
        if ((timestamp(r.get("boost_until")) or "") > at and not r.get("landed"))
        or records.get(r["ledger_identity"], {}).get("dueAt", "9999") <= at
    ]
