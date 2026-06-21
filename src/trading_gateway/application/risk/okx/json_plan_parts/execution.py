from __future__ import annotations

from decimal import Decimal, InvalidOperation
from typing import Any

from trading_gateway.support.redaction import redact_mapping

from .builder import build_okx_json_plan


def place_okx_json_plan_orders(client: Any, compiled_plan: dict[str, Any]) -> dict[str, Any]:
    if compiled_plan.get("raw_plan"):
        compiled_plan = build_okx_json_plan(compiled_plan["raw_plan"], **_live_specs(client, str(compiled_plan["symbol"])))
    symbol = str(compiled_plan["symbol"])
    if bool((compiled_plan.get("execution") or {}).get("replace_existing")):
        _cancel_existing(client, symbol)
    leverage = client.privatePostAccountSetLeverage(
        {"instId": symbol, "lever": str(compiled_plan["leverage"]), "mgnMode": str(compiled_plan["margin_mode"])}
    )
    results = []
    for order in compiled_plan.get("orders") or []:
        endpoint = order.get("endpoint")
        if endpoint == "order_algo":
            result = client.privatePostTradeOrderAlgo(order["payload"])
        else:
            result = client.privatePostTradeOrder(order["payload"])
        results.append({"id": order.get("id"), "endpoint": endpoint, "result": result})
    return redact_mapping(
        {
            "status": "submitted",
            "exchange": "okx",
            "symbol": symbol,
            "leverage": leverage,
            "orders": results,
            "verification": {
                "open_orders": client.privateGetTradeOrdersPending({"instType": "SWAP", "instId": symbol}),
                "trigger": client.privateGetTradeOrdersAlgoPending({"ordType": "trigger", "instId": symbol}),
                "conditional": client.privateGetTradeOrdersAlgoPending({"ordType": "conditional", "instId": symbol}),
                "oco": client.privateGetTradeOrdersAlgoPending({"ordType": "oco", "instId": symbol}),
                "positions": client.privateGetAccountPositions({"instType": "SWAP", "instId": symbol}),
            },
        }
    )


def place_okx_guarded_json_plan_orders(client: Any, compiled_plan: dict[str, Any]) -> dict[str, Any]:
    if compiled_plan.get("raw_plan"):
        compiled_plan = build_okx_json_plan(compiled_plan["raw_plan"], **_live_specs(client, str(compiled_plan["symbol"])))
    symbol = str(compiled_plan["symbol"])
    precheck = _guard_snapshot(client, symbol)
    violations = _guard_violations(precheck)
    if violations:
        return redact_mapping(
            {
                "status": "blocked",
                "exchange": "okx",
                "symbol": symbol,
                "precheck": precheck,
                "violations": violations,
                "orders": [],
                "verification": {},
                "final_outcome": "precheck_blocked",
            }
        )

    leverage = client.privatePostAccountSetLeverage(
        {"instId": symbol, "lever": str(compiled_plan["leverage"]), "mgnMode": str(compiled_plan["margin_mode"])}
    )
    results = []
    for order in compiled_plan.get("orders") or []:
        endpoint = order.get("endpoint")
        if endpoint == "order_algo":
            result = client.privatePostTradeOrderAlgo(order["payload"])
        else:
            result = client.privatePostTradeOrder(order["payload"])
        results.append({"id": order.get("id"), "endpoint": endpoint, "result": result})

    verification = _guard_snapshot(client, symbol)
    submitted_ids = _submitted_order_ids(results)
    outcome = _guard_final_outcome(verification, submitted_ids, _target_sides(compiled_plan))
    return redact_mapping(
        {
            "status": "success" if outcome in {"resting", "filled_or_partial"} else "failed",
            "exchange": "okx",
            "symbol": symbol,
            "leverage": leverage,
            "precheck": precheck,
            "orders": results,
            "submitted_order_ids": submitted_ids,
            "verification": verification,
            "final_outcome": outcome,
        }
    )


def prepare_okx_json_plan_for_live(raw_plan: dict[str, Any]) -> dict[str, Any]:
    dry = build_okx_json_plan(raw_plan)
    dry["raw_plan"] = raw_plan
    return dry


def _cancel_existing(client: Any, symbol: str) -> None:
    pending = client.privateGetTradeOrdersPending({"instType": "SWAP", "instId": symbol})
    for row in pending.get("data") or []:
        ord_id = str(row.get("ordId") or "").strip()
        if ord_id:
            client.privatePostTradeCancelOrder({"instId": symbol, "ordId": ord_id})
    for kind in ("trigger", "conditional", "oco"):
        rows = client.privateGetTradeOrdersAlgoPending({"ordType": kind, "instId": symbol}).get("data") or []
        ids = [{"algoId": str(row.get("algoId")), "instId": symbol} for row in rows if row.get("algoId")]
        if ids:
            client.privatePostTradeCancelAlgos(ids)


