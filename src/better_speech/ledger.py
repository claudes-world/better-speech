"""Delivered-only SQLite events, reduced chronologically inside one write transaction."""

from __future__ import annotations

import json
import sqlite3
import time
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path

from .budget import remaining
from .config import state_dir
from .exposure import record_exposures, timestamp
from .types import Diagnostic, Profile, canonical_json

SCHEMA = """
CREATE TABLE IF NOT EXISTS events (
    learner_id TEXT NOT NULL,
    delivery_id TEXT NOT NULL,
    phrase_key TEXT NOT NULL,
    delivered_at TEXT NOT NULL,
    phrase_json TEXT NOT NULL,
    PRIMARY KEY (learner_id, delivery_id, phrase_key)
);
CREATE INDEX IF NOT EXISTS event_replay
ON events(learner_id, phrase_key, delivered_at, delivery_id);
CREATE TABLE IF NOT EXISTS phrases (
    learner_id TEXT NOT NULL,
    phrase_key TEXT NOT NULL,
    record_json TEXT NOT NULL,
    PRIMARY KEY (learner_id, phrase_key)
);
"""


@dataclass(frozen=True)
class DeliveryReceipt:
    added: int = 0
    diagnostics: tuple[Diagnostic, ...] = ()


class Ledger:
    def __init__(self, path: str | Path, *, busy_timeout_s: float = 5):
        self.path = Path(path)
        self.busy_timeout_s = busy_timeout_s

    @classmethod
    def for_profile(cls, profile: Profile):
        return cls(state_dir(profile) / "ledger.sqlite3")

    def _connect(self, deadline=None):
        timeout = (
            min(self.busy_timeout_s, remaining(deadline)) if deadline is not None else self.busy_timeout_s
        )
        self.path.parent.mkdir(parents=True, exist_ok=True)
        connection = sqlite3.connect(self.path, timeout=timeout, isolation_level=None)
        try:
            if deadline is not None:
                connection.set_progress_handler(lambda: int(time.monotonic() >= deadline), 1000)
            self._execute(connection, "PRAGMA journal_mode=WAL", deadline=deadline)
            for statement in SCHEMA.split(";"):
                if statement.strip():
                    self._execute(connection, statement, deadline=deadline)
            return connection
        except BaseException:
            connection.close()
            raise

    def _execute(self, connection, sql, parameters=(), *, deadline=None):
        if deadline is not None:
            timeout = min(self.busy_timeout_s, remaining(deadline))
            connection.execute(f"PRAGMA busy_timeout={max(1, int(timeout * 1000))}")
        cursor = connection.execute(sql, parameters)
        if deadline is not None:
            remaining(deadline)
        return cursor

    def snapshot(self, learner_id="liam", *, deadline=None):
        if not self.path.exists():
            return {}
        connection = self._connect(deadline)
        try:
            return {
                key: json.loads(value)
                for key, value in self._execute(
                    connection,
                    "SELECT phrase_key, record_json FROM phrases WHERE learner_id=?",
                    (learner_id,),
                    deadline=deadline,
                )
            }
        finally:
            connection.close()

    def record(self, plan, delivery_id, delivered_at, *, deadline=None):
        if not plan.executed:
            return DeliveryReceipt(diagnostics=(Diagnostic("ledger", "unexecuted_plan_ignored"),))
        if not plan.lessons:
            return DeliveryReceipt()
        if not isinstance(delivery_id, str) or not delivery_id:
            raise ValueError("delivery_id required")
        instant = timestamp(delivered_at.isoformat() if isinstance(delivered_at, datetime) else delivered_at)
        if instant is None:
            raise ValueError("invalid delivery time")
        connection = self._connect(deadline)
        added = 0
        try:
            self._execute(connection, "BEGIN IMMEDIATE", deadline=deadline)
            for lesson in plan.lessons:
                if deadline is not None:
                    remaining(deadline)
                phrase = {"l2": lesson.phrase_key, "l1": lesson.record["translation"]}
                changed = self._execute(
                    connection,
                    "INSERT OR IGNORE INTO events VALUES(?,?,?,?,?)",
                    (plan.learner_id, delivery_id, lesson.phrase_key, instant, canonical_json(phrase)),
                    deadline=deadline,
                ).rowcount
                if not changed:
                    continue
                added += 1
                records = {}
                # Late acknowledgements replay the complete chronological history.
                # Arrival order cannot advance or freeze the interval differently.
                for at, raw in self._execute(
                    connection,
                    "SELECT delivered_at, phrase_json FROM events WHERE learner_id=? AND phrase_key=? "
                    "ORDER BY delivered_at, delivery_id",
                    (plan.learner_id, lesson.phrase_key),
                    deadline=deadline,
                ):
                    if deadline is not None:
                        remaining(deadline)
                    records, _ = record_exposures(records, [json.loads(raw)], now=at)
                self._execute(
                    connection,
                    "INSERT INTO phrases VALUES(?,?,?) ON CONFLICT(learner_id,phrase_key) "
                    "DO UPDATE SET record_json=excluded.record_json",
                    (plan.learner_id, lesson.phrase_key, canonical_json(records[lesson.phrase_key])),
                    deadline=deadline,
                )
            if deadline is not None:
                remaining(deadline)
            connection.commit()
        except BaseException:
            connection.rollback()
            raise
        finally:
            connection.close()
        return DeliveryReceipt(added)


def record_delivered(
    plan,
    delivery_id: str,
    delivered_at: datetime | str | None = None,
    *,
    ledger: Ledger | None = None,
    deadline: float | None = None,
) -> DeliveryReceipt:
    """Call only after confirmed send; retry the same id/time without resending audio."""
    try:
        profile = Profile.from_dict(json.loads(plan.profile_json))
        ledger = ledger or Ledger.for_profile(profile)
        return ledger.record(plan, delivery_id, delivered_at or datetime.now(timezone.utc), deadline=deadline)
    except Exception as error:
        return DeliveryReceipt(diagnostics=(Diagnostic("ledger", type(error).__name__),))
