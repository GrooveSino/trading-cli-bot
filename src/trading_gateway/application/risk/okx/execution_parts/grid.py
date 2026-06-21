from __future__ import annotations

from typing import Any

from trading_gateway.support.redaction import redact_mapping

from .managed import _verify_leverage


def place_okx_grid_short_orders(client: Any, plan: dict[str, Any], *, replace_existing: bool = False) -> dict[str, Any]:
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
    results["set_leverage"] = client.privatePostAccountSetLeverage(
        {"instId": symbol, "lever": str(plan["leverage"]), "mgnMode": str(plan["margin_mode"])}
    )
    order_results = []
    for order in plan.get("orders") or []:
        order_results.append({"label": order.get("label"), "result": client.privatePostTradeOrder(order["payload"])})
    results["orders"] = order_results
    results["verification"] = {
        "open_orders": client.privateGetTradeOrdersPending({"instType": "SWAP", "instId": symbol}),
        "oco": client.privateGetTradeOrdersAlgoPending({"ordType": "oco", "instId": symbol}),
        "conditional": client.privateGetTradeOrdersAlgoPending({"ordType": "conditional", "instId": symbol}),
        "trigger": client.privateGetTradeOrdersAlgoPending({"ordType": "trigger", "instId": symbol}),
        "positions": client.privateGetAccountPositions({"instType": "SWAP", "instId": symbol}),
    }
    return redact_mapping(results)

