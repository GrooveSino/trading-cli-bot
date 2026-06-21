from __future__ import annotations

from copy import deepcopy
from typing import Any

DERIVATIVE_KEY_MAP = {
    "binance_oi": "derivatives_oi",
    "binance_trade_flow": "derivatives_trade_flow",
    "binance_cvd": "derivatives_cvd",
    "binance_whale_flow": "derivatives_whale_flow",
    "binance_ofi_3m": "derivatives_ofi_3m",
    "binance_ratios": "derivatives_long_short_ratios",
    "binance_momentum": "derivatives_momentum",
    "binance_rsi": "derivatives_rsi",
    "binance_vpp": "derivatives_vpp",
    "binance_liquidations": "derivatives_liquidations_30m",
}

NESTED_KEY_MAP = {
    "binance_liquidations": "derivatives_liquidations_30m",
    "binance_momentum_bundle": "derivatives_momentum_bundle",
    "binance_oi": "derivatives_oi",
    "binance_ratios": "derivatives_long_short_ratios",
    "binance_trade_flow": "derivatives_trade_flow",
}

SOURCE_REPLACEMENTS = (
    ("Binance USD-M Futures public API", "USD-M futures public market data"),
    ("Binance /fapi/v1/openInterest + /futures/data/openInterestHist", "USD-M futures public open interest endpoints"),
    ("Binance futures/data long-short ratio endpoints", "USD-M futures public long/short ratio endpoints"),
    ("Binance /fapi/v1/allForceOrders", "USD-M futures public force-order REST"),
    ("Binance /fapi/v1/aggTrades", "USD-M futures public aggTrades"),
    ("Binance /fapi/v1/klines", "USD-M futures public klines"),
    ("Binance /fapi/v1/premiumIndex + OKX /api/v5/public/funding-rate", "USD-M futures premiumIndex + OKX public funding-rate"),
    ("Binance !forceOrder@arr websocket filtered to BTCUSDT", "USD-M futures forceOrder websocket filtered to BTCUSDT"),
    ("Binance topLongShortPositionRatio", "USD-M futures topLongShortPositionRatio"),
    ("Binance/OKX funding APIs", "USD-M futures/OKX funding APIs"),
    ("Binance basis_bps", "USD-M futures basis_bps"),
    ("Binance aggTrades OFI", "USD-M futures aggTrades OFI"),
    ("Binance", "USD-M futures"),
    ("binance", "derivatives"),
)


def market_snapshot_public_view(snapshot: dict[str, Any]) -> dict[str, Any]:
    view: dict[str, Any] = {}
    for key, value in snapshot.items():
        mapped = DERIVATIVE_KEY_MAP.get(key, key)
        if key.startswith("binance_") and key not in DERIVATIVE_KEY_MAP:
            continue
        view[mapped] = _sanitize_text(deepcopy(value))
    view["data_sources"] = _public_data_sources(view.get("data_sources"))
    _sanitize_nested_sections(view)
    view["display_mode"] = "okx_primary_market_snapshot"
    return view


def _sanitize_nested_sections(view: dict[str, Any]) -> None:
    timings = view.get("collector_timings_ms")
    if isinstance(timings, dict):
        view["collector_timings_ms"] = {NESTED_KEY_MAP.get(key, key): value for key, value in timings.items()}
    funding = view.get("funding_basis")
    if isinstance(funding, dict) and "binance" in funding:
        funding["derivatives"] = funding.pop("binance")
    features = (view.get("llm_feature_vectors") or {}).get("features") or {}
    _replace_evidence(features)
    strategy = view.get("fetch_strategy") or {}
    if "aggTrades" in strategy:
        strategy["aggTrades"] = _sanitize_text(strategy["aggTrades"])
    density = view.get("liquidation_density_24h")
    if isinstance(density, dict):
        density["source"] = _sanitize_text(density.get("source"))
    top_delta = view.get("top_trader_position_delta")
    if isinstance(top_delta, dict):
        top_delta["source"] = _sanitize_text(top_delta.get("source"))


def _replace_evidence(features: dict[str, Any]) -> None:
    for feature in features.values():
        if isinstance(feature, dict) and "evidence" in feature:
            feature["evidence"] = _sanitize_text(feature.get("evidence"))


def _public_data_sources(raw: Any) -> list[str]:
    sources = raw if isinstance(raw, list) else []
    sanitized = [_sanitize_text(item) for item in sources]
    result = []
    for item in [*sanitized, "OKX public market API"]:
        if item and item not in result:
            result.append(str(item))
    return result


def _sanitize_text(value: Any) -> Any:
    if isinstance(value, str):
        text = value
        for old, new in SOURCE_REPLACEMENTS:
            text = text.replace(old, new)
        return text
    if isinstance(value, list):
        return [_sanitize_text(item) for item in value]
    if isinstance(value, dict):
        return {NESTED_KEY_MAP.get(key, key): _sanitize_text(item) for key, item in value.items()}
    return value
