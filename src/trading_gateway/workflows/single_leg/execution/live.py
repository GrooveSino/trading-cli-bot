from __future__ import annotations

from collections.abc import Callable
from typing import Any

from trading_gateway.adapters.exchanges.leverage import ensure_perp_leverage
from trading_gateway.app.config import get_gateway_config
from trading_gateway.workflows.single_leg.execution.perp_context import (
    build_runtime_order_context,
    is_perp_close_all,
)
from trading_gateway.workflows.single_leg.execution.perp_orders import monitor_order, report_execution
from trading_gateway.workflows.single_leg.execution.perp_close_all_runtime import run_perp_close_all
from trading_gateway.workflows.single_leg.execution.perp_target import run_perp_resting_limit, run_perp_target
from trading_gateway.workflows.single_leg.execution.spot_execution import run_spot_target

Progress = Callable[[dict[str, Any]], None]


def run_live_execution(
    client: Any,
    plan: dict[str, Any],
    *,
    confirm: str,
    timeout_sec: float | None = None,
    max_requotes: int | None = None,
    poll_interval_sec: float | None = None,
    min_poll_interval_sec: float | None = None,
    target_tolerance_steps: int | None = None,
    progress: Progress | None = None,
) -> dict[str, Any]:
    config = get_gateway_config()
    market_defaults = execution_defaults(config, plan["market"])
    timeout = market_defaults["timeout"] if timeout_sec is None else timeout_sec
    requotes = int(market_defaults["requotes"] if max_requotes is None else max_requotes)
    close_all_maker_attempts = int(config.perp_close_all_maker_attempts if max_requotes is None else max_requotes + 1)
    poll = market_defaults["poll"] if poll_interval_sec is None else poll_interval_sec
    min_poll = market_defaults["min_poll"] if min_poll_interval_sec is None else min_poll_interval_sec
    tolerance_steps = int(market_defaults["tolerance_steps"] if target_tolerance_steps is None else target_tolerance_steps)
    steps: list[dict[str, Any]] = []
    add_step(steps, {"name": "plan", "status": "ok", "can_execute": plan["can_execute"]}, progress)
    if not plan["can_execute"]:
        return report_execution(plan, steps, "blocked")
    if config.require_live_confirm and str(confirm or "").strip() != plan["confirm_phrase"]:
        add_step(steps, {"name": "live_confirm", "status": "blocked", "expected_confirm_phrase": plan["confirm_phrase"]}, progress)
        return report_execution(plan, steps, "blocked")
    runtime = build_runtime_order_context(client, plan)
    if runtime.get("position_mode"):
        add_step(steps, {"name": "position_mode", "status": "ok", **runtime["position_mode"]}, progress)
    if plan["market"] == "perp":
        leverage = perp_leverage_step(client, plan, int(plan.get("requested_leverage") or config.perp_execution.target_leverage))
        add_step(steps, {"name": "perp_leverage", **leverage}, progress)
        if leverage["status"] not in {"ok", "skipped"}:
            return report_execution(plan, steps, "blocked")
        if is_perp_close_all(plan):
            return run_perp_close_all(
                client,
                plan,
                runtime,
                steps,
                maker_attempts=close_all_maker_attempts,
                order_timeout_sec=timeout,
                poll_interval_sec=poll,
                min_poll_interval_sec=min_poll,
                progress=progress,
                add_step=add_step,
                emit_before=emit_before,
            )
        if is_resting_limit_plan(plan):
            return run_perp_resting_limit(
                client,
                plan,
                runtime,
                steps,
                poll_interval_sec=poll,
                min_poll_interval_sec=min_poll,
                target_tolerance_steps=tolerance_steps,
                progress=progress,
                add_step=add_step,
                emit_before=emit_before,
            )
        return run_perp_target(
            client,
            plan,
            runtime,
            steps,
            timeout_sec=timeout,
            max_requotes=requotes,
            poll_interval_sec=poll,
            min_poll_interval_sec=min_poll,
            target_tolerance_steps=tolerance_steps,
            progress=progress,
            add_step=add_step,
            emit_before=emit_before,
        )
    status, target = run_spot_target(
        client,
        plan,
        steps,
        timeout_sec=timeout,
        max_requotes=requotes,
        poll_interval_sec=poll,
        min_poll_interval_sec=min_poll,
        target_tolerance_steps=tolerance_steps,
        emit_before=emit_before,
        progress=progress,
        add_step=add_step,
        monitor_order=_spot_monitor_order,
        cancel_or_restore=_cancel_or_restore,
    )
    return report_execution(plan, steps, status, target)


def perp_leverage_step(client: Any, plan: dict[str, Any], leverage: int) -> dict[str, Any]:
    if str(plan.get("action") or "").startswith("close-"):
        return {"status": "skipped", "target_leverage": leverage, "reason": "not required for close action"}
    return ensure_perp_leverage(client, plan["symbol"], leverage)


def execution_defaults(config: Any, market: str) -> dict[str, float | int]:
    if market == "spot":
        return {
            "timeout": config.spot_order_timeout_sec,
            "requotes": config.spot_max_requotes,
            "poll": config.spot_poll_interval_sec,
            "min_poll": config.spot_min_poll_interval_sec,
            "tolerance_steps": config.spot_target_tolerance_steps,
        }
    return {
        "timeout": config.perp_order_timeout_sec,
        "requotes": config.perp_max_requotes,
        "poll": config.perp_poll_interval_sec,
        "min_poll": config.perp_min_poll_interval_sec,
        "tolerance_steps": config.perp_target_tolerance_steps,
    }


def is_resting_limit_plan(plan: dict[str, Any]) -> bool:
    return plan.get("market") == "perp" and plan.get("limit_price") is not None


def add_step(steps: list[dict[str, Any]], row: dict[str, Any], progress: Progress | None) -> None:
    steps.append(row)
    if progress:
        progress(row)


def emit_before(progress: Progress | None, row: dict[str, Any]) -> None:
    if progress:
        progress({"phase": "before", **row})


def _spot_monitor_order(client: Any, order: dict[str, Any], symbol: str, timeout_sec: float, poll_interval_sec: float, min_poll_interval_sec: float) -> dict[str, Any]:
    return monitor_order(client, order, symbol, timeout_sec, poll_interval_sec, min_poll_interval_sec)


def _cancel_or_restore(
    client: Any,
    order: dict[str, Any],
    symbol: str,
    steps: list[dict[str, Any]],
    progress: Progress | None,
    add_step_fn: Callable[[list[dict[str, Any]], dict[str, Any], Progress | None], None],
) -> dict[str, Any]:
    from trading_gateway.workflows.single_leg.recovery.runtime import cancel_or_restore

    return cancel_or_restore(client, order, symbol, steps, progress, add_step_fn)
