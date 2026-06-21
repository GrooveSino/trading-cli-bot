from __future__ import annotations

from typing import Any

from trading_gateway.app.config import get_gateway_config
from trading_gateway.infrastructure.exchange.factory import build_ccxt_client, close_client
from trading_gateway.infrastructure.exchange.static_client import StaticLabClient
from trading_gateway.interfaces.daemon.client import DaemonClientError, daemon_http_post
from trading_gateway.workflows.pair_trade.planning import build_pair_close_plan, build_pair_plan
from trading_gateway.workflows.single_leg.planning import SingleLegIntent, build_single_leg_trade_plan, ccxt_symbol


def build_single_leg_cli_plan(
    *,
    exchange: str,
    market: str,
    action: str,
    symbol: str,
    quote_usdt: float | None,
    bbo: bool,
    last_price: float | None,
    limit_price: float | None = None,
    take_profit: float | None = None,
    stop_loss: float | None = None,
    margin_mode: str | None = None,
    leverage: int | None = None,
    account_mode: str | None = None,
) -> dict[str, Any]:
    intent = SingleLegIntent(
        exchange=exchange,
        market=market,
        action=action,
        symbol=symbol,
        quote_usdt=quote_usdt,
        bbo=bbo,
        limit_price=limit_price,
        take_profit=take_profit,
        stop_loss=stop_loss,
        margin_mode=margin_mode,
        leverage=leverage,
        account_mode=account_mode,
    )
    if last_price in (None, ""):
        daemon_plan = _daemon_plan_or_none(
            "/api/lab/plan",
            {
                "exchange": exchange,
                "market": market,
                "action": action,
                "symbol": symbol,
                "quote_usdt": quote_usdt,
                "bbo": bbo,
                "limit_price": limit_price,
                "take_profit": take_profit,
                "stop_loss": stop_loss,
                "margin_mode": margin_mode,
                "leverage": leverage,
                "account_mode": account_mode,
            },
        )
        if daemon_plan is not None:
            return daemon_plan["plan"]
    client = lab_client(exchange, market, intent.symbol, last_price, private=last_price is None, account_mode=account_mode)
    try:
        return build_single_leg_trade_plan(client, intent, universe_path=get_gateway_config().route_universe)
    finally:
        close_client(client)


def build_pair_cli_plan(
    *,
    spot_exchange: str,
    perp_exchange: str,
    symbol: str,
    quote_usdt: float,
    last_price: float | None,
    pair_client: Any,
) -> dict[str, Any]:
    if last_price in (None, ""):
        daemon_plan = _daemon_plan_or_none(
            "/api/pair/plan",
            {
                "spot_exchange": spot_exchange,
                "perp_exchange": perp_exchange,
                "symbol": symbol,
                "quote_usdt": quote_usdt,
            },
        )
        if daemon_plan is not None:
            return daemon_plan["plan"]
    spot = pair_client(spot_exchange, "spot", symbol, last_price, private=False)
    perp = pair_client(perp_exchange, "perp", symbol, last_price, private=False)
    try:
        return build_pair_plan(
            spot,
            perp,
            spot_exchange=spot_exchange,
            perp_exchange=perp_exchange,
            symbol=symbol,
            quote_usdt=quote_usdt,
            universe_path=get_gateway_config().route_universe,
        )
    finally:
        close_client(spot)
        close_client(perp)


def build_pair_close_cli_plan(
    *,
    spot_exchange: str,
    perp_exchange: str,
    symbol: str,
    last_price: float | None,
    pair_client: Any,
) -> dict[str, Any]:
    if last_price in (None, ""):
        daemon_plan = _daemon_plan_or_none(
            "/api/pair/close/plan",
            {
                "spot_exchange": spot_exchange,
                "perp_exchange": perp_exchange,
                "symbol": symbol,
            },
        )
        if daemon_plan is not None:
            return daemon_plan["plan"]
    spot = pair_client(spot_exchange, "spot", symbol, last_price, private=False)
    perp = pair_client(perp_exchange, "perp", symbol, last_price, private=False)
    try:
        return build_pair_close_plan(
            spot,
            perp,
            spot_exchange=spot_exchange,
            perp_exchange=perp_exchange,
            symbol=symbol,
            universe_path=get_gateway_config().route_universe,
        )
    finally:
        close_client(spot)
        close_client(perp)


def cli_pair_client(exchange: str, market: str, symbol: str, last_price: float | None, *, private: bool) -> Any:
    return lab_client(exchange, market, ccxt_symbol(exchange, market, symbol), last_price, private=private)


def lab_client(exchange: str, market: str, symbol: str, last_price: float | None, *, private: bool, account_mode: str | None = None) -> Any:
    if last_price is not None:
        return StaticLabClient(exchange, symbol, market, last_price)
    return build_ccxt_client(exchange, "swap" if market == "perp" else "spot", require_private=private, account_mode=account_mode)


def _daemon_plan_or_none(path: str, body: dict[str, Any]) -> dict[str, Any] | None:
    try:
        return daemon_http_post(path, body, timeout_sec=3.0)
    except DaemonClientError:
        return None
