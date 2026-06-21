from __future__ import annotations

import time
from decimal import Decimal
from typing import Any

from ..managed import (
    _cancel_exits_after_flat,
    _emergency_reduce_only_close,
    _managed_exit_payloads,
    _okx_response_ok,
    _result_algo_id,
    _verify_exit_algos,
    _verify_parent_trigger_live,
    _wait_for_managed_parent,
)
from ..sessions import ManagedEventLog


def _place_managed_static_notional_order(
    client: Any,
    symbol: str,
    plan: dict[str, Any],
    order: dict[str, Any],
    result: dict[str, Any],
    *,
    managed_poll_interval_sec: float,
    managed_timeout_sec: float,
    event_log: ManagedEventLog | None = None,
) -> dict[str, Any]:
    row_result: dict[str, Any] = {"label": order.get("label"), "endpoint": order.get("endpoint"), "result": result}
    if not _okx_response_ok(result):
        return row_result

    parent_algo_id = _result_algo_id(result)
    row_result["managed_parent_algo_id"] = parent_algo_id
    if not _verify_parent_trigger_live(client, symbol, parent_algo_id):
        row_result["managed_parent_state"] = {"state": "not_live_after_submit"}
        if event_log:
            event_log.emit("parent_not_live", label=order.get("label"), parent_algo_id=parent_algo_id)
        return row_result
    if event_log:
        event_log.emit("parent_live", label=order.get("label"), parent_algo_id=parent_algo_id, payload=order.get("payload"))
        event_log.emit("monitoring", label=order.get("label"), timeout_sec=managed_timeout_sec)
    wait = _wait_for_managed_parent(
        client,
        symbol,
        parent_algo_id,
        str(order.get("side")),
        poll_interval_sec=managed_poll_interval_sec,
        timeout_sec=managed_timeout_sec,
    )
    row_result["managed_parent_state"] = wait
    if wait.get("state") != "filled":
        if event_log:
            event_log.emit("parent_terminal", label=order.get("label"), parent_algo_id=parent_algo_id, state=wait.get("state"))
        return row_result

    filled_size = Decimal(str(wait["filled_size"]))
    if event_log:
        event_log.emit("fill_delta", label=order.get("label"), parent_algo_id=parent_algo_id, filled_size=str(filled_size))
    exit_attempts = []
    exit_ids: list[str] = []
    for attempt in range(1, 4):
        exit_attempt = {"attempt": attempt, "orders": []}
        for exit_order in _managed_exit_payloads(symbol, str(plan["margin_mode"]), order["exit_plan"], filled_size):
            exit_result = client.privatePostTradeOrderAlgo(exit_order["payload"])
            exit_attempt["orders"].append({"kind": exit_order["kind"], "result": exit_result})
            if _okx_response_ok(exit_result):
                algo_id = _result_algo_id(exit_result)
                if algo_id:
                    exit_ids.append(algo_id)
        exit_attempts.append(exit_attempt)
        if _verify_exit_algos(client, symbol, exit_ids):
            row_result["managed_exits"] = {"status": "live", "algo_ids": exit_ids, "attempts": exit_attempts}
            if event_log:
                event_log.emit("exits_live", label=order.get("label"), algo_ids=exit_ids)
            row_result["managed_cleanup"] = _cancel_exits_after_flat(
                client,
                symbol,
                str(order.get("side")),
                exit_ids,
                poll_interval_sec=managed_poll_interval_sec,
                timeout_sec=managed_timeout_sec,
            )
            return row_result
        time.sleep(min(managed_poll_interval_sec, 5.0))

    emergency = _emergency_reduce_only_close(client, symbol, str(plan["margin_mode"]), str(order.get("side")), filled_size)
    if event_log:
        event_log.emit("emergency_close", label=order.get("label"), filled_size=str(filled_size), result=emergency)
    row_result["managed_exits"] = {
        "status": "emergency_closed",
        "algo_ids": exit_ids,
        "attempts": exit_attempts,
        "emergency_close": emergency,
    }
    return row_result
