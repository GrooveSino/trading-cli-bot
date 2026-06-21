from __future__ import annotations

from datetime import datetime, timedelta
from typing import Any

from .models import CST, MaintenanceConfig, PositionView
from .utils import _float, _float_changed, _last_int, _maybe_float, _normalize_inst_id, _parse_cst_datetime, _safe_data


def _fetch_account(client: Any) -> dict[str, Any]:
    raw = client.privateGetAccountBalance({})
    data = (raw.get("data") or [{}])[0]
    usdt = next((row for row in data.get("details", []) if row.get("ccy") == "USDT"), {})
    return {
        "totalEq": data.get("totalEq"),
        "usdtEq": usdt.get("eq"),
        "availBal": usdt.get("availBal"),
        "cashBal": usdt.get("cashBal"),
        "frozenBal": usdt.get("frozenBal"),
        "isoEq": usdt.get("isoEq"),
        "upl": usdt.get("upl"),
        "uTime": usdt.get("uTime"),
    }


def _build_positions(raw_positions: list[dict[str, Any]], oco_orders: list[dict[str, Any]], config: MaintenanceConfig) -> list[PositionView]:
    oco_by_symbol = {_normalize_inst_id(row.get("instId")): row for row in oco_orders if row.get("state") == "live"}
    views: list[PositionView] = []
    for row in raw_positions:
        size = _float(row.get("pos"))
        if size == 0:
            continue
        inst_id = _normalize_inst_id(row.get("instId"))
        oco = oco_by_symbol.get(inst_id)
        close_algo = (row.get("closeOrderAlgo") or [{}])[0] if row.get("closeOrderAlgo") else {}
        algo_id = str((oco or {}).get("algoId") or close_algo.get("algoId") or "").strip() or None
        owner = "automation" if inst_id in config.automation_symbols else "external/user-managed"
        if inst_id in config.user_symbols:
            owner = "external/user-managed"
        protected = _is_protective_oco(row, oco)
        views.append(
            PositionView(
                inst_id=inst_id,
                owner=owner,
                side="long" if size > 0 else "short",
                size=size,
                entry=_float(row.get("avgPx")),
                mark=_float(row.get("markPx") or row.get("last")),
                upl=_float(row.get("upl")),
                realized_pnl=_float(row.get("realizedPnl")),
                fee=_float(row.get("fee")),
                funding_fee=_float(row.get("fundingFee")),
                margin_mode=str(row.get("mgnMode") or ""),
                leverage=str(row.get("lever") or ""),
                liq_px=str(row.get("liqPx") or ""),
                margin=_float(row.get("margin")),
                tp=_maybe_float((oco or {}).get("tpTriggerPx") or close_algo.get("tpTriggerPx")),
                sl=_maybe_float((oco or {}).get("slTriggerPx") or close_algo.get("slTriggerPx")),
                oco_algo_id=algo_id,
                protected=protected,
                protection_note="live reduce-only full-close OCO" if protected else "missing or mismatched OCO",
            )
        )
    return sorted(views, key=lambda item: (item.owner != "automation", item.inst_id))


def _is_protective_oco(position: dict[str, Any], oco: dict[str, Any] | None) -> bool:
    if not oco or oco.get("state") != "live":
        return False
    if str(oco.get("reduceOnly")).lower() != "true":
        return False
    if str(oco.get("closeFraction") or "") not in {"1", "1.0"}:
        return False
    size = _float(position.get("pos"))
    expected_side = "sell" if size > 0 else "buy"
    return str(oco.get("side") or "").lower() == expected_side


def _next_audit(prior_state: str, now: datetime, gap: int, has_candidate: bool) -> tuple[int, int]:
    current_day = now.strftime("%Y-%m-%d")
    prior_timestamp = _parse_cst_datetime(next((line for line in prior_state.splitlines() if "Timestamp basis" in line), ""))
    if prior_timestamp and prior_timestamp.strftime("%Y-%m-%d") != current_day:
        return 1, 1 if gap > 0 and not has_candidate else 0
    audit = 1
    refusal = 0
    for line in prior_state.splitlines():
        if current_day not in line and "2026-" in line:
            continue
        if "daily audit entry" in line.lower() or "2026-06-05 cst daily audit entry" in line.lower():
            audit = max(audit, _last_int(line) + 1)
        parsed_refusal = _parse_hard_refusal_count(line)
        if parsed_refusal is not None:
            refusal = max(refusal, parsed_refusal)
    if gap > 0 and not has_candidate:
        refusal += 1
    return audit, refusal


