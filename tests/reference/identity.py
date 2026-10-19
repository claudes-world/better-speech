import hashlib
import json
import unicodedata

from better_speech.cache import _encode


def rendition_identity(
    *,
    provider: str,
    model: str,
    voice: str,
    response_format: str,
    text: str,
    input_mode: str = "text",
) -> tuple[str, str, str]:
    if input_mode not in {"text", "ssml"}:
        raise ValueError("invalid speech input mode")
    input_sha256 = hashlib.sha256(text.encode("utf-8")).hexdigest()
    encoded = json.dumps(
        {
            "provider": provider,
            "model": model,
            "voice": voice,
            "response_format": response_format,
            "input_sha256": input_sha256,
            # Preserve existing plain-text keys/old rows; marked requests
            # cannot coalesce with plain text that happens to contain XML.
            **({"input_mode": input_mode, "schema_version": 2} if input_mode != "text" else {}),
        },
        separators=(",", ":"),
        sort_keys=True,
    ).encode("utf-8")
    cache_key = hashlib.sha256(encoded).hexdigest()
    return f"tts:{cache_key}", cache_key, input_sha256


def legacy_phrase_bytes(*, provider: str, voice: str, rate: float, text: str, format: str) -> bytes:
    """V1 ordered JSON, retained for provenance; not an active fallback key."""
    return _encode(
        {
            "provider": provider,
            "voice": voice,
            "rate": rate,
            "text": unicodedata.normalize("NFC", text),
            "format": format,
        },
        sort_keys=False,
    ).encode("utf-8")


def legacy_phrase_key(**inputs) -> str:
    return hashlib.sha256(legacy_phrase_bytes(**inputs)).hexdigest()
