from __future__ import annotations

from collections.abc import Callable
from typing import Any

import typer

from trading_gateway.infrastructure.exchange.factory import build_ccxt_client as default_build_ccxt_client
from trading_gateway.infrastructure.exchange.factory import close_client as default_close_client
from trading_gateway.support.redaction import redact_text


def require_confirm(actual: str, expected: str, label: str) -> None:
    if str(actual or "").strip() != expected:
        raise typer.BadParameter(f"{label} confirmation mismatch; expected {expected}", param_hint="--confirm")


build_ccxt_client = default_build_ccxt_client
close_client = default_close_client


def set_client_hooks(build: Callable[..., Any], close: Callable[[Any], None]) -> None:
    global build_ccxt_client, close_client
    build_ccxt_client = build
    close_client = close


def with_okx_client(action: Callable[[Any], dict]) -> dict:
    client = build_ccxt_client("okx", "swap", require_private=True)
    try:
        return action(client)
    except Exception as exc:  # noqa: BLE001 - CLI boundary returns concise redacted exchange errors.
        raise typer.BadParameter(redact_text(f"{type(exc).__name__}: {exc}")) from exc
    finally:
        close_client(client)
