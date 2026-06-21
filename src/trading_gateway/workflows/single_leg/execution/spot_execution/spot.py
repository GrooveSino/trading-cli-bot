from __future__ import annotations

import uuid
from collections.abc import Callable
from typing import Any

from trading_gateway.adapters.exchanges.fees import order_fee_summary
from trading_gateway.adapters.exchanges.rules import amount_to_precision
from trading_gateway.app.config import get_gateway_config
from trading_gateway.support.redaction import redact_text
from trading_gateway.support.tolerance import quantity_tolerance_from_quote
from trading_gateway.workflows.single_leg.execution.spot_execution.spot_rescue import maybe_spot_target_rescue, order_amount_reason

Progress = Callable[[dict[str, Any]], None]


def run_spot_target(
    client: Any,
    plan: dict[str, Any],
    steps: list[dict[str, Any]],
    *,
    timeout_sec: float,
    max_requotes: int,
    poll_interval_sec: float,
    min_poll_interval_sec: float,
    target_tolerance_steps: int,
    progress: Progress | None,
    add_step: Callable[[list[dict[str, Any]], dict[str, Any], Progress | None], None],
    emit_before: Callable[[Progress | None, dict[str, Any]], None],
    monitor_order: Callable[[Any, dict[str, Any], str, float, float, float], dict[str, Any]],
    cancel_or_restore: Callable[[Any, dict[str, Any], str, list[dict[str, Any]], Progress | None, Callable[[list[dict[str, Any]], dict[str, Any], Progress | None], None]], dict[str, Any]],
) -> tuple[str, dict[str, Any] | None]:
    balance, target, state, tolerance = _initial_spot_state(client, plan, target_tolerance_steps)
    add_step(steps, {"name": "balance_before", "status": "ok", **state, "balance": balance}, progress)
    add_step(steps, {"name": "target_balance", "status": "ok", **state}, progress)
    if target_reached(plan["action"], state):
        add_step(steps, {"name": "balance_verify", "status": "asset_target_reached", **state, "balance": balance}, progress)
        return "asset_target_reached", state
    status, balance, state, reason = _spot_requote_loop(
        client,
        plan,
        steps,
        balance,
        target,
        tolerance,
        max_requotes,
        timeout_sec,
        poll_interval_sec,
        min_poll_interval_sec,
        progress,
        add_step,
        emit_before,
        monitor_order,
        cancel_or_restore,
    )
    if status:
        return status, state
    return _verify_or_rescue(
        client,
        plan,
        steps,
        balance,
        target,
        tolerance,
        reason,
        timeout_sec,
        poll_interval_sec,
        min_poll_interval_sec,
        progress,
        add_step,
        emit_before,
        monitor_order,
    )


def _initial_spot_state(client: Any, plan: dict[str, Any], target_tolerance_steps: int) -> tuple[dict[str, float], float, dict[str, Any], float]:
    config = get_gateway_config()
    step = float(plan.get("quantity_step") or 0)
    fallback_tolerance = step * max(0, target_tolerance_steps)
    tolerance = quantity_tolerance_from_quote(float(plan.get("last_price") or 0), config.spot_execution.target_tolerance_quote_usdt, fallback_tolerance)
    balance = fetch_asset_balance(client, plan)
    target = target_balance(plan, balance)
    return balance, target, target_state(plan, balance, target, tolerance), tolerance


def _spot_requote_loop(
    client: Any,
    plan: dict[str, Any],
    steps: list[dict[str, Any]],
    balance: dict[str, float],
    target: float,
    tolerance: float,
    max_requotes: int,
    timeout_sec: float,
    poll_interval_sec: float,
    min_poll_interval_sec: float,
    progress: Progress | None,
    add_step: Callable[[list[dict[str, Any]], dict[str, Any], Progress | None], None],
    emit_before: Callable[[Progress | None, dict[str, Any]], None],
    monitor_order: Callable[[Any, dict[str, Any], str, float, float, float], dict[str, Any]],
    cancel_or_restore: Callable[[Any, dict[str, Any], str, list[dict[str, Any]], Progress | None, Callable[[list[dict[str, Any]], dict[str, Any], Progress | None], None]], dict[str, Any]],
) -> tuple[str | None, dict[str, float], dict[str, Any], str | None]:
    state = target_state(plan, balance, target, tolerance)
    terminal_reason: str | None = None
    for attempt in range(max_requotes + 1):
        amount, terminal_reason = _next_order_amount(client, plan, balance, target)
        if amount <= 0:
            state = {**state, "runtime_reason": terminal_reason}
            add_step(steps, {"name": "submit", "status": "blocked", "attempt": attempt + 1, "amount": amount, **state}, progress)
            if attempt == 0:
                return "blocked", balance, state, terminal_reason
            break
        result = _submit_and_monitor(client, plan, steps, amount, attempt, max_requotes, timeout_sec, poll_interval_sec, min_poll_interval_sec, progress, add_step, emit_before, monitor_order, cancel_or_restore)
        if result == "submit_error":
            return "submit_error", balance, state, terminal_reason
        balance = fetch_asset_balance(client, plan)
        state = target_state(plan, balance, target, tolerance)
        add_step(steps, {"name": "balance_after_order", "status": "ok", **state, "balance": balance}, progress)
        if target_reached(plan["action"], state):
            add_step(steps, {"name": "balance_verify", "status": "asset_target_reached", **state, "balance": balance}, progress)
            return "asset_target_reached", balance, state, terminal_reason
        if attempt < max_requotes:
            emit_before(progress, {"name": "requote", "status": "start", "attempt": attempt + 2, "attempt_total": max_requotes + 1, **state})
            add_step(steps, {"name": "requote", "status": "pending", "attempt": attempt + 2, **state}, progress)
    return None, balance, state, terminal_reason


