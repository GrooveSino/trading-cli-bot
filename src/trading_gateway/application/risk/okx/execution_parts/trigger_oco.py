from __future__ import annotations

import time
from pathlib import Path
from typing import Any

from trading_gateway.support.redaction import redact_mapping

from .managed import _cancel_algo_ids, _history_rows, _okx_response_ok, _pending_algo_rows, _recompile_with_live_specs, _result_algo_id, _verify_leverage, _verify_parent_trigger_live
from .orders import fetch_okx_recent_history
from .sessions import ManagedEventLog
from .managed_parts.order import _place_managed_static_notional_order


def place_okx_trigger_oco_orders(
    client: Any,
    plan: dict[str, Any],
    *,
    replace_existing: bool = False,
    managed_poll_interval_sec: float = 2.0,
    managed_timeout_sec: float = 9 * 60 * 60,
    managed_session_id: str | None = None,
    managed_log_file: str | Path | None = None,
    managed_state_file: str | Path | None = None,
) -> dict[str, Any]:
    event_log = ManagedEventLog(managed_session_id, Path(managed_log_file) if managed_log_file else None, Path(managed_state_file) if managed_state_file else None)
    event_log.emit("starting", command="trigger-oco", replace_existing=replace_existing)
    plan = _recompile_with_live_specs(client, plan)
    symbol = str(plan["symbol"])
    results: dict[str, Any] = {"status": "submitted", "exchange": "okx", "symbol": symbol, "replace_existing": replace_existing}
    if replace_existing:
        pending = client.privateGetTradeOrdersPending({"instType": "SWAP", "instId": symbol})
        cancel_results = []
        for row in pending.get("data") or []:
            ord_id = str(row.get("ordId") or "").strip()
            if ord_id:
                cancel_results.append(client.privatePostTradeCancelOrder({"instId": symbol, "ordId": ord_id}))
        for kind in ["trigger", "conditional", "oco"]:
            for row in _pending_algo_rows(client, symbol, kind):
                algo_id = str(row.get("algoId") or "").strip()
                if algo_id:
                    cancel_results.extend(_cancel_algo_ids(client, symbol, [algo_id]))
        results["canceled_existing"] = cancel_results
    results["compiled_plan"] = plan
    results["leverage"] = _verify_leverage(client, symbol, str(plan["leverage"]), str(plan["margin_mode"]))
    event_log.emit("leverage_verified", symbol=symbol, leverage=plan["leverage"], margin_mode=plan["margin_mode"], result=results["leverage"])

    submitted = []
    for order in plan.get("orders") or []:
        result = client.privatePostTradeOrderAlgo(order["payload"])
        parent_algo_id = _result_algo_id(result) if _okx_response_ok(result) else ""
        if parent_algo_id and _verify_parent_trigger_live(client, symbol, parent_algo_id):
            event_log.emit("parent_live", label=order.get("label"), parent_algo_id=parent_algo_id, payload=order.get("payload"))
        submitted.append(
            {
                "label": order.get("label"),
                "endpoint": order.get("endpoint"),
                "result": result,
                "managed_parent_algo_id": parent_algo_id,
            }
        )
    results["submitted_parents"] = submitted

    active_orders = {row["managed_parent_algo_id"]: order for row, order in zip(submitted, plan.get("orders") or []) if row.get("managed_parent_algo_id")}
    non_live_ids = [algo_id for algo_id in active_orders if not _verify_parent_trigger_live(client, symbol, algo_id)]
    if non_live_ids:
        results["status"] = "parent_not_live"
        results["canceled_after_submit_failure"] = _cancel_algo_ids(client, symbol, list(active_orders))
        event_log.emit("parent_not_live", algo_ids=non_live_ids, canceled=results["canceled_after_submit_failure"])
        return redact_mapping(results)
    event_log.emit("monitoring", active_parent_algo_ids=list(active_orders), timeout_sec=managed_timeout_sec)
    deadline = time.monotonic() + managed_timeout_sec
    final_order = None
    while active_orders and time.monotonic() <= deadline:
        for algo_id, order in list(active_orders.items()):
            pending_ids = {str(row.get("algoId") or "").strip() for row in _pending_algo_rows(client, symbol, "trigger")}
            if algo_id in pending_ids:
                continue
            history = fetch_okx_recent_history(client, symbol, limit=100, kind="trigger", state="all", algo_id=algo_id)
            if _history_rows(history, "trigger", "order_failed"):
                submitted.append({"label": order.get("label"), "managed_parent_algo_id": algo_id, "managed_parent_state": {"state": "order_failed", "history": history}})
                event_log.emit("parent_terminal", label=order.get("label"), parent_algo_id=algo_id, state="order_failed")
                active_orders.pop(algo_id, None)
                continue
            if _history_rows(history, "trigger", "canceled"):
                submitted.append({"label": order.get("label"), "managed_parent_algo_id": algo_id, "managed_parent_state": {"state": "canceled", "history": history}})
                event_log.emit("parent_terminal", label=order.get("label"), parent_algo_id=algo_id, state="canceled")
                active_orders.pop(algo_id, None)
                continue
            if _history_rows(history, "trigger", "effective"):
                sibling_ids = [other_id for other_id in active_orders if other_id != algo_id]
                cancel_siblings = _cancel_algo_ids(client, symbol, sibling_ids)
                event_log.emit("parent_effective", label=order.get("label"), parent_algo_id=algo_id)
                event_log.emit("sibling_canceled", winner_algo_id=algo_id, sibling_algo_ids=sibling_ids, result=cancel_siblings)
                final_order = _place_managed_static_notional_order(
                    client,
                    symbol,
                    plan,
                    order,
                    {"code": "0", "data": [{"algoId": algo_id, "sCode": "0"}]},
                    managed_poll_interval_sec=managed_poll_interval_sec,
                    managed_timeout_sec=managed_timeout_sec,
                    event_log=event_log,
                )
                final_order["canceled_siblings"] = cancel_siblings
                results["winning_order"] = final_order
                active_orders.clear()
                break
        if not active_orders or final_order:
            break
        time.sleep(managed_poll_interval_sec)
    if active_orders and not final_order:
        results["status"] = "timeout"
        results["remaining_parent_algo_ids"] = list(active_orders)
        results["timeout_cancel"] = _cancel_algo_ids(client, symbol, list(active_orders))
        event_log.emit("timeout", remaining_parent_algo_ids=list(active_orders), timeout_cancel=results["timeout_cancel"])
    results["verification"] = {
        "open_orders": client.privateGetTradeOrdersPending({"instType": "SWAP", "instId": symbol}),
        "oco": client.privateGetTradeOrdersAlgoPending({"ordType": "oco", "instId": symbol}),
        "conditional": client.privateGetTradeOrdersAlgoPending({"ordType": "conditional", "instId": symbol}),
        "trigger": client.privateGetTradeOrdersAlgoPending({"ordType": "trigger", "instId": symbol}),
        "positions": client.privateGetAccountPositions({"instType": "SWAP", "instId": symbol}),
    }
    return redact_mapping(results)

