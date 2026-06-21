from __future__ import annotations

from statistics import fmean, pstdev
from typing import Any

from .shared import BINANCE_FAPI, SYMBOL, HttpClient, cst_datetime, kline_summary, rsi


def collect_rsi(client: HttpClient, symbol: str = SYMBOL) -> dict[str, Any]:
    rsi_payload = {}
    for interval in ("15m", "1h", "4h"):
        klines = client.get_json(f"{BINANCE_FAPI}/fapi/v1/klines", {"symbol": symbol, "interval": interval, "limit": "120"})
        closes = [float(row[4]) for row in klines]
        rsi_payload[interval] = {
            "current_bar_open_cst": cst_datetime(int(klines[-1][0])),
            "rsi14_live": rsi(closes),
            "rsi14_latest_closed": rsi(closes[:-1]),
        }
    return {"source": "Binance /fapi/v1/klines", "rsi": rsi_payload}


def collect_vpp(client: HttpClient, symbol: str = SYMBOL) -> dict[str, Any]:
    rows = client.get_json(f"{BINANCE_FAPI}/fapi/v1/klines", {"symbol": symbol, "interval": "15m", "limit": "6"})
    latest_closed = rows[-2]
    return {
        "source": "Binance /fapi/v1/klines",
        "latest_closed_15m": kline_summary(latest_closed),
    }


def collect_momentum_bundle(client: HttpClient, symbol: str = SYMBOL) -> dict[str, Any]:
    klines_by_interval = {
        interval: client.get_json(f"{BINANCE_FAPI}/fapi/v1/klines", {"symbol": symbol, "interval": interval, "limit": "120"})
        for interval in ("15m", "1h", "4h")
    }
    rsi_payload = {}
    for interval, rows in klines_by_interval.items():
        closes = [float(row[4]) for row in rows]
        rsi_payload[interval] = {
            "current_bar_open_cst": cst_datetime(int(rows[-1][0])),
            "rsi14_live": rsi(closes),
            "rsi14_latest_closed": rsi(closes[:-1]),
        }
    closed_15m = klines_by_interval["15m"][:-1]
    vpp_payload = {"source": "Binance /fapi/v1/klines", "latest_closed_15m": kline_summary(klines_by_interval["15m"][-2])}
    baselines = _momentum_baselines(closed_15m[-96:])
    rsi_result = {"source": "Binance /fapi/v1/klines", "rsi": rsi_payload}
    return {
        "binance_rsi": rsi_result,
        "binance_vpp": vpp_payload,
        "binance_momentum": {"source": "Binance /fapi/v1/klines", "rsi": rsi_payload, "latest_closed_15m": vpp_payload["latest_closed_15m"], **baselines},
    }


def _momentum_baselines(rows: list[list[Any]]) -> dict[str, Any]:
    summaries = [kline_summary(row) for row in rows]
    vpps = [float(row["vpp_by_close"]) for row in summaries if row.get("vpp_by_close") is not None]
    latest = summaries[-1] if summaries else {}
    zscore = _zscore(float(latest.get("vpp_by_close") or 0), vpps) if len(vpps) >= 20 else None
    return {
        "vpp_baseline_24h": {
            "zscore": zscore,
            "sample_count": len(vpps),
            "latest_pct_change": latest.get("pct_change"),
            "note": None if zscore is not None else "insufficient 15m VPP samples",
        },
        "liquidity_buckets_24h": {"bucket_usd": 250, "buckets": _volume_buckets(summaries, 250)},
    }


def _volume_buckets(summaries: list[dict[str, Any]], bucket_usd: int) -> list[dict[str, Any]]:
    buckets: dict[int, float] = {}
    for row in summaries:
        low = float(row.get("low") or 0)
        high = float(row.get("high") or 0)
        quote_volume = float(row.get("quote_volume_usd") or 0)
        if low <= 0 or high <= 0 or quote_volume <= 0:
            continue
        start = int(min(low, high) // bucket_usd * bucket_usd)
        end = int(max(low, high) // bucket_usd * bucket_usd)
        covered = list(range(start, end + bucket_usd, bucket_usd))
        share = quote_volume / max(1, len(covered))
        for bucket in covered:
            buckets[bucket] = buckets.get(bucket, 0.0) + share
    return [{"price_bucket": bucket, "quote_volume_usd": value} for bucket, value in sorted(buckets.items())]


def _zscore(value: float, values: list[float]) -> float | None:
    std = pstdev(values)
    return None if std <= 1e-9 else (value - fmean(values)) / std
