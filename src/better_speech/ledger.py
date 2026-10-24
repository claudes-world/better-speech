"""Delivered-only SQLite events, reduced chronologically inside one write transaction."""

from __future__ import annotations

import json
import sqlite3
import time
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from datetime import time as day_time
from pathlib import Path
from zoneinfo import ZoneInfo

from filelock import FileLock

from .budget import remaining
from .config import state_dir
from .curriculum import validate_inventory
from .exposure import record_exposures, timestamp
from .types import Diagnostic, Profile, canonical_json

SCHEMA = """
CREATE TABLE IF NOT EXISTS learner_phrases (
    learner_id TEXT NOT NULL,
    phrase_id TEXT NOT NULL,
    record_json TEXT NOT NULL,
    PRIMARY KEY (learner_id, phrase_id)
);
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
            # SQLite can return SQLITE_BUSY immediately during first WAL setup.
            # Serialize initialization, including version inspection/migration.
            with FileLock(str(self.path) + ".init.lock", timeout=timeout):
                version = self._execute(connection, "PRAGMA user_version", deadline=deadline).fetchone()[0]
                if version not in (0, 1, 2):
                    raise ValueError("unsupported ledger user_version")
                if version < 2:
                    self._execute(connection, "PRAGMA journal_mode=WAL", deadline=deadline)
                    self._execute(connection, "BEGIN IMMEDIATE", deadline=deadline)
                    try:
                        for statement in SCHEMA.split(";"):
                            if statement.strip():
                                self._execute(connection, statement, deadline=deadline)
                        self._execute(connection, "PRAGMA user_version=2", deadline=deadline)
                        connection.commit()
                    except BaseException:
                        connection.rollback()
                        raise
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

    def learner_state(self, learner_id, *, now=None, timezone=None, deadline=None):
        """Read exposure history and learner additions from one SQLite snapshot."""
        if not self.path.exists():
            return {}, []
        connection = self._connect(deadline)
        try:
            self._execute(connection, "BEGIN", deadline=deadline)
            records = {
                key: json.loads(value)
                for key, value in self._execute(
                    connection, "SELECT phrase_key, record_json FROM phrases WHERE learner_id=?",
                    (learner_id,), deadline=deadline,
                )
            }
            if now is not None:
                local = datetime.fromisoformat(now.replace("Z", "+00:00")).astimezone(ZoneInfo(timezone))
                start = datetime.combine(local.date(), day_time(), local.tzinfo)
                end = datetime.combine(local.date() + timedelta(days=1), day_time(), local.tzinfo)
                for key, count in self._execute(
                    connection, "SELECT phrase_key, COUNT(*) FROM events WHERE learner_id=? "
                    "AND delivered_at>=? AND delivered_at<? GROUP BY phrase_key",
                    (learner_id, timestamp(start.isoformat()), timestamp(end.isoformat())), deadline=deadline,
                ):
                    records[key]["dailyCount"] = count
            rows = self._learner_rows(connection, learner_id, deadline=deadline)
            return records, rows
        finally:
            connection.close()

    def _learner_rows(self, connection, learner_id, *, deadline=None):
        return [json.loads(raw) for (raw,) in self._execute(
            connection, "SELECT record_json FROM learner_phrases WHERE learner_id=? ORDER BY rowid",
            (learner_id,), deadline=deadline,
        )]

    def snapshot(self, learner_id="liam", *, deadline=None):
        return self.learner_state(learner_id, deadline=deadline)[0]

    def edit_phrases(self, learner_id, edit, *, deadline=None):
        """Read/modify/write a learner's queue under the ledger's writer transaction."""
        connection = self._connect(deadline)
        try:
            self._execute(connection, "BEGIN IMMEDIATE", deadline=deadline)
            rows = self._learner_rows(connection, learner_id, deadline=deadline)
            result = edit(rows)
            validate_inventory(rows)
            for row in rows:
                self._execute(
                    connection, "INSERT INTO learner_phrases VALUES(?,?,?) "
                    "ON CONFLICT(learner_id,phrase_id) DO UPDATE SET record_json=excluded.record_json",
                    (learner_id, row["id"], canonical_json(row)), deadline=deadline,
                )
            if deadline is not None:
                remaining(deadline)
            connection.commit()
            return result
        except BaseException:
            connection.rollback()
            raise
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
                if lesson.record.get("category") in ("greeting", "opener", "closing", "bookend"):
                    prefix = "lastClosing" if lesson.record["category"] == "closing" or (lesson.record["category"] == "bookend" and lesson.record["lesson_type"] == "learning_sentence") else "lastGreeting"
                    records[lesson.phrase_key][prefix + "Key"] = lesson.phrase_key
                    records[lesson.phrase_key][prefix + "At"] = at
                    records[lesson.phrase_key][prefix + "Order"] = self._execute(
                        connection, "SELECT rowid FROM events WHERE learner_id=? AND phrase_key=? "
                        "ORDER BY delivered_at DESC,rowid DESC LIMIT 1",
                        (plan.learner_id, lesson.phrase_key), deadline=deadline,
                    ).fetchone()[0]
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
    ledger: Ledger,
    deadline: float | None = None,
) -> DeliveryReceipt:
    """Call only after confirmed send; retry the same id/time without resending audio."""
    try:
        return ledger.record(plan, delivery_id, delivered_at or datetime.now(timezone.utc), deadline=deadline)
    except Exception as error:
        return DeliveryReceipt(diagnostics=(Diagnostic("ledger", type(error).__name__),))
