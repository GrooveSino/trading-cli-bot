from __future__ import annotations

import math
from statistics import fmean, median, pstdev
from typing import Any

SCHEMA_VERSION = "btcusdt_llm_features.v1"


def build_llm_feature_vectors(snapshot: dict[str, Any], *, basis_history_bps: list[float] | None = None) -> dict[str, Any]:
    features = {
        "oi_acceleration": _oi_acceleration(snapshot),
        "cvd_log_skew": _cvd_log_skew(snapshot),
        "order_flow_imbalance_3m": _order_flow_imbalance(snapshot),
        "whale_flow": _whale_flow(snapshot),
        "weighted_orderbook_gravity": _orderbook_gravity(snapshot),
        "liquidity_vacuum_down": _liquidity_vacuum_down(snapshot),
        "basis_zscore": _basis_zscore(snapshot, basis_history_bps or []),
        "vpp_anomaly_zscore": _vpp_anomaly(snapshot),
        "squeeze_coefficient": _squeeze_coefficient(snapshot),
        "realized_liquidation_24h": _realized_liq(snapshot),
    }
    data_quality = _data_quality(snapshot, features)
    flags = _semantic_flags(snapshot, features, data_quality)
    return {
        "schema_version": SCHEMA_VERSION,
        "market_state": _market_state(flags, data_quality),
        "confidence": _confidence(flags, data_quality),
        "features": features,
        "semantic_flags": flags,
        "data_quality": data_quality,
    }


def _oi_acceleration(snapshot: dict[str, Any]) -> dict[str, Any]:
    delta = (snapshot.get("binance_oi") or {}).get("delta") or {}
    d5 = _num((delta.get("5m") or {}).get("btc_delta"))
    d15 = _num((delta.get("15m") or {}).get("btc_delta"))
    if d5 is None or d15 is None:
        return _feature(None, "missing", "需要 5m/15m OI delta")
    baseline = d15 / 3
    if abs(baseline) < 1e-9:
        return _feature(None, "insufficient", "15m OI baseline too small")
    return _feature(d5 / baseline, _tier_abs(d5 / baseline, 1.5, 3.0), f"5m={d5:.3f} BTC; 15m/3={baseline:.3f} BTC")


def _cvd_log_skew(snapshot: dict[str, Any]) -> dict[str, Any]:
    cvd = snapshot.get("binance_cvd") or {}
    buy = _num(cvd.get("taker_buy_usd"))
    sell = _num(cvd.get("taker_sell_usd"))
    if buy is None or sell is None or buy <= 0 or sell <= 0:
        return _feature(None, "missing", "需要 taker buy/sell notional")
    value = math.log(buy / sell)
    return _feature(value, _signed_tier(value, 0.25, 0.5), f"buy={buy:.0f}; sell={sell:.0f}")


def _order_flow_imbalance(snapshot: dict[str, Any]) -> dict[str, Any]:
    ofi = snapshot.get("binance_ofi_3m") or {}
    value = _num(ofi.get("log_skew"))
    if value is None:
        return _feature(None, "missing", "需要 3m Binance aggTrades OFI")
    evidence = (
        f"buy_share={_fmt_share(ofi.get('buy_share'))}; sell_share={_fmt_share(ofi.get('sell_share'))}; "
        f"delta={_num(ofi.get('delta_usd')) or 0:.0f}; trades={ofi.get('agg_trade_count', 0)}; "
        f"px_change={_num(ofi.get('price_change_pct')) or 0:.4f}%"
    )
    return _feature(value, ofi.get("tier") or _signed_tier(value, 0.7, 1.7), evidence)


def _whale_flow(snapshot: dict[str, Any]) -> dict[str, Any]:
    whale = ((snapshot.get("binance_cvd") or {}).get("whale_over_100k") or {})
    delta = _num(whale.get("delta_usd"))
    if delta is None:
        return _feature(None, "missing", "需要 >$100K whale flow")
    return _feature(delta, _signed_tier(delta, 1_000_000, 3_000_000), f"buy_count={whale.get('buy_count', 0)}; sell_count={whale.get('sell_count', 0)}")


