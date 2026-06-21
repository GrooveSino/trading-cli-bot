from __future__ import annotations

from trading_gateway.infrastructure.exchange.factory import build_ccxt_client, close_client
from trading_gateway.interfaces.cli.commands.risk import app as _app
from trading_gateway.interfaces.cli.commands.risk.live import require_confirm, set_client_hooks, with_okx_client
from trading_gateway.interfaces.cli.commands.risk.rendering import _print_algo_orders, _print_live_result, _print_plan


def _sync_client_hooks() -> None:
    set_client_hooks(build_ccxt_client, close_client)


def register_risk_commands(app):
    return _app.register_risk_commands(app)


def risk_bracket(*args, **kwargs):
    _sync_client_hooks()
    return _app.risk_bracket(*args, **kwargs)


def risk_apply(*args, **kwargs):
    _sync_client_hooks()
    return _app.risk_apply(*args, **kwargs)


def risk_guarded_apply(*args, **kwargs):
    _sync_client_hooks()
    return _app.risk_guarded_apply(*args, **kwargs)


def risk_orders(*args, **kwargs):
    _sync_client_hooks()
    return _app.risk_orders(*args, **kwargs)


def risk_cancel(*args, **kwargs):
    _sync_client_hooks()
    return _app.risk_cancel(*args, **kwargs)


def risk_history(*args, **kwargs):
    _sync_client_hooks()
    return _app.risk_history(*args, **kwargs)


__all__ = [name for name in globals() if not name.startswith("_")]
