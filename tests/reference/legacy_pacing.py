"""Legacy tier compatibility, separate from the locked four-layer compiler.

No tier-to-layer conversion: new lessons use speech_pacing.compile_lesson.
Legacy annotations fail open as a whole if any sentinel cannot be rendered.
"""

from __future__ import annotations

import logging
import re

from better_speech.curriculum import js_collapse_whitespace, strip_tiers

DEFAULT_PAUSE_STYLE = "16"
DEFAULT_PAUSE_TEXT_MODE = "none"
WORD_SEAM_MARKER = " • "
PAUSE_STYLES = {
    "16": (" ", " • ", " ... ", " – "),
    "15": (" ", " • ", " ... ", " - "),
    "off": (" ", " ", " ", " "),
}
PROVIDERS = frozenset({"elevenlabs", "openai", "google"})
_SENTINEL = re.compile(r"⟨([0-9])⟩")
_WARNED: set[str] = set()
_LOGGER = logging.getLogger(__name__)


def reset_pause_warnings() -> None:
    """Test seam matching V1's process-local warning reset."""
    _WARNED.clear()


def _warn_once(key: str, message: str) -> None:
    if key in _WARNED:
        return
    _WARNED.add(key)
    try:
        _LOGGER.warning("polyglot-pause: %s", message)
    except Exception:
        # Optional diagnostics must never prevent the original phrase.
        pass


def has_tier_sentinels(text: str) -> bool:
    return "⟨" in text or "⟩" in text


def is_known_pause_style(style: object) -> bool:
    return isinstance(style, str) and style in PAUSE_STYLES


def is_pause_text_mode(mode: object) -> bool:
    return isinstance(mode, str) and mode in {"none", "pause", "word"}


def _render(text: str, seams: tuple[str, ...], warning_key: str) -> str:
    aborted = False

    def replace(match: re.Match) -> str:
        nonlocal aborted
        tier = int(match[1])
        if tier > 3:
            aborted = True
            return " "
        return seams[tier]

    rendered = _SENTINEL.sub(replace, text)
    if aborted or has_tier_sentinels(rendered):
        _warn_once(warning_key, f"render anomaly under {warning_key}; rendering plain text")
        return strip_tiers(text)
    return js_collapse_whitespace(rendered)


def render_pauses_for_audio(text: str, provider: str, style: str = DEFAULT_PAUSE_STYLE) -> str:
    if not is_known_pause_style(style):
        _warn_once(f"style:{style}", f"unknown pause style {style!r}; rendering plain text")
        return strip_tiers(text)
    if provider not in PROVIDERS:
        _warn_once(f"provider:{provider}", f"no pause map for provider {provider!r}; rendering plain text")
        return strip_tiers(text)
    return _render(text, PAUSE_STYLES[style], f"audio:{style}")


def render_pauses_for_text(
    text: str, mode: str = DEFAULT_PAUSE_TEXT_MODE, style: str = DEFAULT_PAUSE_STYLE
) -> str:
    if mode == "none":
        return strip_tiers(text)
    if mode == "word":
        return _render(text, (WORD_SEAM_MARKER,) * 4, "text:word")
    if not is_known_pause_style(style):
        _warn_once(f"style:{style}", f"unknown pause style {style!r}; rendering plain text")
        return strip_tiers(text)
    # V1's typed caller accepts none/pause/word; its runtime default branch
    # treats other mode strings as pause. Preserve that compatibility here.
    return _render(text, PAUSE_STYLES[style], f"text:{style}")
