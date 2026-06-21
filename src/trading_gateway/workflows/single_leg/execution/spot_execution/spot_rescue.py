from __future__ import annotations

import uuid
from collections.abc import Callable
from typing import Any

from trading_gateway.adapters.exchanges.fees import order_fee_summary
from trading_gateway.adapters.exchanges.single_leg import adapter_for
from trading_gateway.app.config import get_gateway_config
from trading_gateway.support.redaction import redact_text

Progress = Callable[[dict[str, Any]], None]


def maybe_spot_target_rescue(
    client: Any,
    plan: dict[str, Any],
    steps: list[dict[str, Any]],
    *,
    balance: dict[str, float],
    target: float,
    tolerance: float,
    timeout_sec: float,
    poll_interval_sec: float,
    min_poll_interval_sec: float,
    progress: Progress | None,
    add_step: Callable[[list[dict[str, Any]], dict[str, Any], Progress | None], None],
    emit_before: Callable[[Progress | None, dict[str, Any]], None],
    monitor_order: Callable[[Any, dict[str, Any], str, float, float, float], dict[str, Any]],
    fetch_asset_balance: Callable[[Any, dict[str, Any]], dict[str, Any]],
    order_amount: Callable[[Any, dict[str, Any], dict[str, float], float], float],
    target_state: Callable[[dict[str, Any], dict[str, float], float, float], dict[str, Any]],
    target_reached: Callable[[str, dict[str, Any]], bool],
    counterparty_price: Callable[[Any, str, str], float],
) -> tuple[str | None, dict[str, Any] | None]:
    config = get_gateway_config().spot_execution
    state = target_state(plan, balance, target, tolerance)
    mode = str(config.sell_all_rescue_mode or "disabled").strip().lower()
    if not is_spot_rescue_eligible(plan):
        return None, None
    if mode != "bbo_counterparty_1":
        add_step(steps, {"name": "spot_rescue_guard", "status": "disabled", "mode": mode or "disabled", **state}, progress)
        return None, None
    guard = spot_rescue_guard(client, plan, balance, state, counterparty_price)
    add_step(steps, {"name": "spot_rescue_guard", **guard}, progress)
    if guard["status"] != "ok":
        blocked_state = {**state, **rescue_target_fields(guard)}
        add_step(steps, {"name": "spot_rescue_verify", "status": "asset_target_not_reached", **blocked_state, "balance": balance}, progress)
        return "asset_target_not_reached", blocked_state
    return _submit_and_verify_rescue(
        client,
        plan,
        steps,
        guard,
        balance,
        target,
        tolerance,
        timeout_sec,
        poll_interval_sec,
        min_poll_interval_sec,
        progress,
        add_step,
        emit_before,
        monitor_order,
        fetch_asset_balance,
        order_amount,
        target_state,
        target_reached,
    )


def _submit_and_verify_rescue(
    client: Any,
    plan: dict[str, Any],
    steps: list[dict[str, Any]],
    guard: dict[str, Any],
    balance: dict[str, float],
    target: float,
    tolerance: float,
    timeout_sec: float,
    poll_interval_sec: float,
    min_poll_interval_sec: float,
    progress: Progress | None,
    add_step: Callable[[list[dict[str, Any]], dict[str, Any], Progress | None], None],
    emit_before: Callable[[Progress | None, dict[str, Any]], None],
    monitor_order: Callable[[Any, dict[str, Any], str, float, float, float], dict[str, Any]],
    fetch_asset_balance: Callable[[Any, dict[str, Any]], dict[str, Any]],
    order_amount: Callable[[Any, dict[str, Any], dict[str, float], float], float],
    target_state: Callable[[dict[str, Any], dict[str, float], float, float], dict[str, Any]],
    target_reached: Callable[[str, dict[str, Any]], bool],
) -> tuple[str, dict[str, Any]]:
    state = target_state(plan, balance, target, tolerance)
    rescue = adapter_for(plan["exchange"], plan["market"]).spot_rescue_order()
    if rescue is None:
        blocked_state = {**state, "rescue_reason": "spot rescue not supported for this exchange"}
        add_step(steps, {"name": "spot_rescue_submit", "status": "blocked", "error": blocked_state["rescue_reason"], **blocked_state}, progress)
        add_step(steps, {"name": "spot_rescue_verify", "status": "asset_target_not_reached", **blocked_state, "balance": balance}, progress)
        return "asset_target_not_reached", blocked_state
    amount, blocked = _rescue_amount(client, plan, balance, target, guard, state, order_amount)
    if blocked is not None:
        add_step(steps, {"name": "spot_rescue_submit", "status": "blocked", "amount": amount, "error": blocked["rescue_reason"], **blocked}, progress)
        add_step(steps, {"name": "spot_rescue_verify", "status": "asset_target_not_reached", **blocked, "balance": balance}, progress)
        return "asset_target_not_reached", blocked
    price = float(guard["counterparty_price"])
    emit_before(progress, {"name": "spot_rescue_submit", "status": "start", "amount": amount, "price": price})
    try:
        order = submit_rescue_spot_order(client, plan, amount, price, rescue[0], rescue[1])
    except Exception as exc:  # noqa: BLE001
        error_state = {**state, **rescue_target_fields(guard), "rescue_reason": redact_text(exc)}
        add_step(steps, {"name": "spot_rescue_submit", "status": "error", "amount": amount, "price": price, "error": redact_text(exc), **error_state}, progress)
        return "submit_error", error_state
    return _verify_rescue_order(
        client,
        plan,
        steps,
        order,
        amount,
        guard,
        target,
        tolerance,
        timeout_sec,
        poll_interval_sec,
        min_poll_interval_sec,
        progress,
        add_step,
        emit_before,
        monitor_order,
        fetch_asset_balance,
        target_state,
        target_reached,
    )


