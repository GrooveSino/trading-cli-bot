from __future__ import annotations

from collections.abc import Callable
from typing import Any

from trading_gateway.adapters.exchanges.fees import order_fee_summary
from trading_gateway.adapters.exchanges.positions import (
    position_quantity,
    remaining_quantity,
    target_quantity,
    target_reached,
    target_side,
    target_state,
)
from trading_gateway.adapters.exchanges.rules import amount_to_precision
from trading_gateway.app.config import get_gateway_config
from trading_gateway.support.tolerance import quantity_tolerance_from_quote
from trading_gateway.workflows.single_leg.execution.perp_context import apply_position_params, fetch_positions
from trading_gateway.workflows.single_leg.execution.perp_orders import (
    monitor_order,
    order_amount,
    order_liquidity,
    report_execution,
    submit_error,
    submit_order,
)
from trading_gateway.workflows.single_leg.recovery.runtime import cancel_or_restore

Progress = Callable[[dict[str, Any]], None]


def run_perp_target(
    client: Any,
    plan: dict[str, Any],
    runtime: dict[str, Any],
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
) -> dict[str, Any]:
    side = target_side(plan["action"])
    state, target = build_target_state(client, plan, runtime, side, target_tolerance_steps)
    add_step(steps, {"name": "position_before", "status": "ok", "side": side, **state, "positions": fetch_positions(client, plan["symbol"])}, progress)
    if target_reached(plan["action"], float(state["current_quantity"]), target, float(state["tolerance_quantity"])):
        add_step(steps, {"name": "position_verify", "status": "target_reached", "side": side, **state, "positions": fetch_positions(client, plan["symbol"])}, progress)
        return report_execution(plan, steps, "target_reached", state)
    return execute_requote_loop(
        client,
        plan,
        runtime,
        steps,
        side=side,
        target=target,
        timeout_sec=timeout_sec,
        max_requotes=max_requotes,
        poll_interval_sec=poll_interval_sec,
        min_poll_interval_sec=min_poll_interval_sec,
        progress=progress,
        add_step=add_step,
        emit_before=emit_before,
    )


def run_perp_resting_limit(
    client: Any,
    plan: dict[str, Any],
    runtime: dict[str, Any],
    steps: list[dict[str, Any]],
    *,
    poll_interval_sec: float,
    min_poll_interval_sec: float,
    target_tolerance_steps: int,
    progress: Progress | None,
    add_step: Callable[[list[dict[str, Any]], dict[str, Any], Progress | None], None],
    emit_before: Callable[[Progress | None, dict[str, Any]], None],
) -> dict[str, Any]:
    side = target_side(plan["action"])
    state, target = build_target_state(client, plan, runtime, side, target_tolerance_steps)
    add_step(steps, {"name": "position_before", "status": "ok", "side": side, **state, "positions": fetch_positions(client, plan["symbol"])}, progress)
    if target_reached(plan["action"], float(state["current_quantity"]), target, float(state["tolerance_quantity"])):
        add_step(steps, {"name": "position_verify", "status": "target_reached", "side": side, **state, "positions": fetch_positions(client, plan["symbol"])}, progress)
        return report_execution(plan, steps, "target_reached", state)
    amount = amount_to_precision(client, plan["symbol"], order_amount(plan, remaining_quantity(plan["action"], float(state["current_quantity"]), target)))
    if amount <= 0:
        return report_execution(plan, steps, "target_not_reached", state)
    emit_before(progress, {"name": "submit", "status": "start", "attempt": 1, "attempt_total": 1, "amount": amount})
    try:
        order = submit_order(client, plan, amount, runtime["params"])
    except Exception as exc:  # noqa: BLE001
        add_step(steps, {"name": "submit", "status": "error", "attempt": 1, "error": submit_error(exc)}, progress)
        return report_execution(plan, steps, "submit_error", state)
    add_step(steps, {"name": "submit", "status": "ok", "attempt": 1, "amount": amount, "price": order.get("price"), "order_id": order.get("id")}, progress)
    emit_before(progress, {"name": "resting_limit_verify", "status": "start", "order_id": order.get("id")})
    status = monitor_order(client, order, plan["symbol"], max(0.25, min_poll_interval_sec), poll_interval_sec, min_poll_interval_sec)
    add_step(steps, {"name": "resting_limit_verify", "status": status.get("status") or "unknown", "order_id": order.get("id")}, progress)
    if status.get("status") == "closed":
        positions = fetch_positions(client, plan["symbol"])
        current = position_quantity(positions, plan["symbol"], side, bool(runtime.get("position_mode", {}).get("hedge")))
        final_state = target_state(current, target, float(state["tolerance_quantity"]))
        add_step(steps, {"name": "position_after_order", "status": "ok", "side": side, **final_state, "positions": positions}, progress)
        return report_execution(plan, steps, "target_reached" if target_reached(plan["action"], current, target, float(state["tolerance_quantity"])) else "target_not_reached", final_state)
    return report_execution(plan, steps, "resting_limit_submitted", {**state, "order_id": order.get("id"), "order_status": status.get("status") or "unknown"})


