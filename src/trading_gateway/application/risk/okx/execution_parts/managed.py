from __future__ import annotations

import time
from decimal import Decimal, ROUND_FLOOR
from typing import Any

from .orders import fetch_okx_recent_history
from ..notional import build_okx_static_notional_plan, build_okx_trigger_oco_plan


def _okx_response_ok(response: dict[str, Any]) -> bool:
    if str(response.get("code", "")) != "0":
        return False
    rows = response.get("data") or []
    return all(str(row.get("sCode", "0")) == "0" for row in rows)


def _result_algo_id(response: dict[str, Any]) -> str:
    rows = response.get("data") or []
    if not rows:
        return ""
    return str(rows[0].get("algoId") or "").strip()


def _history_rows(response: dict[str, Any], kind: str, state: str) -> list[dict[str, Any]]:
    return ((response.get("algo_history") or {}).get(kind) or {}).get(state, {}).get("data") or []


def _pending_algo_rows(client: Any, symbol: str, ord_type: str) -> list[dict[str, Any]]:
    return (client.privateGetTradeOrdersAlgoPending({"ordType": ord_type, "instId": symbol}).get("data") or [])


def _okx_instrument_specs(client: Any, symbol: str) -> dict[str, str]:
    if not hasattr(client, "publicGetPublicInstruments"):
        return {"contract_btc": "0.01", "lot_size": "0.01", "min_size": "0.01", "tick_size": ""}
    response = client.publicGetPublicInstruments({"instType": "SWAP", "instId": symbol})
    rows = response.get("data") or []
    if not rows:
        raise ValueError(f"OKX instrument not found: {symbol}")
    row = rows[0]
    return {
        "contract_btc": str(row.get("ctVal") or "0.01"),
        "lot_size": str(row.get("lotSz") or row.get("minSz") or "0.01"),
        "min_size": str(row.get("minSz") or row.get("lotSz") or "0.01"),
        "tick_size": str(row.get("tickSz") or ""),
    }


def _okx_position_mode(client: Any) -> str:
    try:
        rows = (client.privateGetAccountConfig().get("data") or [])
    except AttributeError:
        return ""
    if not rows:
        return ""
    return str(rows[0].get("posMode") or "")


def _recompile_with_live_specs(client: Any, plan: dict[str, Any]) -> dict[str, Any]:
    symbol = str(plan["symbol"])
    specs = _okx_instrument_specs(client, symbol)
    position_mode = _okx_position_mode(client)
    kwargs = {
        "leverage": int(plan["leverage"]),
        "margin_mode": str(plan["margin_mode"]),
        "tp_trigger_px_type": str(plan.get("tp_trigger_px_type") or "last"),
        "sl_trigger_px_type": str(plan.get("sl_trigger_px_type") or "mark"),
        "trigger_px_type": str(plan.get("trigger_px_type") or "last"),
        "contract_btc": specs["contract_btc"],
        "lot_size": specs["lot_size"],
        "min_size": specs["min_size"],
        "tick_size": specs["tick_size"],
        "position_mode": position_mode,
    }
    if plan.get("mode") == "static_notional_plan":
        return build_okx_static_notional_plan(
            "okx",
            symbol,
            side=str(plan["side"]),
            entries=list(plan.get("source_entries") or []),
            stop_limit_mode=str(plan.get("stop_limit_mode") or "auto"),
            **kwargs,
        )
    if plan.get("mode") == "trigger_oco_plan":
        return build_okx_trigger_oco_plan(
            "okx",
            symbol,
            legs=list(plan.get("source_legs") or []),
            **kwargs,
        )
    return plan


def _verify_leverage(client: Any, symbol: str, leverage: str, margin_mode: str) -> dict[str, Any]:
    set_result = client.privatePostAccountSetLeverage({"instId": symbol, "lever": str(leverage), "mgnMode": str(margin_mode)})
    verification: dict[str, Any] = {"set_result": set_result, "verified": True}
    if hasattr(client, "privateGetAccountLeverageInfo"):
        info = client.privateGetAccountLeverageInfo({"instId": symbol, "mgnMode": str(margin_mode)})
        verification["leverage_info"] = info
        rows = info.get("data") or []
        if rows and not any(str(row.get("lever") or "") == str(leverage) for row in rows):
            verification["verified"] = False
            raise ValueError(f"OKX leverage verification failed for {symbol}: expected {leverage}x")
    return verification


def _position_rows(client: Any, symbol: str) -> list[dict[str, Any]]:
    return client.privateGetAccountPositions({"instType": "SWAP", "instId": symbol}).get("data") or []


def _position_abs_size(client: Any, symbol: str, side: str) -> Decimal:
    expected_sign = Decimal("-1") if side == "sell" else Decimal("1")
    for row in _position_rows(client, symbol):
        if str(row.get("instId") or "") != symbol:
            continue
        try:
            pos = Decimal(str(row.get("pos") or "0"))
        except Exception:  # noqa: BLE001 - exchange payloads can contain blank strings.
            continue
        if pos == 0:
            continue
        if (pos > 0 and expected_sign > 0) or (pos < 0 and expected_sign < 0):
            return abs(pos)
    return Decimal("0")


def _round_down(value: Decimal, lot: Decimal) -> Decimal:
    return (value / lot).to_integral_value(rounding=ROUND_FLOOR) * lot


def _num(value: Decimal) -> str:
    return format(value.normalize(), "f")


