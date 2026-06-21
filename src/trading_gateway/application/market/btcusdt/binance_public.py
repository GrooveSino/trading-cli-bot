from __future__ import annotations

from datetime import datetime, timezone
from typing import Any

from .shared import BINANCE_FAPI, CST, SYMBOL, HttpClient, cst_datetime, oi_delta


def collect_open_interest(client: HttpClient, symbol: str = SYMBOL) -> dict[str, Any]:
    current = client.get_json(f"{BINANCE_FAPI}/fapi/v1/openInterest", {"symbol": symbol})
    history = client.get_json(f"{BINANCE_FAPI}/futures/data/openInterestHist", {"symbol": symbol, "period": "5m", "limit": "24"})
    rows = [(int(row["timestamp"]), float(row["sumOpenInterest"]), float(row["sumOpenInterestValue"])) for row in history]
    latest = rows[-1]
    latest_price = latest[2] / latest[1] if latest[1] else None
    return {
        "source": "Binance /fapi/v1/openInterest + /futures/data/openInterestHist",
        "current": {"btc": float(current["openInterest"]), "timestamp_cst": cst_datetime(int(current["time"]))},
        "history_latest_timestamp_cst": cst_datetime(latest[0]),
        "delta": {label: oi_delta(rows, steps, latest_price=latest_price) for label, steps in {"5m": 1, "15m": 3, "1h": 12}.items()},
    }


def collect_long_short_ratios(client: HttpClient, symbol: str = SYMBOL) -> dict[str, Any]:
    return {
        "source": "Binance futures/data long-short ratio endpoints",
        "top_position": ratio_row(client, "topLongShortPositionRatio", symbol),
        "top_account": ratio_row(client, "topLongShortAccountRatio", symbol),
        "global_account": ratio_row(client, "globalLongShortAccountRatio", symbol),
    }


def collect_liquidations(client: HttpClient, now_ms: int, symbol: str = SYMBOL) -> dict[str, Any]:
    start_ms = now_ms - 30 * 60 * 1000
    try:
        rows = client.get_json(
            f"{BINANCE_FAPI}/fapi/v1/allForceOrders",
            {"symbol": symbol, "startTime": start_ms, "endTime": now_ms, "limit": "1000"},
        )
    except Exception as exc:  # noqa: BLE001 - this endpoint is often unavailable.
        return {
            "source": "Binance /fapi/v1/allForceOrders",
            "available": False,
            "reason": f"{type(exc).__name__}: {exc}",
            "note": "Public REST liquidation history is not reliable; use a live websocket stream or paid data source for density.",
        }
    long_liq = short_liq = 0.0
    per_minute: dict[str, dict[str, float]] = {}
    for row in rows:
        stamp = int(row.get("time") or row.get("updateTime") or 0)
        minute = datetime.fromtimestamp(stamp / 1000, timezone.utc).astimezone(CST).strftime("%H:%M")
        notional = float(row.get("avgPrice") or row.get("price") or 0) * float(row.get("executedQty") or row.get("origQty") or 0)
        bucket = per_minute.setdefault(minute, {"long_liq_usd": 0.0, "short_liq_usd": 0.0})
        if row.get("side") == "SELL":
            long_liq += notional
            bucket["long_liq_usd"] += notional
        elif row.get("side") == "BUY":
            short_liq += notional
            bucket["short_liq_usd"] += notional
    return {"source": "Binance /fapi/v1/allForceOrders", "available": True, "long_liq_usd": long_liq, "short_liq_usd": short_liq, "per_minute": per_minute}


def ratio_row(client: HttpClient, endpoint: str, symbol: str = SYMBOL) -> dict[str, Any]:
    row = client.get_json(f"{BINANCE_FAPI}/futures/data/{endpoint}", {"symbol": symbol, "period": "5m", "limit": "1"})[-1]
    return {**row, "timestamp_cst": cst_datetime(int(row["timestamp"]))}
