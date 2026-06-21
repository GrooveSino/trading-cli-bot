from __future__ import annotations

import time
from concurrent.futures import ThreadPoolExecutor
from typing import Any

from trading_gateway.application.market.btcusdt.features import build_llm_feature_vectors
from trading_gateway.application.market.btcusdt.rendering import build_readings, render_markdown
from trading_gateway.application.market.btcusdt.shared import HttpClient, cst_datetime
from trading_gateway.application.market.specs import get_market_spec, get_venue_profile

from .account import collect_account_overlay
from .derivatives import collect_global_derivatives
from .public import collect_public_market


def build_market_snapshot(
    venue: str,
    symbol: str,
    client: HttpClient | None = None,
    *,
    now_ms: int | None = None,
    include_account: bool = True,
    global_derivatives: dict[str, Any] | None = None,
) -> dict[str, Any]:
    spec = get_market_spec(symbol)
    profile = get_venue_profile(venue)
    http = client or HttpClient()
    close_http = client is None and hasattr(http, "close")
    started_ms = now_ms or int(time.time() * 1000)
    payload = _base_payload(profile, spec, started_ms, http)
    collectors = {
        "market_source": lambda: collect_public_market(http, profile, spec),
    }
    if global_derivatives is None:
        collectors["global_derivatives"] = lambda: collect_global_derivatives(http, spec, started_ms)
    else:
        payload["global_derivatives"] = global_derivatives
    if include_account:
        collectors["account_overlay"] = lambda: collect_account_overlay(profile, spec)
    try:
        with ThreadPoolExecutor(max_workers=len(collectors), thread_name_prefix="market-snapshot") as pool:
            futures = {name: pool.submit(_timed, fn) for name, fn in collectors.items()}
            for name, future in futures.items():
                result = future.result()
                payload["collector_timings_ms"][name] = result["elapsed_ms"]
                if result["ok"]:
                    payload[name] = result["value"]
                else:
                    payload["errors"][name] = result["error"]
                    payload[name] = _default_section(name, payload, result["error"])
        _attach_compatibility(payload)
        payload["llm_feature_vectors"] = build_llm_feature_vectors(payload)
        payload["readings"] = _readings(payload)
        return payload
    finally:
        if close_http:
            http.close()


def render_market_markdown(snapshot: dict[str, Any]) -> str:
    title = f"Venue: {snapshot.get('venue_profile', {}).get('id')} | Symbol: {snapshot.get('symbol')}"
    return f"{title}\n\n" + render_markdown(snapshot)


def _base_payload(profile: Any, spec: Any, started_ms: int, http: HttpClient) -> dict[str, Any]:
    return {
        "mode": "market_snapshot",
        "venue_profile": {"id": profile.id, "exchange": profile.exchange, "account_mode": profile.account_mode, "display_name": profile.display_name},
        "symbol": spec.key,
        "base_asset": spec.base,
        "derivatives_symbol": spec.derivatives_symbol,
        "okx_inst_id": spec.okx_inst_id,
        "account_instrument_label": spec.okx_inst_id,
        "snapshot_time_ms": started_ms,
        "snapshot_time_cst": cst_datetime(started_ms),
        "data_sources": [profile.market_source, "USD-M futures public reference data"],
        "collector_timings_ms": {},
        "fetch_strategy": {"http": getattr(http, "transport_name", "custom_client")},
        "errors": {},
    }


def _timed(fn: Any) -> dict[str, Any]:
    started = time.perf_counter()
    try:
        return {"ok": True, "value": fn(), "elapsed_ms": int((time.perf_counter() - started) * 1000)}
    except Exception as exc:  # noqa: BLE001 - snapshots should degrade section-by-section.
        return {"ok": False, "error": f"{type(exc).__name__}: {exc}", "elapsed_ms": int((time.perf_counter() - started) * 1000)}


def _attach_compatibility(payload: dict[str, Any]) -> None:
    market = payload.get("market_source") or {}
    derivatives = payload.get("global_derivatives") or {}
    payload["okx_market"] = market
    payload["binance_oi"] = derivatives.get("oi") or {}
    payload["binance_trade_flow"] = derivatives.get("trade_flow") or {}
    payload["binance_cvd"] = derivatives.get("cvd") or {}
    payload["binance_whale_flow"] = derivatives.get("whale_flow") or {}
    payload["binance_ofi_3m"] = derivatives.get("ofi_3m") or {}
    payload["binance_ratios"] = derivatives.get("ratios") or {}
    payload["binance_momentum"] = derivatives.get("momentum") or {}
    payload["binance_liquidations"] = derivatives.get("liquidations_30m") or {}
    payload["funding_basis"] = derivatives.get("funding_basis") or {}
    payload["top_trader_position_delta"] = derivatives.get("top_trader_position_delta") or {}
    if derivatives.get("fetch_strategy"):
        payload["fetch_strategy"].update(derivatives["fetch_strategy"])
    payload["account_overlay"] = payload.get("account_overlay") or _empty_account_overlay(payload)
    if payload["venue_profile"]["exchange"] == "okx":
        payload["okx_account"] = payload["account_overlay"]
    payload["display_market_label"] = payload["venue_profile"]["display_name"]
    payload["display_account_label"] = f"{payload['venue_profile']['display_name']} {str(payload.get('symbol')).upper()}"


def _default_section(name: str, payload: dict[str, Any], error: str) -> dict[str, Any]:
    if name == "market_source":
        return {
            "status": "error",
            "source": payload.get("venue_profile", {}).get("display_name"),
            "timestamp_cst": payload.get("snapshot_time_cst"),
            "last": None,
            "best_bid": None,
            "best_ask": None,
            "depth_bands": {},
            "top_ask_walls": [],
            "top_bid_walls": [],
            "super_ask_walls": [],
            "key_super_ask_levels": {},
            "orderbook_geometry": {},
            "error": error,
        }
    if name == "global_derivatives":
        return {"status": "error", "source": "USD-M futures public reference data", "error": error}
    if name == "account_overlay":
        account = _empty_account_overlay(payload)
        account.update({"status": "error", "error": error})
        return account
    return {"status": "error", "error": error}


def _empty_account_overlay(payload: dict[str, Any]) -> dict[str, Any]:
    return {
        "source": payload.get("venue_profile", {}).get("id"),
        "status": "skipped",
        "counts": {"positions": 0, "open_orders": 0, "algo_orders": 0},
        "positions": [],
        "open_orders": [],
        "algo_orders": {},
    }


def _readings(payload: dict[str, Any]) -> list[str]:
    venue = payload.get("venue_profile", {}).get("id")
    symbol = str(payload.get("symbol") or "").upper()
    prefix = f"{venue} {symbol}: market uses {payload.get('market_source', {}).get('source', '-')}; account overlay={payload.get('account_overlay', {}).get('status', '-')}"
    return [prefix, *build_readings(payload)][:6]