def _live_specs(client: Any, symbol: str) -> dict[str, str]:
    response = client.publicGetPublicInstruments({"instType": "SWAP", "instId": symbol})
    rows = response.get("data") or []
    if not rows:
        raise ValueError(f"OKX instrument not found: {symbol}")
    row = rows[0]
    pos_mode = ""
    if hasattr(client, "privateGetAccountConfig"):
        config_rows = client.privateGetAccountConfig().get("data") or []
        if config_rows:
            pos_mode = str(config_rows[0].get("posMode") or "")
    return {
        "contract_btc": str(row.get("ctVal") or "0.01"),
        "lot_size": str(row.get("lotSz") or row.get("minSz") or "0.01"),
        "min_size": str(row.get("minSz") or row.get("lotSz") or "0.01"),
        "tick_size": str(row.get("tickSz") or ""),
        "position_mode": pos_mode,
    }


def _guard_snapshot(client: Any, symbol: str) -> dict[str, Any]:
    positions = client.privateGetAccountPositions({"instType": "SWAP", "instId": symbol})
    open_orders = client.privateGetTradeOrdersPending({"instType": "SWAP", "instId": symbol})
    algos = {
        kind: client.privateGetTradeOrdersAlgoPending({"ordType": kind, "instId": symbol})
        for kind in ("trigger", "conditional", "oco")
    }
    active_positions = _active_positions(positions.get("data") or [], symbol)
    pending_orders = _rows_for_symbol(open_orders.get("data") or [], symbol)
    active_algos = {kind: _rows_for_symbol((payload.get("data") or []), symbol) for kind, payload in algos.items()}
    return {
        "positions": positions,
        "open_orders": open_orders,
        "algo_orders": algos,
        "active_positions": active_positions,
        "pending_orders": pending_orders,
        "active_algo_orders": active_algos,
        "counts": {
            "positions": len(active_positions),
            "open_orders": len(pending_orders),
            "algo_orders": sum(len(rows) for rows in active_algos.values()),
        },
    }


def _guard_violations(snapshot: dict[str, Any]) -> list[dict[str, Any]]:
    counts = snapshot.get("counts") or {}
    violations = []
    if int(counts.get("positions") or 0) > 0:
        violations.append({"kind": "positions", "count": counts.get("positions")})
    if int(counts.get("open_orders") or 0) > 0:
        violations.append({"kind": "open_orders", "count": counts.get("open_orders")})
    if int(counts.get("algo_orders") or 0) > 0:
        violations.append({"kind": "algo_orders", "count": counts.get("algo_orders")})
    return violations


def _guard_final_outcome(snapshot: dict[str, Any], submitted_ids: list[str], target_sides: set[str]) -> str:
    pending_rows = snapshot.get("pending_orders") or []
    if submitted_ids and any(str(row.get("ordId") or "") in submitted_ids and str(row.get("state") or "").lower() == "live" for row in pending_rows):
        return "resting"
    if _has_target_position(snapshot.get("active_positions") or [], target_sides):
        return "filled_or_partial"
    return "not_resting_or_filled"


def _submitted_order_ids(results: list[dict[str, Any]]) -> list[str]:
    ids: list[str] = []
    for item in results:
        for row in ((item.get("result") or {}).get("data") or []):
            order_id = str(row.get("ordId") or "").strip()
            if order_id:
                ids.append(order_id)
    return ids


def _target_sides(compiled_plan: dict[str, Any]) -> set[str]:
    return {str(order.get("side") or "").strip().lower() for order in (compiled_plan.get("orders") or []) if order.get("side")}


def _has_target_position(rows: list[dict[str, Any]], target_sides: set[str]) -> bool:
    for row in rows:
        pos = _decimal(row.get("pos"))
        if pos is None or pos == 0:
            continue
        if pos > 0 and "buy" in target_sides:
            return True
        if pos < 0 and "sell" in target_sides:
            return True
    return False


def _active_positions(rows: list[dict[str, Any]], symbol: str) -> list[dict[str, Any]]:
    active = []
    for row in _rows_for_symbol(rows, symbol):
        pos = _decimal(row.get("pos"))
        if pos is not None and pos != 0:
            active.append(row)
    return active


def _rows_for_symbol(rows: list[dict[str, Any]], symbol: str) -> list[dict[str, Any]]:
    return [row for row in rows if not row.get("instId") or str(row.get("instId")) == symbol]


def _decimal(value: Any) -> Decimal | None:
    try:
        return Decimal(str(value or "0"))
    except (InvalidOperation, ValueError):
        return None