def _next_order_amount(client: Any, plan: dict[str, Any], balance: dict[str, float], target: float) -> tuple[float, str | None]:
    try:
        amount = order_amount(client, plan, balance, target)
    except Exception as exc:  # noqa: BLE001
        return 0.0, order_amount_reason(exc)
    if amount <= 0 or amount < float(plan.get("min_executable_quantity") or 0):
        return 0.0, "remaining free quantity is below minimum executable quantity"
    return amount, None


def _submit_and_monitor(
    client: Any,
    plan: dict[str, Any],
    steps: list[dict[str, Any]],
    amount: float,
    attempt: int,
    max_requotes: int,
    timeout_sec: float,
    poll_interval_sec: float,
    min_poll_interval_sec: float,
    progress: Progress | None,
    add_step: Callable[[list[dict[str, Any]], dict[str, Any], Progress | None], None],
    emit_before: Callable[[Progress | None, dict[str, Any]], None],
    monitor_order: Callable[[Any, dict[str, Any], str, float, float, float], dict[str, Any]],
    cancel_or_restore: Callable[[Any, dict[str, Any], str, list[dict[str, Any]], Progress | None, Callable[[list[dict[str, Any]], dict[str, Any], Progress | None], None]], dict[str, Any]],
) -> str:
    emit_before(progress, {"name": "submit", "status": "start", "attempt": attempt + 1, "attempt_total": max_requotes + 1, "amount": amount})
    try:
        order = submit_spot_order(client, plan, amount)
    except Exception as exc:  # noqa: BLE001
        add_step(steps, {"name": "submit", "status": "error", "attempt": attempt + 1, "amount": amount, "error": redact_text(exc)}, progress)
        return "submit_error"
    add_step(steps, {"name": "submit", "status": "ok", "attempt": attempt + 1, "amount": amount, "price": order.get("price"), "order_id": order.get("id")}, progress)
    emit_before(progress, {"name": "order_monitor", "status": "start", "attempt": attempt + 1, "attempt_total": max_requotes + 1, "order_id": order.get("id")})
    status = monitor_order(client, order, plan["symbol"], timeout_sec, poll_interval_sec, min_poll_interval_sec)
    fee = order_fee_summary(plan, order, status, market="spot", amount=amount, price=order.get("price"), liquidity="maker")
    add_step(steps, {"name": "order_monitor", "status": status.get("status"), "order_id": order.get("id"), "fee": fee}, progress)
    if status.get("status") not in {"closed", "canceled", "rejected", "expired"}:
        emit_before(progress, {"name": "timeout_cancel", "status": "start", "attempt": attempt + 1, "attempt_total": max_requotes + 1, "order_id": order.get("id")})
        cancel_or_restore(client, order, plan["symbol"], steps, progress, add_step)
    return "ok"


