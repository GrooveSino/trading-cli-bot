from __future__ import annotations

import os
import re
from collections.abc import Mapping, Sequence
from typing import Any


REDACTED = "<redacted>"

_SENSITIVE_KEY_PARTS = (
    "authorization",
    "api_key",
    "apikey",
    "secret",
    "password",
    "passphrase",
    "token",
    "signature",
)
_SENSITIVE_ENV_NAMES = (
    "BINANCE_API_KEY",
    "BINANCE_API_SECRET",
    "OKX_API_KEY",
    "OKX_API_SECRET",
    "OKX_PASSWORD",
    "OKX_SIM_API_KEY",
    "OKX_SIM_API_SECRET",
    "OKX_SIM_PASSWORD",
    "GATE_API_KEY",
    "GATE_API_SECRET",
    "GATE_PASSWORD",
    "MEXC_API_KEY",
    "MEXC_API_SECRET",
    "MEXC_PASSWORD",
)
_BEARER_RE = re.compile(r"(bearer\s+)([^\s,;]+)", re.IGNORECASE)
_SIGNED_QUERY_RE = re.compile(r"([?&](?:signature|secret|apiKey|password|token)=)([^&\s]+)", re.IGNORECASE)


def is_sensitive_key(key: object) -> bool:
    text = str(key or "").strip().lower().replace("-", "_")
    return any(part in text for part in _SENSITIVE_KEY_PARTS)


def sensitive_env_values() -> tuple[str, ...]:
    values: list[str] = []
    for name in _SENSITIVE_ENV_NAMES:
        value = (os.getenv(name) or "").strip()
        if len(value) >= 4:
            values.append(value)
    return tuple(dict.fromkeys(values))


def redact_text(value: object) -> str:
    text = str(value)
    text = _BEARER_RE.sub(r"\1" + REDACTED, text)
    text = _SIGNED_QUERY_RE.sub(r"\1" + REDACTED, text)
    for secret in sensitive_env_values():
        text = text.replace(secret, REDACTED)
    return text


def redact_mapping(value: Any) -> Any:
    if isinstance(value, Mapping):
        return {key: REDACTED if is_sensitive_key(key) else redact_mapping(item) for key, item in value.items()}
    if isinstance(value, Sequence) and not isinstance(value, (str, bytes, bytearray)):
        return [redact_mapping(item) for item in value]
    if isinstance(value, str):
        return redact_text(value)
    return value
