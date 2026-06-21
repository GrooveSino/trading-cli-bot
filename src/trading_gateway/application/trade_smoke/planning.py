from __future__ import annotations

from typing import Any

from trading_gateway.domain.models import (
    OrderIntent,
    OrderPlan,
    display_market,
    format_decimal,
)
from trading_gateway.app.config import get_gateway_config
from trading_gateway.infrastructure.exchange.account_modes import normalize_account_mode


def order_confirm_phrase(intent: OrderIntent) -> str:
    if intent.quote_usdt is None:
        raise ValueError("live order confirmation requires quote_usdt")
    mode = normalize_account_mode(intent.account_mode, exchange=intent.exchange).upper()
    exchange = _confirm_exchange_label(intent.exchange, mode)
    return (
        f"LIVE_ORDER:{exchange}:{display_market(intent.market)}:"
        f"{intent.symbol}:{format_decimal(float(intent.quote_usdt))}"
    )


def _confirm_exchange_label(exchange: str, mode: str) -> str:
    name = exchange.lower()
    if name == "okx" and mode == "LIVE":
        return "OKX_LIVE"
    if name == "okx" and mode == "SIM":
        return "OKX_SIM"
    return exchange if mode == "LIVE" else f"{exchange.upper()}_{mode}"


def _last_price(client: Any, symbol: str, override: float | None = None) -> float:
    if override is not None:
        price = float(override)
    else:
        ticker = client.fetch_ticker(symbol) or {}
        price = float(ticker.get("last") or ticker.get("close") or ticker.get("bid") or ticker.get("ask") or 0)
    if price <= 0:
        raise ValueError(f"last price unavailable for {symbol}")
    return price


def _markets(client: Any) -> dict[str, Any]:
    markets = getattr(client, "markets", None)
    if markets:
        return markets
    loaded = client.load_markets()
    return loaded or getattr(client, "markets", {}) or {}


def _amount_to_precision(client: Any, symbol: str, amount: float) -> float:
    method = getattr(client, "amount_to_precision", None)
    if not callable(method):
        return amount
    return float(method(symbol, amount))


def _swap_params(intent: OrderIntent) -> dict[str, Any]:
    params: dict[str, Any] = {}
    if intent.margin_mode:
        params["marginMode"] = intent.margin_mode
        params["tdMode"] = intent.margin_mode
    if intent.position_mode == "hedge":
        pos_side = "long" if intent.side == "buy" else "short"
        params["positionSide"] = pos_side.upper()
        params["posSide"] = pos_side
    return params


def _supports_spot_buy_cost(client: Any) -> bool:
    has = getattr(client, "has", {}) or {}
    method = getattr(client, "create_market_buy_order_with_cost", None)
    return bool(has.get("createMarketBuyOrderWithCost")) and callable(method)


def build_order_plan(
    client: Any,
    intent: OrderIntent,
    *,
    last_price: float | None = None,
) -> OrderPlan:
    markets = _markets(client)
    market = markets.get(intent.symbol)
    if not market:
        raise ValueError(f"symbol not found in exchange markets: {intent.symbol}")
    price = _last_price(client, intent.symbol, last_price)
    if intent.base_amount is None and intent.quote_usdt is None:
        raise ValueError("quote_usdt or base_amount is required")
    if intent.base_amount is not None:
        base_amount = float(intent.base_amount)
    else:
        quote_usdt = intent.quote_usdt
        if quote_usdt is None:
            raise ValueError("quote_usdt or base_amount is required")
        base_amount = float(quote_usdt) / price
    quote = float(intent.quote_usdt) if intent.quote_usdt is not None else base_amount * price
    contract_amount: float | None = None
    cost_amount: float | None = None
    amount = base_amount
    params: dict[str, Any] = {}
    order_method = "create_order"
    if intent.market == "swap":
        contract_size = float(market.get("contractSize") or market.get("contract_size") or 1)
        if contract_size <= 0:
            raise ValueError(f"invalid contractSize for {intent.symbol}")
        contract_amount = _amount_to_precision(client, intent.symbol, base_amount / contract_size)
        amount = contract_amount
        params = _swap_params(intent)
    else:
        amount = _amount_to_precision(client, intent.symbol, base_amount)
        if intent.side == "buy" and _supports_spot_buy_cost(client):
            cost_amount = quote
            order_method = "create_market_buy_order_with_cost"
    return OrderPlan(
        exchange=intent.exchange,
        market=intent.market,
        symbol=intent.symbol,
        side=intent.side,
        last_price=price,
        base_amount=base_amount,
        contract_amount=contract_amount,
        cost_amount=cost_amount,
        amount=amount,
        quote_usdt=quote,
        params=params,
        order_method=order_method,
        live_confirm_phrase=order_confirm_phrase(intent),
        account_mode=normalize_account_mode(intent.account_mode, exchange=intent.exchange),
    )


def validate_live_request(intent: OrderIntent, *, live: bool, confirm: str) -> None:
    if not live:
        return
    cap = get_gateway_config().lab_max_quote_usdt
    if intent.quote_usdt is None or float(intent.quote_usdt) > cap:
        raise ValueError(f"live order requires quote_usdt <= {format_decimal(cap)}")
    expected = order_confirm_phrase(intent)
    if str(confirm or "").strip() != expected:
        raise ValueError(f"live order confirmation mismatch; expected {expected}")
