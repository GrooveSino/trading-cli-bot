from __future__ import annotations

import time
from concurrent.futures import ThreadPoolExecutor
from typing import Any

from trading_gateway.application.market.okx_btc_account import add_okx_account_counts, collect_okx_btc_account

from .binance_public import collect_liquidations, collect_long_short_ratios, collect_open_interest
from .features import build_llm_feature_vectors
from .momentum import collect_momentum_bundle
from .okx_public import collect_okx_market_bundle
from .rendering import build_readings, render_markdown
from .shared import HttpClient, OKX_INST_ID, SYMBOL, cst_datetime
from .trade_flow import collect_trade_flow


def build_btcusdt_snapshot(client: HttpClient | None = None, *, now_ms: int | None = None, include_okx_account: bool = True) -> dict[str, Any]:
    http = client or HttpClient()
    close_http = client is None and hasattr(http, "close")
    started_ms = now_ms or int(time.time() * 1000)
    payload: dict[str, Any] = {
        "mode": "btcusdt_market_snapshot",
        "symbol": SYMBOL,
        "okx_inst_id": OKX_INST_ID,
        "snapshot_time_cst": cst_datetime(started_ms),
        "data_sources": ["OKX public market API", "Binance USD-M Futures public API"],
        "errors": {},
        "collector_timings_ms": {},
        "fetch_strategy": {"http": getattr(http, "transport_name", "custom_client")},
    }
    collectors = {
        "okx_market_bundle": lambda: collect_okx_market_bundle(http),
        "binance_oi": lambda: collect_open_interest(http),
        "binance_trade_flow": lambda: collect_trade_flow(http, started_ms),
        "binance_ratios": lambda: collect_long_short_ratios(http),
        "binance_momentum_bundle": lambda: collect_momentum_bundle(http),
        "binance_liquidations": lambda: collect_liquidations(http, started_ms),
        "collector_cross_check": lambda: {"status": "ok", "note": "Public-data collector groups completed or degraded independently."},
    }
    if include_okx_account:
        collectors["okx_account"] = lambda: add_okx_account_counts(collect_okx_btc_account())
    try:
        with ThreadPoolExecutor(max_workers=max(1, len(collectors)), thread_name_prefix="btcusdt-snapshot") as pool:
            futures = {name: pool.submit(_timed_collect, fn) for name, fn in collectors.items()}
            for name, future in futures.items():
                result = future.result()
                payload["collector_timings_ms"][name] = result["elapsed_ms"]
                if not result["ok"]:
                    payload["errors"][name] = result["error"]
                    continue
                _merge_collector_result(payload, name, result["value"])
        merge_collector_payloads(payload)
        payload["llm_feature_vectors"] = build_llm_feature_vectors(payload)
        payload["readings"] = build_readings(payload)
        return payload
    finally:
        if close_http:
            http.close()


def _timed_collect(fn: Any) -> dict[str, Any]:
    started = time.perf_counter()
    try:
        return {"ok": True, "value": fn(), "elapsed_ms": int((time.perf_counter() - started) * 1000)}
    except Exception as exc:  # noqa: BLE001 - snapshot should degrade by section.
        return {"ok": False, "error": f"{type(exc).__name__}: {exc}", "elapsed_ms": int((time.perf_counter() - started) * 1000)}


def _merge_collector_result(payload: dict[str, Any], name: str, value: Any) -> None:
    if name == "okx_market_bundle":
        payload.update(value)
        return
    if name == "binance_trade_flow":
        payload.update({key: value[key] for key in ("binance_trade_flow", "binance_cvd", "binance_whale_flow", "binance_ofi_3m")})
        payload["fetch_strategy"]["aggTrades"] = value.get("fetch_strategy")
        return
    if name == "binance_momentum_bundle":
        payload.update(value)
        return
    payload[name] = value


def merge_collector_payloads(payload: dict[str, Any]) -> None:
    depth = payload.get("okx_depth_bands") or {}
    walls = payload.get("okx_super_walls") or {}
    geometry = payload.get("okx_orderbook_geometry") or {}
    if depth or walls or geometry:
        payload["okx_market"] = {**depth, **walls, **geometry}
    rsi_payload = payload.get("binance_rsi") or {}
    vpp_payload = payload.get("binance_vpp") or {}
    if rsi_payload or vpp_payload:
        payload["binance_momentum"] = {
            **(payload.get("binance_momentum") or {}),
            "source": "Binance /fapi/v1/klines",
            "rsi": rsi_payload.get("rsi") or {},
            "latest_closed_15m": vpp_payload.get("latest_closed_15m") or {},
        }