def build_target_state(client: Any, plan: dict[str, Any], runtime: dict[str, Any], side: str, target_tolerance_steps: int) -> tuple[dict[str, Any], float]:
    step = float(plan.get("quantity_step") or 0)
    fallback_tolerance = step * max(0, target_tolerance_steps)
    tolerance = quantity_tolerance_from_quote(
        float(plan.get("last_price") or 0),
        get_gateway_config().perp_execution.target_tolerance_quote_usdt,
        fallback_tolerance,
    )
    positions = fetch_positions(client, plan["symbol"])
    current = position_quantity(positions, plan["symbol"], side, bool(runtime.get("position_mode", {}).get("hedge")))
    target = target_quantity(plan, current)
    state = target_state(current, target, tolerance)
    apply_position_params(runtime["params"], plan, positions, side)
    return state, target


def execute_requote_loop(
    client: Any,
    plan: dict[str, Any],
    runtime: dict[str, Any],
    steps: list[dict[str, Any]],
    *,
    side: str,
    target: float,
    timeout_sec: float,
    max_requotes: int,
    poll_interval_sec: float,
    min_poll_interval_sec: float,
    progress: Progress | None,
    add_step: Callable[[list[dict[str, Any]], dict[str, Any], Progress | None], None],
    emit_before: Callable[[Progress | None, dict[str, Any]], None],
) -> dict[str, Any]:
    tolerance = build_target_state(client, plan, runtime, side, 0)[0]["tolerance_quantity"]
    positions = fetch_positions(client, plan["symbol"])
    current = position_quantity(positions, plan["symbol"], side, bool(runtime.get("position_mode", {}).get("hedge")))
    state = target_state(current, target, float(tolerance))
    for attempt in range(max_requotes + 1):
        amount = amount_to_precision(client, plan["symbol"], order_amount(plan, remaining_quantity(plan["action"], current, target)))
        if amount <= 0:
            break
        emit_before(progress, {"name": "submit", "status": "start", "attempt": attempt + 1, "attempt_total": max_requotes + 1, "amount": amount})
        try:
            order = submit_order(client, plan, amount, runtime["params"])
        except Exception as exc:  # noqa: BLE001
            add_step(steps, {"name": "submit", "status": "error", "attempt": attempt + 1, "error": submit_error(exc)}, progress)
            return report_execution(plan, steps, "submit_error", state)
        add_step(steps, {"name": "submit", "status": "ok", "attempt": attempt + 1, "amount": amount, "price": order.get("price"), "order_id": order.get("id")}, progress)
        emit_before(progress, {"name": "order_monitor", "status": "start", "attempt": attempt + 1, "attempt_total": max_requotes + 1, "order_id": order.get("id")})
        status = monitor_order(client, order, plan["symbol"], timeout_sec, poll_interval_sec, min_poll_interval_sec)
        fee = order_fee_summary(plan, order, status, amount=amount, price=order.get("price"), liquidity=order_liquidity(order))
        add_step(steps, {"name": "order_monitor", "status": status.get("status"), "order_id": order.get("id"), "fee": fee}, progress)
        if status.get("status") not in {"closed", "canceled", "rejected", "expired"}:
            emit_before(progress, {"name": "timeout_cancel", "status": "start", "attempt": attempt + 1, "attempt_total": max_requotes + 1, "order_id": order.get("id")})
            cancel_or_restore(client, order, plan["symbol"], steps, progress, add_step)
        positions = fetch_positions(client, plan["symbol"])
        current = position_quantity(positions, plan["symbol"], side, bool(runtime.get("position_mode", {}).get("hedge")))
        state = target_state(current, target, float(tolerance))
        add_step(steps, {"name": "position_after_order", "status": "ok", "side": side, **state, "positions": positions}, progress)
        if target_reached(plan["action"], current, target, float(tolerance)):
            add_step(steps, {"name": "position_verify", "status": "target_reached", "side": side, **state, "positions": positions}, progress)
            return report_execution(plan, steps, "target_reached", state)
        if attempt < max_requotes:
            emit_before(progress, {"name": "requote", "status": "start", "attempt": attempt + 2, "attempt_total": max_requotes + 1, **state})
            add_step(steps, {"name": "requote", "status": "pending", "attempt": attempt + 2, **state}, progress)
    return finalize_unreached(client, plan, runtime, steps, side, target, progress, add_step)


def finalize_unreached(
    client: Any,
    plan: dict[str, Any],
    runtime: dict[str, Any],
    steps: list[dict[str, Any]],
    side: str,
    target: float,
    progress: Progress | None,
    add_step: Callable[[list[dict[str, Any]], dict[str, Any], Progress | None], None],
) -> dict[str, Any]:
    positions = fetch_positions(client, plan["symbol"])
    current = position_quantity(positions, plan["symbol"], side, bool(runtime.get("position_mode", {}).get("hedge")))
    tolerance = build_target_state(client, plan, runtime, side, 0)[0]["tolerance_quantity"]
    state = target_state(current, target, float(tolerance))
    add_step(steps, {"name": "position_verify", "status": "target_not_reached", "side": side, **state, "positions": positions}, progress)
    return report_execution(plan, steps, "target_not_reached", state)
