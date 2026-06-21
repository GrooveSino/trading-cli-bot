from __future__ import annotations

from pathlib import Path
from typing import Any

from trading_gateway.support.redaction import redact_mapping

from .managed import _okx_response_ok, _recompile_with_live_specs, _verify_leverage
from .sessions import ManagedEventLog
from .managed_parts.order import _place_managed_static_notional_order


def place_okx_static_notional_orders(
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
    event_log.emit("starting", command="static-notional", replace_existing=replace_existing)
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
        results["canceled_existing"] = cancel_results
    results["compiled_plan"] = plan
    results["leverage"] = _verify_leverage(client, symbol, str(plan["leverage"]), str(plan["margin_mode"]))
    event_log.emit("leverage_verified", symbol=symbol, leverage=plan["leverage"], margin_mode=plan["margin_mode"], result=results["leverage"])
    order_results = []
    for order in plan.get("orders") or []:
        if order.get("endpoint") in {"order_algo", "managed_order_algo"}:
            result = client.privatePostTradeOrderAlgo(order["payload"])
        else:
            result = client.privatePostTradeOrder(order["payload"])
        if order.get("endpoint") == "managed_order_algo":
            order_results.append(
                _place_managed_static_notional_order(
                    client,
                    symbol,
                    plan,
                    order,
                    result,
                    managed_poll_interval_sec=managed_poll_interval_sec,
                    managed_timeout_sec=managed_timeout_sec,
                    event_log=event_log,
                )
            )
        else:
            order_results.append({"label": order.get("label"), "endpoint": order.get("endpoint"), "result": result})
    results["orders"] = order_results
    results["verification"] = {
        "open_orders": client.privateGetTradeOrdersPending({"instType": "SWAP", "instId": symbol}),
        "oco": client.privateGetTradeOrdersAlgoPending({"ordType": "oco", "instId": symbol}),
        "conditional": client.privateGetTradeOrdersAlgoPending({"ordType": "conditional", "instId": symbol}),
        "trigger": client.privateGetTradeOrdersAlgoPending({"ordType": "trigger", "instId": symbol}),
        "positions": client.privateGetAccountPositions({"instType": "SWAP", "instId": symbol}),
    }
    return redact_mapping(results)