def _orderbook_gravity(snapshot: dict[str, Any]) -> dict[str, Any]:
    geometry = ((snapshot.get("okx_market") or {}).get("orderbook_geometry") or {})
    value = _num(geometry.get("weighted_gravity"))
    if value is None:
        return _feature(None, "missing", "需要 OKX orderbook_geometry")
    return _feature(value, _signed_tier(value, 0.15, 0.35), f"weighted_bid={geometry.get('weighted_bid')}; weighted_ask={geometry.get('weighted_ask')}")


def _liquidity_vacuum_down(snapshot: dict[str, Any]) -> dict[str, Any]:
    okx_last = _num((snapshot.get("okx_market") or {}).get("last"))
    buckets = (((snapshot.get("binance_momentum") or {}).get("liquidity_buckets_24h") or {}).get("buckets") or [])
    if okx_last is None or not buckets:
        return _feature(None, "insufficient", "需要 OKX last 与 24h 15m range-volume buckets")
    volumes = [_num(row.get("quote_volume_usd")) or 0 for row in buckets]
    med = median(volumes) if volumes else 0
    if med <= 0:
        return _feature(None, "insufficient", "24h volume bucket median is zero")
    bucket_size = 250
    current_bucket = math.floor(okx_last / bucket_size) * bucket_size
    by_bucket = {int(row["price_bucket"]): _num(row.get("quote_volume_usd")) or 0 for row in buckets}
    down_keys = [bucket for bucket in sorted(by_bucket) if bucket < current_bucket]
    down = [by_bucket[bucket] for bucket in down_keys[-4:]]
    if not down:
        return _feature(None, "insufficient", "当前价下方没有已观测 24h range-volume bucket")
    evidence = f"proxy=24h_15m_range_volume; observed_down_buckets={len(down)}; down_avg={fmean(down):.0f}; bucket_median={med:.0f}"
    if len(down) < 4:
        return _feature(None, "insufficient", f"{evidence}; downside sample too sparse, no vacuum conclusion")
    score = 1 - min(1.0, fmean(down) / med)
    return _feature(score, _tier(score, 0.4, 0.65), evidence)


def _basis_zscore(snapshot: dict[str, Any], history: list[float]) -> dict[str, Any]:
    current = _num((((snapshot.get("funding_basis") or {}).get("binance") or {}).get("basis_bps")))
    if current is None:
        return _feature(None, "missing", "需要 Binance basis_bps")
    samples = [value for value in history if value is not None]
    if len(samples) < 20:
        return _feature(None, "insufficient", f"basis history samples={len(samples)}")
    std = pstdev(samples)
    if std <= 1e-9:
        return _feature(None, "insufficient", "basis history stdev is zero")
    value = (current - fmean(samples)) / std
    return _feature(value, _signed_tier(value, 1.5, 2.5), f"current={current:.3f}bps; samples={len(samples)}")


def _vpp_anomaly(snapshot: dict[str, Any]) -> dict[str, Any]:
    baseline = ((snapshot.get("binance_momentum") or {}).get("vpp_baseline_24h") or {})
    value = _num(baseline.get("zscore"))
    if value is None:
        return _feature(None, "insufficient", baseline.get("note") or "需要 24h VPP baseline")
    return _feature(value, _tier(value, 1.5, 2.5), f"sample_count={baseline.get('sample_count')}; latest_pct={baseline.get('latest_pct_change')}")


def _squeeze_coefficient(snapshot: dict[str, Any]) -> dict[str, Any]:
    global_ratio = _num((((snapshot.get("binance_ratios") or {}).get("global_account") or {}).get("longShortRatio")))
    top_delta_1h = _num(((((snapshot.get("top_trader_position_delta") or {}).get("delta") or {}).get("1h") or {}).get("long_short_ratio_delta")))
    current_oi = _num((((snapshot.get("binance_oi") or {}).get("current") or {}).get("btc")))
    oi_1h = _num(((((snapshot.get("binance_oi") or {}).get("delta") or {}).get("1h") or {}).get("btc_delta")))
    if None in (global_ratio, top_delta_1h, current_oi, oi_1h) or not current_oi:
        return _feature(None, "missing", "需要 global ratio、top trader delta、current OI、1h OI delta")
    value = global_ratio * (1 + max(0.0, -top_delta_1h * 100)) * (1 + abs(oi_1h / current_oi) * 100)
    return _feature(value, _tier(value, 2.0, 3.0), f"global_ratio={global_ratio}; top_delta_1h={top_delta_1h}; oi_1h={oi_1h}")