def _rescue_amount(
    client: Any,
    plan: dict[str, Any],
    balance: dict[str, float],
    target: float,
    guard: dict[str, Any],
    state: dict[str, Any],
    order_amount: Callable[[Any, dict[str, Any], dict[str, float], float], float],
) -> tuple[float, dict[str, Any] | None]:
    try:
        amount = order_amount(client, plan, balance, target)
    except Exception as exc:  # noqa: BLE001
        return 0.0, {**state, **rescue_target_fields(guard), "rescue_reason": order_amount_reason(exc)}
    if amount <= 0 or amount < float(plan.get("min_executable_quantity") or 0):
        return amount, {**state, **rescue_target_fields(guard), "rescue_reason": "remaining free quantity rounded below minimum executable quantity"}
    return amount, None


def _verify_rescue_order(
    client: Any,
    plan: dict[str, Any],
    steps: list[dict[str, Any]],
    order: dict[str, Any],
    amount: float,
    guard: dict[str, Any],
    target: float,
    tolerance: float,
    timeout_sec: float,
    poll_interval_sec: float,
    min_poll_interval_sec: float,
    progress: Progress | None,
    add_step: Callable[[list[dict[str, Any]], dict[str, Any], Progress | None], None],
    emit_before: Callable[[Progress | None, dict[str, Any]], None],
    monitor_order: Callable[[Any, dict[str, Any], str, float, float, float], dict[str, Any]],
    fetch_asset_balance: Callable[[Any, dict[str, Any]], dict[str, Any]],
    target_state: Callable[[dict[str, Any], dict[str, float], float, float], dict[str, Any]],
    target_reached: Callable[[str, dict[str, Any]], bool],
) -> tuple[str, dict[str, Any]]:
    add_step(steps, {"name": "spot_rescue_submit", "status": "ok", "amount": amount, "price": order.get("price"), "order_id": order.get("id")}, progress)
    emit_before(progress, {"name": "spot_rescue_monitor", "status": "start", "order_id": order.get("id")})
    status = monitor_order(client, order, plan["symbol"], timeout_sec, poll_interval_sec, min_poll_interval_sec)
    fee = order_fee_summary(plan, order, status, market="spot", amount=amount, price=order.get("price"), liquidity="taker")
    add_step(steps, {"name": "spot_rescue_monitor", "status": status.get("status"), "order_id": order.get("id"), "fee": fee}, progress)
    balance_after = fetch_asset_balance(client, plan)
    state_after = target_state(plan, balance_after, target, tolerance)
    verify_status = "asset_target_reached" if target_reached(plan["action"], state_after) else "asset_target_not_reached"
    target_after = dict(state_after)
    if verify_status != "asset_target_reached":
        target_after.update(rescue_target_fields(guard))
        target_after["rescue_reason"] = spot_rescue_not_reached_reason(status)
    add_step(steps, {"name": "spot_rescue_verify", "status": verify_status, **target_after, "balance": balance_after}, progress)
    return verify_status, target_after


