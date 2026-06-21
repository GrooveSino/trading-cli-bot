from __future__ import annotations

import time
from typing import Any

from trading_gateway.application.market.specs import MarketSpec, VenueProfile, get_market_spec
from trading_gateway.domain.models import format_decimal
from trading_gateway.infrastructure.exchange.factory import build_ccxt_client, close_client
from trading_gateway.interfaces.cli.commands.venues.readiness import okx_sim_trade_readiness
from trading_gateway.interfaces.cli.commands.venues.spot_lifecycle import execute_spot_lifecycle
from trading_gateway.support.redaction import redact_mapping, redact_text


def okx_sim_lifecycle_plan(symbol: str, side: str, quote_usdt: float, market: str = "spot") -> dict[str, Any]:
    spec = get_market_spec(symbol)
    steps = _plan_steps(market)
    return {
        "mode": "okx_sim_lifecycle_plan",
        "status": "dry_run",
        "venue": "okx-sim",
        "market": market,
        "symbol": spec.key,
        "ccxt_symbol": _ccxt_symbol(spec, market),
        "side": side,
        "quote_usdt": quote_usdt,
        "live_confirm_phrase": lifecycle_confirm_phrase(spec, side, quote_usdt),
        "steps": steps,
    }


def lifecycle_confirm_phrase(spec: MarketSpec, side: str, quote_usdt: float) -> str:
    return f"OKX_SIM_LIFECYCLE:{spec.key.upper()}:{side.upper()}:QUOTE_{format_decimal(float(quote_usdt))}"


def run_okx_sim_lifecycle(
    profile: VenueProfile,
    symbol: str,
    side: str,
    quote_usdt: float,
    leverage: int,
    margin_mode: str,
    live: bool,
    confirm: str,
    market: str = "spot",
) -> dict[str, Any]:
    spec = get_market_spec(symbol)
    market = _normalize_lifecycle_market(market)
    plan = okx_sim_lifecycle_plan(symbol, side, quote_usdt, market)
    if profile.id != "okx-sim":
        raise ValueError("lifecycle verification is OKX sim only; use tbot sim btc test")
    if not live:
        return plan
    expected = plan["live_confirm_phrase"]
    if str(confirm or "").strip() != expected:
        raise ValueError(f"live lifecycle confirmation mismatch; expected {expected}")
    readiness = okx_sim_trade_readiness(spec.okx_ccxt_symbol)
    if readiness["status"] != "ok":
        return {
            "mode": "okx_sim_lifecycle",
            "status": "not_ready",
            "market": market,
            "symbol": spec.key,
            "confirm": expected,
            "checks": readiness["checks"],
            "error": readiness.get("error") or "OKX sim private readiness failed",
        }
    if market == "spot":
        return execute_spot_lifecycle(spec, quote_usdt, expected)
    return _execute_lifecycle(spec, side, quote_usdt, leverage, margin_mode, expected)


def _normalize_lifecycle_market(market: str) -> str:
    text = str(market or "spot").strip().lower()
    if text in {"perp", "swap"}:
        return "swap"
    if text == "spot":
        return "spot"
    raise ValueError("market must be spot or swap")


def _ccxt_symbol(spec: MarketSpec, market: str) -> str:
    return f"{spec.base}/USDT" if market == "spot" else spec.okx_ccxt_symbol


def _plan_steps(market: str) -> list[str]:
    if market == "spot":
        return ["create passive post-only spot order", "cancel passive order", "buy spot with quote USDT", "sell bought spot amount"]
    return ["create passive post-only swap order", "cancel passive order", "open demo market position", "close demo position reduce-only"]


def _execute_lifecycle(spec: MarketSpec, side: str, quote_usdt: float, leverage: int, margin_mode: str, confirm: str) -> dict[str, Any]:
    client = build_ccxt_client("okx", "swap", require_private=True, account_mode="sim")
    opened = False
    steps: list[dict[str, Any]] = []
    try:
        _prepare_swap(client, spec.okx_ccxt_symbol, leverage, margin_mode, steps)
        amount = _planned_amount(client, spec.okx_ccxt_symbol, quote_usdt)
        passive = _create_passive_order(client, spec.okx_ccxt_symbol, side, amount, margin_mode)
        steps.append({"name": "passive_order_create", "status": "ok", "order": passive})
        cancelled = _cancel_order(client, spec.okx_ccxt_symbol, passive)
        steps.append({"name": "passive_order_cancel", "status": "ok", "order": cancelled})
        opened_order = client.create_order(spec.okx_ccxt_symbol, "market", side, amount, None, _market_params(margin_mode))
        opened = True
        steps.append({"name": "market_open", "status": "ok", "order": opened_order})
        time.sleep(1)
        close_amount = _position_contracts(client, spec.okx_ccxt_symbol, side) or amount
        closed = client.create_order(spec.okx_ccxt_symbol, "market", _opposite(side), close_amount, None, _close_params(margin_mode))
        opened = False
        steps.append({"name": "reduce_only_close", "status": "ok", "order": closed})
        time.sleep(1)
        return redact_mapping({"mode": "okx_sim_lifecycle", "status": "ok", "market": "swap", "confirm": confirm, "symbol": spec.key, "steps": steps, "positions_after": _safe_positions(client, spec.okx_ccxt_symbol)})
    except Exception as exc:  # noqa: BLE001 - surface concise diagnostics and attempt cleanup.
        # If the demo open succeeded but later steps fail, try to leave the test flat.
        rescue = _rescue_close(client, spec.okx_ccxt_symbol, side, margin_mode) if opened else None
        return redact_mapping({"mode": "okx_sim_lifecycle", "status": "error", "market": "swap", "symbol": spec.key, "steps": steps, "rescue_close": rescue, "error": redact_text(f"{type(exc).__name__}: {exc}")})
    finally:
        close_client(client)


