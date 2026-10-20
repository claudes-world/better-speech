"""Add an adapter module and register its factory; the compiler stays unchanged."""

from collections.abc import Callable

from ..model_calls import ModelCall
from ..types import Profile
from .agy import AgyAdapter
from .claude import ClaudeAdapter
from .codex import CodexAdapter

REGISTRY: dict[str, Callable[[Profile], ModelCall]] = {
    "claude": ClaudeAdapter,
    "codex": CodexAdapter,
    "agy": AgyAdapter,
}


def register_model(name: str, factory: Callable[[Profile], ModelCall]) -> None:
    if not name or name == "none":
        raise ValueError("invalid adapter name")
    REGISTRY[name] = factory


def create_model(profile: Profile) -> ModelCall:
    return REGISTRY[profile.model](profile)