def _realized_liq(snapshot: dict[str, Any]) -> dict[str, Any]:
    liq = snapshot.get("liquidation_density_24h") or {}
    status = liq.get("stream_status") or {}
    value = (_num(liq.get("long_liq_usd")) or 0) - (_num(liq.get("short_liq_usd")) or 0)
    return _feature(value, "info", f"events={liq.get('event_count', 0)}; stream={status.get('status', '-')}; btc_msgs={status.get('btc_force_order_messages', 0)}")


def _semantic_flags(snapshot: dict[str, Any], features: dict[str, Any], data_quality: dict[str, Any]) -> list[dict[str, str]]:
    flags: list[dict[str, str]] = []
    _flag_small_baselines(flags, features)
    basis = _num((((snapshot.get("funding_basis") or {}).get("binance") or {}).get("basis_bps")))
    basis_z = features["basis_zscore"]["value"]
    if (basis is not None and basis < -3) or (basis_z is not None and basis_z < -1.5):
        flags.append(_flag("NEGATIVE_BASIS_COMBAT", "medium", f"basis={basis}; z={basis_z}", data_quality))
    cvd_skew = features["cvd_log_skew"]["value"]
    if cvd_skew is not None and cvd_skew < -0.25:
        flags.append(_flag("CVD_SELL_PRESSURE", "high", f"log_skew={cvd_skew:.3f}", data_quality))
    if cvd_skew is not None and cvd_skew > 0.25:
        flags.append(_flag("CVD_BUY_PRESSURE", "medium", f"log_skew={cvd_skew:.3f}", data_quality))
    _flow_flags(flags, snapshot, features, data_quality)
    return flags


def _flow_flags(flags: list[dict[str, str]], snapshot: dict[str, Any], features: dict[str, Any], data_quality: dict[str, Any]) -> None:
    whale = ((snapshot.get("binance_cvd") or {}).get("whale_over_100k") or {})
    whale_delta = _num(whale.get("delta_usd"))
    if whale_delta is not None and whale_delta < -1_000_000 and int(whale.get("sell_count") or 0) > int(whale.get("buy_count") or 0):
        flags.append(_flag("WHALE_SELL_PRESSURE", "high", f"delta={whale_delta:.0f}", data_quality))
    gravity = features["weighted_orderbook_gravity"]["value"]
    if gravity is not None and gravity < -0.15:
        flags.append(_flag("ASK_GRAVITY", "medium", f"gravity={gravity:.3f}", data_quality))
    if gravity is not None and gravity > 0.15:
        flags.append(_flag("BID_GRAVITY", "medium", f"gravity={gravity:.3f}", data_quality))
    global_ratio = _num((((snapshot.get("binance_ratios") or {}).get("global_account") or {}).get("longShortRatio")))
    if global_ratio is not None and global_ratio > 1.8:
        flags.append(_flag("RETAIL_LONG_CROWDED", "medium", f"global_ratio={global_ratio:.3f}", data_quality))
    top_delta = _num(((((snapshot.get("top_trader_position_delta") or {}).get("delta") or {}).get("1h") or {}).get("long_short_ratio_delta")))
    cvd = features["cvd_log_skew"]["value"]
    if top_delta is not None and cvd is not None and top_delta < -0.002 and cvd < 0:
        flags.append(_flag("SMART_MONEY_DISTRIBUTION", "high", f"top_delta_1h={top_delta:.4f}; cvd_skew={cvd:.3f}", data_quality))
    vacuum = features["liquidity_vacuum_down"]
    if (vacuum["value"] or 0) > 0.65:
        severity = "medium" if vacuum["status"] == "partial" else "high"
        flags.append(_flag("VACUUM_BELOW", severity, vacuum["evidence"], data_quality))
    liq = snapshot.get("liquidation_density_24h") or {}
    if ((liq.get("stream_status") or {}).get("status") == "connected") and int(liq.get("event_count") or 0) == 0:
        flags.append(_flag("REALIZED_LIQ_STREAM_EMPTY", "low", "24h BTCUSDT realized liquidation event_count=0", data_quality))


