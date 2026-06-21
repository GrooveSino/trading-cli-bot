from __future__ import annotations

from typing import Any

from trading_gateway.support.redaction import redact_mapping

from ..common import _okx_swap_inst_id


def place_okx_bracket_orders(client: Any, plan: dict[str, Any]) -> dict[str, Any]:
    results = []
    for order in plan.get("algo_orders") or []:
        results.append({"kind": order.get("kind"), "result": client.privatePostTradeOrderAlgo(order["payload"])})
    return redact_mapping({"status": "live", "exchange": plan.get("exchange"), "symbol": plan.get("symbol"), "orders": results})


def fetch_okx_algo_orders(client: Any, symbol: str | None = None, ord_type: str = "conditional") -> dict[str, Any]:
    requested = str(ord_type or "conditional").strip().lower()
    if requested not in {"conditional", "oco", "trigger", "all"}:
        raise ValueError("ord_type must be conditional, oco, trigger, or all")
    inst_id = _okx_swap_inst_id(symbol) if symbol else None
    kinds = ["conditional", "oco", "trigger"] if requested == "all" else [requested]
    result: dict[str, Any] = {}
    for kind in kinds:
        params = {"ordType": kind}
        if inst_id:
            params["instId"] = inst_id
        result[kind] = client.privateGetTradeOrdersAlgoPending(params)
    return redact_mapping({"exchange": "okx", "symbol": inst_id, "algo_orders": result})


def _filter_okx_rows(rows: list[dict[str, Any]], *, algo_id: str | None = None, order_id: str | None = None) -> list[dict[str, Any]]:
    wanted_algo = str(algo_id or "").strip()
    wanted_order = str(order_id or "").strip()
    if not wanted_algo and not wanted_order:
        return rows

    filtered = []
    for row in rows:
        row_algo_ids = {
            str(row.get("algoId") or "").strip(),
            str((row.get("linkedAlgoOrd") or {}).get("algoId") or "").strip(),
        }
        for child in row.get("attachAlgoOrds") or []:
            row_algo_ids.add(str(child.get("attachAlgoId") or "").strip())
            row_algo_ids.add(str(child.get("algoId") or "").strip())

        row_order_ids = {
            str(row.get("ordId") or "").strip(),
            str(row.get("ordIdList") or "").strip(),
        }
        if (wanted_algo and wanted_algo in row_algo_ids) or (wanted_order and wanted_order in row_order_ids):
            filtered.append(row)
    return filtered


def fetch_okx_recent_history(
    client: Any,
    symbol: str | None = None,
    limit: int = 20,
    *,
    kind: str = "all",
    state: str = "all",
    algo_id: str | None = None,
    order_id: str | None = None,
) -> dict[str, Any]:
    inst_id = _okx_swap_inst_id(symbol) if symbol else None
    safe_limit = str(max(1, min(int(limit), 100)))
    base_params = {"instType": "SWAP", "limit": safe_limit}
    if inst_id:
        base_params["instId"] = inst_id

    requested_kind = str(kind or "all").strip().lower()
    if requested_kind not in {"all", "trigger", "conditional", "oco"}:
        raise ValueError("kind must be trigger, conditional, oco, or all")
    requested_state = str(state or "all").strip().lower()
    if requested_state not in {"all", "effective", "canceled", "order_failed"}:
        raise ValueError("state must be effective, canceled, order_failed, or all")

    algo_history: dict[str, Any] = {}
    kinds = ["trigger", "conditional", "oco"] if requested_kind == "all" else [requested_kind]
    states = ["effective", "canceled", "order_failed"] if requested_state == "all" else [requested_state]
    for item_kind in kinds:
        algo_history[item_kind] = {}
        for item_state in states:
            params = {"ordType": item_kind, "state": item_state, "limit": safe_limit}
            if inst_id:
                params["instId"] = inst_id
            response = client.privateGetTradeOrdersAlgoHistory(params)
            response["data"] = _filter_okx_rows(response.get("data") or [], algo_id=algo_id, order_id=order_id)
            algo_history[item_kind][item_state] = response

    orders_history = client.privateGetTradeOrdersHistory(base_params)
    orders_history["data"] = _filter_okx_rows(orders_history.get("data") or [], algo_id=algo_id, order_id=order_id)
    fills = client.privateGetTradeFills(base_params)
    fills["data"] = _filter_okx_rows(fills.get("data") or [], algo_id=algo_id, order_id=order_id)

    return redact_mapping(
        {
            "exchange": "okx",
            "symbol": inst_id,
            "filters": {"kind": requested_kind, "state": requested_state, "algo_id": algo_id, "order_id": order_id},
            "orders_history": orders_history,
            "fills": fills,
            "bills": client.privateGetAccountBills(base_params),
            "algo_history": algo_history,
        }
    )


def cancel_okx_algo_orders(client: Any, symbol: str, algo_ids: list[str]) -> dict[str, Any]:
    inst_id = str(symbol or "").strip().upper()
    if not inst_id:
        raise ValueError("symbol is required")
    ids = [str(item).strip() for item in algo_ids if str(item).strip()]
    if not ids:
        raise ValueError("at least one algo id is required")
    rows = [{"algoId": algo_id, "instId": inst_id} for algo_id in ids]
    return redact_mapping({"exchange": "okx", "symbol": inst_id, "result": client.privatePostTradeCancelAlgos(rows)})