def _parse_hard_refusal_count(line: str) -> int | None:
    import re

    patterns = (
        r"Current daily hard-refusal count:\s*(\d+)",
        r"Hard-refusal count today:\s*(\d+)",
        r"Hard-refusal count after this run:\s*(\d+)",
        r"Current \d{4}-\d{2}-\d{2}(?: daily)? hard-refusal count:\s*(\d+)",
    )
    for pattern in patterns:
        match = re.search(pattern, line, re.IGNORECASE)
        if match:
            return int(match.group(1))
    return None


def _infer_since_ms(prior_state: str, now: datetime, fallback_minutes: int) -> int:
    for line in prior_state.splitlines():
        if "Timestamp basis" in line or "Final private verification time" in line:
            parsed = _parse_cst_datetime(line)
            if parsed:
                return int(parsed.timestamp() * 1000)
    return int((now - timedelta(minutes=fallback_minutes)).timestamp() * 1000)


def _detect_protection_changes(prior_state: str, positions: list[PositionView]) -> list[dict[str, Any]]:
    prior = _parse_prior_position_brackets(prior_state)
    changes: list[dict[str, Any]] = []
    for position in positions:
        previous = prior.get(position.inst_id)
        if not previous:
            continue
        old_tp = previous.get("tp")
        old_sl = previous.get("sl")
        old_algo = previous.get("algoId")
        tp_changed = _float_changed(old_tp, position.tp)
        sl_changed = _float_changed(old_sl, position.sl)
        algo_changed = bool(old_algo and position.oco_algo_id and old_algo != position.oco_algo_id)
        if tp_changed or sl_changed or algo_changed:
            changes.append(
                {
                    "instId": position.inst_id,
                    "owner": position.owner,
                    "oldTp": old_tp,
                    "newTp": position.tp,
                    "oldSl": old_sl,
                    "newSl": position.sl,
                    "oldAlgoId": old_algo,
                    "newAlgoId": position.oco_algo_id,
                }
            )
    return changes


def _parse_prior_position_brackets(prior_state: str) -> dict[str, dict[str, Any]]:
    out: dict[str, dict[str, Any]] = {}
    for line in prior_state.splitlines():
        if "-USDT-SWAP" not in line or not line.startswith("|"):
            continue
        cells = [cell.strip().strip("`") for cell in line.strip().strip("|").split("|")]
        if len(cells) < 10:
            continue
        inst_id = _normalize_inst_id(cells[0])
        if not inst_id.endswith("-USDT-SWAP"):
            continue
        out[inst_id] = {
            "tp": _maybe_float(cells[7]),
            "sl": _maybe_float(cells[8]),
            "algoId": cells[9] if cells[9] != "-" else None,
        }
    return out


def _fetch_oco_history_since(client: Any, since_ms: int) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    for state in ("effective", "canceled", "order_failed"):
        data = _safe_data(client.privateGetTradeOrdersAlgoHistory({"ordType": "oco", "state": state, "limit": "100"}))
        for row in data:
            ts = int(row.get("uTime") or row.get("cTime") or 0)
            if ts >= since_ms:
                summary = _timed_summary(row, "uTime" if row.get("uTime") else "cTime")
                summary["stateQuery"] = state
                rows.append(summary)
    return rows


def _filter_since(rows: list[dict[str, Any]], key: str, since_ms: int) -> list[dict[str, Any]]:
    return [row for row in rows if int(row.get(key) or 0) >= since_ms]


def _timed_summary(row: dict[str, Any], time_key: str) -> dict[str, Any]:
    ts = int(row.get(time_key) or 0)
    out = {
        "instId": row.get("instId"),
        "ordId": row.get("ordId"),
        "algoId": row.get("algoId"),
        "side": row.get("side") or row.get("actualSide"),
        "state": row.get("state"),
        "px": row.get("px") or row.get("fillPx") or row.get("actualPx"),
        "sz": row.get("sz") or row.get("fillSz") or row.get("actualSz"),
        "fee": row.get("fee"),
        "pnl": row.get("pnl"),
    }
    if ts:
        out[f"{time_key}CST"] = datetime.fromtimestamp(ts / 1000, CST).strftime("%Y-%m-%d %H:%M:%S")
    return out


def _oco_summary(row: dict[str, Any]) -> dict[str, Any]:
    return {
        "instId": row.get("instId"),
        "algoId": row.get("algoId"),
        "state": row.get("state"),
        "side": row.get("side"),
        "reduceOnly": row.get("reduceOnly"),
        "closeFraction": row.get("closeFraction"),
        "tpTriggerPx": row.get("tpTriggerPx"),
        "slTriggerPx": row.get("slTriggerPx"),
        "tpOrdPx": row.get("tpOrdPx"),
        "slOrdPx": row.get("slOrdPx"),
    }