def _flag_small_baselines(flags: list[dict[str, str]], features: dict[str, Any]) -> None:
    if features["oi_acceleration"]["status"] == "insufficient":
        flags.append({"code": "OI_BASELINE_TOO_SMALL", "severity": "low", "evidence": features["oi_acceleration"]["evidence"], "data_quality": "partial"})
    if features["basis_zscore"]["status"] == "insufficient":
        flags.append({"code": "INSUFFICIENT_BASIS_HISTORY", "severity": "low", "evidence": features["basis_zscore"]["evidence"], "data_quality": "partial"})


def _market_state(flags: list[dict[str, str]], data_quality: dict[str, Any]) -> str:
    codes = {flag["code"] for flag in flags}
    if data_quality["missing_critical"]:
        return "DATA_DEGRADED"
    if {"RETAIL_LONG_CROWDED", "SMART_MONEY_DISTRIBUTION"} <= codes or {"RETAIL_LONG_CROWDED", "VACUUM_BELOW"} <= codes:
        return "LONG_LIQUIDATION_RISK"
    if {"CVD_SELL_PRESSURE", "WHALE_SELL_PRESSURE"} <= codes or "SMART_MONEY_DISTRIBUTION" in codes:
        return "BEARISH_DISTRIBUTION"
    if {"CVD_BUY_PRESSURE", "BID_GRAVITY"} <= codes:
        return "BULLISH_BREAKOUT"
    if "CVD_BUY_PRESSURE" in codes and "NEGATIVE_BASIS_COMBAT" in codes:
        return "SQUEEZE_OR_COVERING"
    return "RANGE_NEUTRAL"


def _confidence(flags: list[dict[str, str]], data_quality: dict[str, Any]) -> float:
    base = 0.45 + min(0.3, len([flag for flag in flags if flag["severity"] in {"medium", "high"}]) * 0.06)
    penalty = len(data_quality["missing_critical"]) * 0.08 + len(data_quality["insufficient_history"]) * 0.03
    return round(max(0.1, min(0.9, base - penalty)), 2)


def _data_quality(snapshot: dict[str, Any], features: dict[str, Any]) -> dict[str, Any]:
    missing = [name for name, payload in features.items() if payload["status"] == "missing"]
    insufficient = [name for name, payload in features.items() if payload["status"] == "insufficient"]
    remote = snapshot.get("remote_snapshot") or {}
    sections = {key: {"status": value.get("status"), "age_sec": value.get("age_sec")} for key, value in (snapshot.get("section_cache") or {}).items()}
    return {
        "remote_age_sec": remote.get("age_sec"),
        "section_cache": sections,
        "missing_critical": missing,
        "insufficient_history": insufficient,
    }


def _flag(code: str, severity: str, evidence: str, data_quality: dict[str, Any]) -> dict[str, str]:
    quality = "partial" if data_quality["missing_critical"] or data_quality["insufficient_history"] else "ok"
    return {"code": code, "severity": severity, "evidence": evidence, "data_quality": quality}


def _feature(value: float | None, status: str, evidence: str) -> dict[str, Any]:
    return {"value": value, "status": status, "tier": status if value is None else status, "evidence": evidence}


def _tier(value: float, watch: float, high: float) -> str:
    return "high" if value >= high else "watch" if value >= watch else "normal"


def _tier_abs(value: float, watch: float, high: float) -> str:
    return _tier(abs(value), watch, high)


def _signed_tier(value: float, watch: float, high: float) -> str:
    mag = abs(value)
    side = "positive" if value > 0 else "negative"
    return f"{side}_{_tier(mag, watch, high)}"


def _num(value: Any) -> float | None:
    try:
        return None if value is None else float(value)
    except (TypeError, ValueError):
        return None


def _fmt_share(value: Any) -> str:
    number = _num(value)
    return "N/A" if number is None else f"{number * 100:.1f}%"
