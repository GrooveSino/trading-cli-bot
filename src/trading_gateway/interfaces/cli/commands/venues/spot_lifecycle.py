from __future__ import annotations

import time
from typing import Any

from trading_gateway.application.market.specs import MarketSpec
from trading_gateway.infrastructure.exchange.factory import build_ccxt_client, close_client
from trading_gateway.support.redaction import redact_mapping, redact_text


def execute_spot_lifecycle(spec: MarketSpec, quote_usdt: float, confirm: str) -> dict[str, Any]:
    symbol = f"{spec.base}/USDT"
    client = build_ccxt_client("okx", "spot", require_private=True, account_mode="sim")
    bought_amount: float | None = None
    steps: list[dict[str, Any]] = []
    try:
        passive = _create_passive_buy(client, symbol, steps)
        cancelled = _cancel_order(client, symbol, passive)
        steps.append({"name": "spot_passive_order_cancel", "status": "ok", "order": cancelled})
        buy = client.create_order(symbol, "market", "buy", None, None, {"tdMode": "cash", "tgtCcy": "quote_ccy", "sz": str(float(quote_usdt))})
        steps.append({"name": "spot_market_buy", "status": "ok", "order": buy})
        time.sleep(1)
        bought_amount = _filled_amount(client, symbol, buy)
        sell = client.create_order(symbol, "market", "sell", bought_amount, None, {"tdMode": "cash"})
        steps.append({"name": "spot_market_sell_close", "status": "ok", "order": sell, "amount": bought_amount})
        time.sleep(1)
        balance = client.fetch_balance().get(spec.base, {})
        return redact_mapping({"mode": "okx_sim_lifecycle", "status": "ok", "market": "spot", "confirm": confirm, "symbol": spec.key, "ccxt_symbol": symbol, "steps": steps, "base_balance_after": balance})
    except Exception as exc:  # noqa: BLE001 - leave a structured audit trail and attempt cleanup.
        rescue = _rescue_sell(client, symbol, bought_amount) if bought_amount else None
        return redact_mapping({"mode": "okx_sim_lifecycle", "status": "error", "market": "spot", "symbol": spec.key, "steps": steps, "rescue_close": rescue, "error": redact_text(f"{type(exc).__name__}: {exc}")})
    finally:
        close_client(client)


def _create_passive_buy(client: Any, symbol: str, steps: list[dict[str, Any]]) -> dict[str, Any]:
    book = client.fetch_order_book(symbol) or {}
    bid = float((book.get("bids") or [[0]])[0][0] or 0)
    if bid <= 0:
        raise ValueError(f"best bid unavailable for {symbol}")
    market = _market(client, symbol)
    min_amount = float(((market.get("limits") or {}).get("amount") or {}).get("min") or 0)
    amount = _amount_to_precision(client, symbol, max(min_amount, _passive_base_amount(symbol)))
    price = _price_to_precision(client, symbol, bid * 0.95)
    order = client.create_order(symbol, "limit", "buy", amount, price, {"tdMode": "cash", "ordType": "post_only"})
    steps.append({"name": "spot_passive_order_create", "status": "ok", "order": order})
    return order


def _filled_amount(client: Any, symbol: str, order: dict[str, Any]) -> float:
    order_id = str(order.get("id") or (order.get("info") or {}).get("ordId") or "")
    fetched = client.fetch_order(order_id, symbol) if order_id else order
    amount = float(fetched.get("filled") or order.get("filled") or 0)
    if amount <= 0 and order_id:
        trades = client.fetch_my_trades(symbol, since=None, limit=10)
        amount = sum(float(row.get("amount") or 0) for row in trades if str(row.get("order")) == order_id)
    amount = _amount_to_precision(client, symbol, amount)
    if amount <= 0:
        raise RuntimeError("market buy filled amount unavailable")
    return amount


def _cancel_order(client: Any, symbol: str, order: dict[str, Any]) -> dict[str, Any]:
    order_id = order.get("id") or order.get("ordId") or (order.get("info") or {}).get("ordId")
    if not order_id:
        return {"status": "skipped", "reason": "order id missing", "source_order": order}
    return client.cancel_order(str(order_id), symbol)


def _rescue_sell(client: Any, symbol: str, amount: float | None) -> dict[str, Any] | None:
    if not amount:
        return None
    try:
        return client.create_order(symbol, "market", "sell", amount, None, {"tdMode": "cash"})
    except Exception as exc:  # noqa: BLE001
        return {"status": "error", "error": redact_text(exc)}


def _market(client: Any, symbol: str) -> dict[str, Any]:
    markets = client.load_markets() if callable(getattr(client, "load_markets", None)) else {}
    row = (markets or getattr(client, "markets", {}) or {}).get(symbol)
    if not row:
        raise ValueError(f"symbol not found in exchange markets: {symbol}")
    return row


def _passive_base_amount(symbol: str) -> float:
    return 0.00006 if symbol.startswith("BTC/") else 0.003


def _amount_to_precision(client: Any, symbol: str, amount: float) -> float:
    precision = getattr(client, "amount_to_precision", None)
    return float(precision(symbol, amount)) if callable(precision) else float(amount)


def _price_to_precision(client: Any, symbol: str, price: float) -> float:
    precision = getattr(client, "price_to_precision", None)
    return float(precision(symbol, price)) if callable(precision) else float(price)