def _prepare_swap(client: Any, symbol: str, leverage: int, margin_mode: str, steps: list[dict[str, Any]]) -> None:
    for name, method, args in [("margin_mode", "set_margin_mode", (margin_mode, symbol)), ("leverage", "set_leverage", (int(leverage), symbol, {"mgnMode": margin_mode}))]:
        fn = getattr(client, method, None)
        if not callable(fn):
            continue
        try:
            steps.append({"name": name, "status": "ok", "result": fn(*args)})
        except Exception as exc:  # noqa: BLE001 - existing settings may already match.
            steps.append({"name": name, "status": "warning", "error": redact_text(exc)})


def _planned_amount(client: Any, symbol: str, quote_usdt: float) -> float:
    ticker = client.fetch_ticker(symbol) or {}
    last = float(ticker.get("last") or ticker.get("close") or ticker.get("ask") or ticker.get("bid") or 0)
    if last <= 0:
        raise ValueError(f"last price unavailable for {symbol}")
    market = _market(client, symbol)
    contract_size = _positive(market.get("contractSize") or (market.get("info") or {}).get("ctVal")) or 1.0
    raw_amount = float(quote_usdt) / last / contract_size
    min_amount = _positive(((market.get("limits") or {}).get("amount") or {}).get("min"))
    amount = max(raw_amount, min_amount)
    precision = getattr(client, "amount_to_precision", None)
    if callable(precision):
        amount = float(precision(symbol, amount))
    if amount <= 0:
        raise ValueError(f"planned amount is zero for {symbol}")
    return amount


def _market(client: Any, symbol: str) -> dict[str, Any]:
    markets = client.load_markets() if callable(getattr(client, "load_markets", None)) else {}
    markets = markets or getattr(client, "markets", {}) or {}
    row = markets.get(symbol)
    if not row:
        raise ValueError(f"symbol not found in exchange markets: {symbol}")
    return row


def _create_passive_order(client: Any, symbol: str, side: str, amount: float, margin_mode: str) -> dict[str, Any]:
    book = client.fetch_order_book(symbol) or {}
    bid = float((book.get("bids") or [[0]])[0][0] or 0)
    ask = float((book.get("asks") or [[0]])[0][0] or 0)
    raw_price = bid * 0.999 if side == "buy" else ask * 1.001
    price_fn = getattr(client, "price_to_precision", None)
    price = float(price_fn(symbol, raw_price)) if callable(price_fn) else raw_price
    return client.create_order(symbol, "limit", side, amount, price, {"tdMode": margin_mode, "ordType": "post_only"})


def _cancel_order(client: Any, symbol: str, order: dict[str, Any]) -> dict[str, Any]:
    order_id = order.get("id") or order.get("ordId") or (order.get("info") or {}).get("ordId")
    if not order_id:
        return {"status": "skipped", "reason": "order id missing", "source_order": order}
    return client.cancel_order(str(order_id), symbol)


def _position_contracts(client: Any, symbol: str, side: str) -> float | None:
    target = "long" if side == "buy" else "short"
    for row in _safe_positions(client, symbol):
        if _row_side(row) == target:
            value = _first(row.get("contracts"), row.get("size"), (row.get("info") or {}).get("pos"))
            try:
                return abs(float(value or 0)) or None
            except (TypeError, ValueError):
                return None
    return None


def _safe_positions(client: Any, symbol: str) -> list[dict[str, Any]]:
    try:
        return list(client.fetch_positions([symbol]) or [])
    except Exception:
        return []


def _rescue_close(client: Any, symbol: str, side: str, margin_mode: str) -> dict[str, Any] | None:
    amount = _position_contracts(client, symbol, side)
    if not amount:
        return None
    try:
        return client.create_order(symbol, "market", _opposite(side), amount, None, _close_params(margin_mode))
    except Exception as exc:  # noqa: BLE001
        return {"status": "error", "error": redact_text(exc)}


def _market_params(margin_mode: str) -> dict[str, Any]:
    return {"tdMode": margin_mode, "ordType": "market"}


def _close_params(margin_mode: str) -> dict[str, Any]:
    return {"tdMode": margin_mode, "ordType": "market", "reduceOnly": True}


def _opposite(side: str) -> str:
    return "sell" if side == "buy" else "buy"


def _row_side(row: dict[str, Any]) -> str | None:
    raw = str(row.get("side") or (row.get("info") or {}).get("posSide") or "").lower()
    return raw if raw in {"long", "short"} else None


def _first(*values: Any) -> Any:
    for value in values:
        if value not in (None, ""):
            return value
    return None


def _positive(value: Any) -> float:
    try:
        number = float(value or 0)
    except (TypeError, ValueError):
        return 0.0
    return number if number > 0 else 0.0
