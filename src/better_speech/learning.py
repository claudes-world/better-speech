"""Atomic learner curriculum edits. Adding/landing a phrase creates no exposure."""

from __future__ import annotations

import hashlib
import json
import os
import tempfile
from datetime import datetime, time, timedelta, timezone
from zoneinfo import ZoneInfo

from filelock import FileLock

from .config import resolve_profile
from .curriculum import canonical_phrase, learner_path, runtime_curriculum, validate_inventory
from .exposure import timestamp
from .pacing import DEFAULT, compile_lesson
from .types import Profile


def _update(profile, edit):
    path = learner_path(profile)
    path.parent.mkdir(parents=True, exist_ok=True)
    with FileLock(str(path) + ".lock", timeout=5):
        value = json.loads(path.read_text(encoding="utf-8")) if path.exists() else {"focusPhrases": []}
        validate_inventory(value["focusPhrases"])
        result = edit(value["focusPhrases"])
        temporary = None
        try:
            with tempfile.NamedTemporaryFile(
                mode="w", encoding="utf-8", dir=path.parent, delete=False
            ) as stream:
                temporary = stream.name
                json.dump(value, stream, ensure_ascii=False, indent=2, allow_nan=False)
                stream.write("\n")
                stream.flush()
                os.fsync(stream.fileno())
            os.replace(temporary, path)
        finally:
            if temporary:
                from pathlib import Path

                Path(temporary).unlink(missing_ok=True)
        return result


def learn(vietnamese: str, gloss: str, profile: Profile = Profile(), *, now: datetime | None = None) -> dict:
    profile = resolve_profile(profile)
    if "invalid_profile" in profile.config_diagnostics:
        raise ValueError("invalid_profile: learner writes disabled")
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
    existing = next((r for r in runtime_curriculum(profile) if canonical_phrase(r["l2"]) == phrase), None)
    key = existing["id"] if existing else "learn-" + hashlib.sha256(phrase.encode()).hexdigest()[:16]

    def edit(rows):
        row = next((r for r in rows if canonical_phrase(r["l2"]) == phrase), None)
        if row is None:
            row = (
                dict(existing)
                if existing
                else {
                    "id": key,
                    "l2": phrase,
                    "units": [phrase],
                    "lesson_type": "learning_sentence",
                    "vocabulary": [],
                    "structure_pauses": [],
                }
            )
            rows.append(row)
        row.update(
            l1=gloss.strip(),
            translation=gloss.strip(),
            pending_learning=True,
            landed=False,
            boost_until=timestamp(midnight.isoformat()),
            added_at=timestamp(instant.isoformat()),
        )
        return dict(row)

    return _update(profile, edit)


def learned(key: str, profile: Profile = Profile()) -> dict:
    profile = resolve_profile(profile)
    if "invalid_profile" in profile.config_diagnostics:
        raise ValueError("invalid_profile: learner writes disabled")
    existing = next(
        (r for r in runtime_curriculum(profile) if r["id"] == key or r["ledger_identity"] == key), None
    )
    if existing is None:
        raise ValueError("unknown phrase key")

    def edit(rows):
        row = next((r for r in rows if r["id"] == existing["id"]), None)
        if row is None:
            row = dict(existing)
            rows.append(row)
        row.update(landed=True, pending_learning=False, boost_until=None)
        return dict(row)

    return _update(profile, edit)


def due(profile: Profile = Profile(), *, ledger=None, now: datetime | None = None) -> list[dict]:
    from .compiler import _rank
    from .ledger import Ledger

    at = timestamp((now or datetime.now(timezone.utc)).isoformat())
    records = (ledger or Ledger.for_profile(profile)).snapshot(profile.learner_id)
    rows = runtime_curriculum(profile)
    return [
        {**r, "exposure": records.get(r["ledger_identity"])}
        for r in _rank(rows, records, at)
        if ((timestamp(r.get("boost_until")) or "") > at and not r.get("landed"))
        or records.get(r["ledger_identity"], {}).get("dueAt", "9999") <= at
    ]