def spot_rescue_guard(
    client: Any,
    plan: dict[str, Any],
    balance: dict[str, float],
    state: dict[str, Any],
    counterparty_price: Callable[[Any, str, str], float],
) -> dict[str, Any]:
    config = get_gateway_config().spot_execution
    if plan["exchange"] == "mexc":
        return {"status": "blocked", "reason": "spot rescue not enabled for mexc in v1", **state}
    remaining_quantity = spot_rescue_remaining_quantity(plan, balance, state)
    min_qty = float(plan.get("min_executable_quantity") or 0)
    if remaining_quantity <= 0 or remaining_quantity < min_qty:
        return {"status": "blocked", "reason": "remaining free quantity is below minimum executable quantity", **state}
    price_side = "buy" if plan["action"] == "buy" else "sell"
    try:
        price = counterparty_price(client, plan["symbol"], price_side)
    except Exception as exc:  # noqa: BLE001
        return {"status": "blocked", "reason": f"counterparty price unavailable: {redact_text(exc)}", **state}
    return _price_guard(plan, remaining_quantity, price, state)


def _price_guard(plan: dict[str, Any], remaining_quantity: float, price: float, state: dict[str, Any]) -> dict[str, Any]:
    config = get_gateway_config().spot_execution
    remaining_quote = remaining_quantity * price
    price_fields = rescue_price_fields(plan["action"], price)
    if remaining_quote > config.sell_all_rescue_max_quote_usdt:
        return {"status": "blocked", "reason": f"remaining quote exceeds rescue cap {config.sell_all_rescue_max_quote_usdt}", "remaining_quote_usdt": remaining_quote, **price_fields, **state}
    last_price = float(plan.get("last_price") or 0)
    slippage_bps = abs(price - last_price) / last_price * 10000 if last_price > 0 else 0.0
    if slippage_bps > config.sell_all_rescue_max_slippage_bps:
        return {"status": "blocked", "reason": spot_rescue_slippage_reason(plan["action"], slippage_bps, config.sell_all_rescue_max_slippage_bps), "remaining_quote_usdt": remaining_quote, "slippage_bps": slippage_bps, **price_fields, **state}
    return {"status": "ok", "mode": config.sell_all_rescue_mode, "remaining_quote_usdt": remaining_quote, "slippage_bps": slippage_bps, **price_fields, **state}


def submit_rescue_spot_order(client: Any, plan: dict[str, Any], amount: float, price: float, order_type: str, rescue_params: dict[str, Any]) -> dict[str, Any]:
    params = dict(rescue_params)
    params.setdefault("newClientOrderId", f"aslab_{uuid.uuid4().hex[:24]}")
    order = plan["order"]
    return client.create_order(order["symbol"], order_type, order["side"], amount, price, params)


def is_spot_rescue_eligible(plan: dict[str, Any]) -> bool:
    if plan.get("market") != "spot":
        return False
    if plan.get("action") == "buy":
        return True
    return plan.get("action") == "sell" and str(plan.get("quantity")).upper() == "ALL"


def rescue_target_fields(row: dict[str, Any]) -> dict[str, Any]:
    result = {key: row[key] for key in ("counterparty_price", "best_bid", "best_ask", "remaining_quote_usdt", "slippage_bps") if key in row}
    if row.get("reason"):
        result["rescue_reason"] = row["reason"]
    return result


def spot_rescue_not_reached_reason(status: dict[str, Any]) -> str:
    monitor_status = str(status.get("status") or "unknown")
    if monitor_status == "closed":
        return "rescue order closed but remaining balance still stayed above tolerance"
    if monitor_status in {"canceled", "expired", "rejected"}:
        return f"rescue order ended as {monitor_status}"
    if monitor_status == "open":
        return "rescue order stayed open until timeout and did not finish the remaining balance"
    return f"rescue order ended with status {monitor_status}"


def order_amount_reason(exc: Exception) -> str:
    text = redact_text(exc)
    lowered = text.lower()
    if "minimum amount precision" in lowered or "must be greater than minimum amount" in lowered:
        return "remaining free quantity rounded below exchange amount precision"
    return text


def spot_rescue_remaining_quantity(plan: dict[str, Any], balance: dict[str, float], state: dict[str, Any]) -> float:
    current = float(balance["total"])
    target = float(state["target_quantity"])
    if plan["action"] == "buy":
        return max(0.0, target - current)
    return min(balance["free"], max(0.0, current - target))


def rescue_price_fields(action: str, price: float) -> dict[str, float]:
    result = {"counterparty_price": price}
    if action == "buy":
        result["best_ask"] = price
    else:
        result["best_bid"] = price
    return result


def spot_rescue_slippage_reason(action: str, slippage_bps: float, cap_bps: float) -> str:
    label = "best ask" if action == "buy" else "best bid"
    return f"{label} deviates by {slippage_bps:.4g} bps which exceeds rescue cap {cap_bps}"
