from __future__ import annotations

import time
import uuid
from typing import Any

from trading_gateway.adapters.exchanges.order_params import build_force_close_params
from trading_gateway.adapters.exchanges.fees import order_fee_summary
from trading_gateway.adapters.exchanges.single_leg import adapter_for
from trading_gateway.support.redaction import redact_text
from trading_gateway.workflows.single_leg.rendering import build_single_leg_report
from trading_gateway.workflows.single_leg.recovery.runtime import (
    is_maker_reject,
    is_position_reduced_error,
    is_unknown_order_error,
    restore_order,
)


def submit_order(client: Any, plan: dict[str, Any], amount: float | None, base_params: dict[str, Any]) -> dict[str, Any]:
    order = plan["order"]
    params = dict(order.get("params") or {})
    params.update(base_params)
    params.setdefault("newClientOrderId", f"aslab_{uuid.uuid4().hex[:24]}")
    price = runtime_price(client, plan)
    return client.create_order(order["symbol"], order["type"], order["side"], amount, price, params)


def submit_force_close_order(client: Any, plan: dict[str, Any], amount: float, base_params: dict[str, Any]) -> dict[str, Any]:
    order = plan["order"]
    params = force_close_params(plan, base_params)
    params.setdefault("newClientOrderId", f"aslab_{uuid.uuid4().hex[:24]}")
    return client.create_order(order["symbol"], "market", order["side"], amount, None, params)


def monitor_order(client: Any, order: dict[str, Any], symbol: str, timeout_sec: float, poll_interval_sec: float, min_poll_interval_sec: float) -> dict[str, Any]:
    deadline = time.monotonic() + max(0, timeout_sec)
    while True:
        status = client.fetch_order(order["id"], symbol)
        if status.get("status") in {"closed", "canceled", "rejected", "expired"}:
            return status
        if time.monotonic() >= deadline:
            return status
        time.sleep(min(poll_interval_sec, max(min_poll_interval_sec, deadline - time.monotonic())))


def safe_monitor_order(client: Any, order: dict[str, Any], symbol: str, timeout_sec: float, poll_interval_sec: float, min_poll_interval_sec: float) -> dict[str, Any]:
    try:
        return monitor_order(client, order, symbol, timeout_sec, poll_interval_sec, min_poll_interval_sec)
    except Exception as exc:  # noqa: BLE001
        return {"id": order.get("id"), "status": "unknown", "error": submit_error(exc)}


def submit_error(exc: Exception) -> str:
    text = redact_text(exc)
    if "-4061" in text or "position side does not match" in text.lower():
        return f"{text}; Binance rejected positionSide. The CLI now auto-detects hedge mode and submits LONG/SHORT positionSide."
    return text


def close_all_submit_step(client: Any, symbol: str, exc: Exception, amount: float) -> dict[str, Any]:
    restored = restore_order(client, symbol)
    text = submit_error(exc)
    if restored.get("status") == "open":
        return {"status": "submit_unknown", "amount": amount, "order_id": restored.get("id"), "error": text}
    if is_maker_reject(exc):
        return {"status": "rejected", "amount": amount, "error": text}
    if is_position_reduced_error(exc):
        return {"status": "position_changed", "amount": amount, "error": text}
    if is_unknown_order_error(exc):
        return {"status": "submit_unknown", "amount": amount, "error": text}
    return {"status": "submit_error", "amount": amount, "error": text}


def order_amount(plan: dict[str, Any], base_quantity: float) -> float:
    size = float(plan.get("contract_size") or 1)
    if plan.get("quantity_unit") == "contracts" and size > 0:
        return base_quantity / size
    return base_quantity


def runtime_price(client: Any, plan: dict[str, Any]) -> float | None:
    order = plan["order"]
    params = order.get("params") or {}
    if order.get("type") == "market" or params.get("priceMatch"):
        return None
    if order.get("price") is not None:
        return float(order["price"])
    book = client.fetch_order_book(order["symbol"])
    rows = book.get("bids") if order.get("side") == "buy" else book.get("asks")
    if not rows:
        raise ValueError(f"order book has no maker price for {order['symbol']}")
    price = float(rows[0][0])
    method = getattr(client, "price_to_precision", None)
    return float(method(order["symbol"], price)) if callable(method) else price


def force_close_params(plan: dict[str, Any], base_params: dict[str, Any]) -> dict[str, Any]:
    adapter = adapter_for(str(plan.get("exchange") or ""), str(plan.get("market") or ""))
    return build_force_close_params(adapter, plan, base_params)


def dry_run_fee(plan: dict[str, Any], planned: Any | None = None, *, market: str | None = None) -> dict[str, Any]:
    order = plan.get("order") or {}
    amount = numeric_amount(order.get("amount")) or numeric_amount(plan.get("order_amount")) or numeric_amount(planned)
    return order_fee_summary(plan, order, {}, market=market or plan.get("market"), amount=amount, price=order.get("price"), liquidity=order_liquidity(order))


def order_liquidity(order: dict[str, Any]) -> str:
    return "taker" if str(order.get("type") or "").lower() == "market" else "maker"


def numeric_amount(value: Any) -> float | None:
    if value is None or str(value).upper() == "ALL":
        return None
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def report_execution(plan: dict[str, Any], steps: list[dict[str, Any]], final_status: str, target: dict[str, Any] | None = None) -> dict[str, Any]:
    return build_single_leg_report(plan, steps, final_status, target)
