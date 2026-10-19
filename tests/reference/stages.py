"""Pure contracts for optional enrichment; no delivery or credential authority.

The owning worker supplies one deadline measured from enqueue, including any
queue wait. Stages share it rather than starting new budgets for fallback legs.
"""

from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass
from typing import Callable, Generic, Literal, TypeVar

T = TypeVar("T")


@dataclass(frozen=True)
class StageInput:
    source: str
    source_revision: str
    settings_digest: str
    asset_digest: str
    upstream_plan_digest: str = ""
    span_identity: str = ""

    def cache_identity(self, stage: str, version: str, *, inputs: dict) -> str:
        """Caller includes prompt/route versions and stage-specific context.

        In particular a polyglot caller must include its exposure snapshot,
        selection time, ET day and RNG seed. Failures are never cache entries.
        """
        payload = {
            "schema": 1,
            "stage": stage,
            "version": version,
            "source": self.source,
            "source_revision": self.source_revision,
            "settings": self.settings_digest,
            "asset": self.asset_digest,
            "upstream": self.upstream_plan_digest,
            "spans": self.span_identity,
            "inputs": inputs,
        }
        encoded = json.dumps(
            payload, sort_keys=True, ensure_ascii=False, separators=(",", ":"), allow_nan=False
        ).encode()
        return hashlib.sha256(encoded).hexdigest()


@dataclass(frozen=True)
class StageContext:
    deadline: float
    clock: Callable[[], float]
    cancelled: Callable[[], bool] = lambda: False

    @classmethod
    def from_enqueue(
        cls,
        enqueued_at: float,
        clock: Callable[[], float],
        *,
        custody_deadline: float,
        budget: float = 180,
        cancelled: Callable[[], bool] = lambda: False,
    ) -> StageContext:
        return cls(min(enqueued_at + max(0, min(budget, 180)), custody_deadline), clock, cancelled)

    def remaining(self) -> float:
        return 0 if self.cancelled() else max(0, self.deadline - self.clock())


@dataclass(frozen=True)
class StageResult(Generic[T]):
    status: Literal["applied", "partial", "skipped"]
    plan: T
    cache_identity: str
    reason: str | None = None
    schema_version: int = 1
