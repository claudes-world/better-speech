"""A single subscription-only selection seam. Providers are independently selected."""

from typing import Protocol


class ModelCall(Protocol):
    def __call__(self, payload: dict, *, timeout: float) -> str: ...