def _verify_or_rescue(
    client: Any,
    plan: dict[str, Any],
    steps: list[dict[str, Any]],
    balance: dict[str, float],
    target: float,
    tolerance: float,
    terminal_reason: str | None,
    timeout_sec: float,
    poll_interval_sec: float,
    min_poll_interval_sec: float,
    progress: Progress | None,
    add_step: Callable[[list[dict[str, Any]], dict[str, Any], Progress | None], None],
    emit_before: Callable[[Progress | None, dict[str, Any]], None],
    monitor_order: Callable[[Any, dict[str, Any], str, float, float, float], dict[str, Any]],
) -> tuple[str, dict[str, Any] | None]:
    balance = fetch_asset_balance(client, plan)
    state = target_state(plan, balance, target, tolerance)
    if terminal_reason:
        state = {**state, "runtime_reason": terminal_reason}
    rescue_status, rescue_state = maybe_spot_target_rescue(
        client,
        plan,
        steps,
        balance=balance,
        target=target,
        tolerance=tolerance,
        timeout_sec=timeout_sec,
        poll_interval_sec=poll_interval_sec,
        min_poll_interval_sec=min_poll_interval_sec,
        progress=progress,
        add_step=add_step,
        emit_before=emit_before,
        monitor_order=monitor_order,
        fetch_asset_balance=fetch_asset_balance,
        order_amount=order_amount,
        target_state=target_state,
        target_reached=target_reached,
        counterparty_price=counterparty_price,
    )
    if rescue_status is not None:
        return rescue_status, rescue_state
    add_step(steps, {"name": "balance_verify", "status": "asset_target_not_reached", **state, "balance": balance}, progress)
    return "asset_target_not_reached", state


def target_balance(plan: dict[str, Any], balance: dict[str, float]) -> float:
    amount = plan["order"].get("amount")
    current = balance["total"]
    if plan["action"] == "buy":
        return current + float(amount or 0)
    if amount is None:
        return max(0.0, current - balance["free"])
    return max(0.0, current - min(balance["free"], float(amount)))


def target_state(plan: dict[str, Any], balance: dict[str, float], target: float, tolerance: float) -> dict[str, Any]:
    current = balance["total"]
    return {
        "asset": plan.get("target_asset") or plan.get("base_asset"),
        "current_quantity": current,
        "free_quantity": balance["free"],
        "used_quantity": balance["used"],
        "target_quantity": target,
        "remaining_quantity": abs(target - current),
        "tolerance_quantity": tolerance,
    }


def target_reached(action: str, state: dict[str, Any]) -> bool:
    current = float(state["current_quantity"])
    target = float(state["target_quantity"])
    tolerance = float(state["tolerance_quantity"])
    return current >= target - tolerance if action == "buy" else current <= target + tolerance


def order_amount(client: Any, plan: dict[str, Any], balance: dict[str, float], target: float) -> float:
    current = balance["total"]
    remaining = max(0.0, target - current) if plan["action"] == "buy" else min(balance["free"], max(0.0, current - target))
    return amount_to_precision(client, plan["symbol"], remaining)


def submit_spot_order(client: Any, plan: dict[str, Any], amount: float) -> dict[str, Any]:
    order = plan["order"]
    params = dict(order.get("params") or {})
    params.setdefault("newClientOrderId", f"aslab_{uuid.uuid4().hex[:24]}")
    price = maker_price(client, plan["symbol"], order["side"])
    return client.create_order(order["symbol"], order["type"], order["side"], amount, price, params)


def maker_price(client: Any, symbol: str, side: str) -> float:
    config = get_gateway_config()
    if config.spot_bbo_price_source != "order_book":
        raise ValueError(f"unsupported spot BBO price_source: {config.spot_bbo_price_source}")
    book = client.fetch_order_book(symbol)
    rows = book.get("bids") if side == "buy" else book.get("asks")
    if not rows:
        raise ValueError(f"order book has no {'bid' if side == 'buy' else 'ask'} price for {symbol}")
    price = float(rows[0][0])
    method = getattr(client, "price_to_precision", None)
    return float(method(symbol, price)) if callable(method) else price


def counterparty_price(client: Any, symbol: str, side: str) -> float:
    book = client.fetch_order_book(symbol)
    rows = book.get("bids") if side == "sell" else book.get("asks")
    if not rows:
        raise ValueError(f"order book has no {'bid' if side == 'sell' else 'ask'} price for {symbol}")
    price = float(rows[0][0])
    method = getattr(client, "price_to_precision", None)
    return float(method(symbol, price)) if callable(method) else price


def fetch_asset_balance(client: Any, plan: dict[str, Any]) -> dict[str, Any]:
    payload = client.fetch_balance() if hasattr(client, "fetch_balance") else {}
    asset = str(plan.get("base_asset") or plan.get("target_asset") or "").upper()
    raw_row = payload.get(asset)
    row = raw_row if isinstance(raw_row, dict) else {}
    free = balance_value(payload, row, "free", asset)
    used = balance_value(payload, row, "used", asset)
    total = balance_value(payload, row, "total", asset)
    if total == 0 and (free or used):
        total = free + used
    return {"asset": asset, "free": free, "used": used, "total": total}


def balance_value(payload: dict[str, Any], row: dict[str, Any], key: str, asset: str) -> float:
    value = row.get(key)
    raw_bucket = payload.get(key)
    if value is None and isinstance(raw_bucket, dict):
        value = raw_bucket.get(asset)
    try:
        return float(value or 0)
    except (TypeError, ValueError):
        return 0.0