def _managed_exit_payloads(symbol: str, td_mode: str, exit_plan: dict[str, Any], filled_size: Decimal) -> list[dict[str, Any]]:
    close_side = str(exit_plan["close_side"])
    lot = Decimal(str(exit_plan.get("lot_size") or "0.01"))
    remaining = _round_down(filled_size, lot)
    payloads: list[dict[str, Any]] = []
    take_profits = exit_plan.get("take_profits") or []
    for index, tp in enumerate(take_profits, start=1):
        if index == len(take_profits):
            size = remaining
        else:
            size = _round_down(filled_size * Decimal(str(tp["allocation_pct"])) / Decimal("100"), lot)
            remaining -= size
        if size <= 0:
            continue
        payloads.append(
            {
                "kind": "take_profit",
                "payload": {
                    "instId": symbol,
                    "tdMode": td_mode,
                    "side": close_side,
                    "ordType": "conditional",
                    "sz": _num(size),
                    "tpTriggerPx": str(tp["trigger_px"]),
                    "tpOrdPx": str(tp.get("order_px") or "-1"),
                    "tpTriggerPxType": str(tp["trigger_px_type"]),
                    "reduceOnly": "true",
                },
            }
        )
        if exit_plan.get("pos_side"):
            payloads[-1]["payload"]["posSide"] = str(exit_plan["pos_side"])
    sl = exit_plan["stop_loss"]
    payloads.append(
        {
            "kind": "stop_loss",
            "payload": {
                "instId": symbol,
                "tdMode": td_mode,
                "side": close_side,
                "ordType": "conditional",
                "sz": _num(filled_size),
                "slTriggerPx": str(sl["trigger_px"]),
                "slOrdPx": str(sl.get("order_px") or "-1"),
                "slTriggerPxType": str(sl["trigger_px_type"]),
                "reduceOnly": "true",
            },
        }
    )
    if exit_plan.get("pos_side"):
        payloads[-1]["payload"]["posSide"] = str(exit_plan["pos_side"])
    return payloads


def _verify_exit_algos(client: Any, symbol: str, expected_ids: list[str]) -> bool:
    if not expected_ids:
        return False
    live_ids = {
        str(row.get("algoId") or "").strip()
        for row in _pending_algo_rows(client, symbol, "conditional")
        if str(row.get("state") or "live") == "live"
    }
    return all(algo_id in live_ids for algo_id in expected_ids)


def _verify_parent_trigger_live(client: Any, symbol: str, algo_id: str) -> bool:
    rows = _pending_algo_rows(client, symbol, "trigger")
    if not rows:
        return True
    live_ids = {str(row.get("algoId") or "").strip() for row in rows}
    return bool(algo_id and algo_id in live_ids)


def _cancel_algo_ids(client: Any, symbol: str, algo_ids: list[str]) -> list[dict[str, Any]]:
    ids = [algo_id for algo_id in algo_ids if algo_id]
    if not ids:
        return []
    rows = [{"algoId": algo_id, "instId": symbol} for algo_id in ids]
    return [client.privatePostTradeCancelAlgos(rows)]


def _cancel_exits_after_flat(
    client: Any,
    symbol: str,
    parent_side: str,
    algo_ids: list[str],
    *,
    poll_interval_sec: float,
    timeout_sec: float,
) -> dict[str, Any]:
    deadline = time.monotonic() + timeout_sec
    while True:
        if _position_abs_size(client, symbol, parent_side) == 0:
            return {"state": "flat", "canceled": _cancel_algo_ids(client, symbol, algo_ids)}
        if time.monotonic() > deadline:
            return {"state": "timeout", "canceled": []}
        time.sleep(poll_interval_sec)


def _emergency_reduce_only_close(client: Any, symbol: str, td_mode: str, parent_side: str, size: Decimal) -> dict[str, Any]:
    close_side = "buy" if parent_side == "sell" else "sell"
    payload = {
        "instId": symbol,
        "tdMode": td_mode,
        "side": close_side,
        "ordType": "market",
        "sz": _num(size),
        "reduceOnly": "true",
    }
    return client.privatePostTradeOrder(payload)


def _wait_for_managed_parent(
    client: Any,
    symbol: str,
    parent_algo_id: str,
    parent_side: str,
    *,
    poll_interval_sec: float,
    timeout_sec: float,
) -> dict[str, Any]:
    deadline = time.monotonic() + timeout_sec
    while True:
        pending_ids = {str(row.get("algoId") or "").strip() for row in _pending_algo_rows(client, symbol, "trigger")}
        if parent_algo_id in pending_ids:
            if time.monotonic() > deadline:
                return {"state": "timeout", "parent_algo_id": parent_algo_id}
            time.sleep(poll_interval_sec)
            continue

        history = fetch_okx_recent_history(client, symbol, limit=100, kind="trigger", state="all", algo_id=parent_algo_id)
        failed = _history_rows(history, "trigger", "order_failed")
        if failed:
            return {"state": "order_failed", "history": history}
        canceled = _history_rows(history, "trigger", "canceled")
        if canceled:
            return {"state": "canceled", "history": history}
        effective = _history_rows(history, "trigger", "effective")
        if effective:
            filled_size = _position_abs_size(client, symbol, parent_side)
            if filled_size > 0:
                return {"state": "filled", "filled_size": _num(filled_size), "history": history}
        if time.monotonic() > deadline:
            return {"state": "timeout", "parent_algo_id": parent_algo_id}
        time.sleep(poll_interval_sec)

