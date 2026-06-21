from __future__ import annotations

from datetime import datetime, timezone
from typing import Any

from trading_gateway.application.market.btcusdt.shared import cst_datetime


def force_order_messages(message: Any) -> list[dict[str, Any]]:
    rows = message if isinstance(message, list) else [message]
    return [row for row in rows if isinstance(row, dict)]


def parse_force_order(message: dict[str, Any]) -> dict[str, Any]:
    event = message.get("o") or message
    price = float(event.get("ap") or event.get("p") or 0)
    qty = float(event.get("z") or event.get("q") or 0)
    side = str(event.get("S") or event.get("side") or "").upper()
    event_ms = int(message.get("E") or event.get("T") or event.get("time") or 0)
    liq_side = "long" if side == "SELL" else "short" if side == "BUY" else "unknown"
    return {
        "event_ms": event_ms,
        "symbol": str(event.get("s") or event.get("symbol") or "BTCUSDT"),
        "side": side,
        "liquidated_side": liq_side,
        "price": price,
        "qty_btc": qty,
        "notional_usd": price * qty,
        "order_status": event.get("X"),
    }


def bucket_price(price: float, bucket_usd: float) -> float:
    if bucket_usd <= 0:
        return price
    return round(price / bucket_usd) * bucket_usd


def liquidation_density(
    events: list[dict[str, Any]],
    *,
    bucket_usd: float,
    now_ms: int,
    stream_status: dict[str, Any] | None = None,
    symbol: str = "BTCUSDT",
) -> dict[str, Any]:
    buckets: dict[float, dict[str, Any]] = {}
    for event in events:
        bucket = bucket_price(float(event["price"]), bucket_usd)
        row = buckets.setdefault(bucket, {"price_bucket": bucket, "long_liq_usd": 0.0, "short_liq_usd": 0.0, "long_count": 0, "short_count": 0})
        side = event.get("liquidated_side")
        if side == "long":
            row["long_liq_usd"] += float(event["notional_usd"])
            row["long_count"] += 1
        elif side == "short":
            row["short_liq_usd"] += float(event["notional_usd"])
            row["short_count"] += 1
    rows = sorted(buckets.values(), key=lambda item: item["price_bucket"])
    return {
        "source": f"Binance !forceOrder@arr websocket filtered to {symbol}",
        "available": True,
        "kind": "realized_liquidation_density_24h",
        "note": _density_note(events, stream_status, symbol),
        "bucket_usd": bucket_usd,
        "event_count": len(events),
        "long_liq_usd": sum(float(row["long_liq_usd"]) for row in rows),
        "short_liq_usd": sum(float(row["short_liq_usd"]) for row in rows),
        "last_event_cst": cst_datetime(max((int(row["event_ms"]) for row in events), default=0)) if events else None,
        "generated_at_cst": cst_datetime(now_ms),
        "stream_status": stream_status or {},
        "buckets": rows,
    }


def _density_note(events: list[dict[str, Any]], stream_status: dict[str, Any] | None, symbol: str) -> str:
    base = "已发生强平密度，不是未来爆仓热力图。"
    if events:
        return base
    status = (stream_status or {}).get("status")
    if status == "connected":
        return f"{base} WebSocket 已连接，但 24h 存储窗口内尚未捕获 {symbol} 强平事件。"
    return f"{base} WebSocket 状态未确认，当前没有可聚合的 {symbol} 强平事件。"


def utc_ms() -> int:
    return int(datetime.now(timezone.utc).timestamp() * 1000)
