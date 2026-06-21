from __future__ import annotations

import asyncio
import json
import time
from typing import Any

from trading_gateway.app.config import get_gateway_config
from trading_gateway.application.market.btcusdt import build_btcusdt_snapshot
from trading_gateway.application.market.btcusdt.features import build_llm_feature_vectors
from trading_gateway.application.market.btcusdt.shared import HttpClient, cst_datetime
from trading_gateway.application.market.okx_btc_account import add_okx_account_counts, collect_okx_btc_account
from trading_gateway.support.redaction import redact_text

from .cache import SectionCache
from .enrichment import collect_funding_basis, collect_top_trader_delta
from .liquidations import force_order_messages, parse_force_order, utc_ms
from .storage import BtcusdtMarketDataStore, write_snapshot

BINANCE_FORCE_ORDER_WS = "wss://fstream.binance.com/ws/!forceOrder@arr"


def run_btcusdt_marketdata_daemon(*, once: bool = False, json_output: bool = False) -> dict[str, Any] | None:
    config = get_gateway_config().btcusdt_marketdata
    store = BtcusdtMarketDataStore(config.sqlite_path)
    try:
        if once:
            payload = build_cloud_snapshot(store)
            write_snapshot(config.snapshot_path, payload)
            if json_output:
                print(json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True))
            return payload
        asyncio.run(_run_forever(store))
        return None
    finally:
        store.close()


async def _run_forever(store: BtcusdtMarketDataStore) -> None:
    cache = SectionCache()
    stream_status = _new_liquidation_status()
    tasks = [asyncio.create_task(_liquidation_loop(store, stream_status)), asyncio.create_task(_snapshot_loop(store, cache, stream_status))]
    try:
        await asyncio.gather(*tasks)
    finally:
        for task in tasks:
            task.cancel()


async def _snapshot_loop(store: BtcusdtMarketDataStore, cache: SectionCache, stream_status: dict[str, Any]) -> None:
    config = get_gateway_config().btcusdt_marketdata
    next_prune = 0.0
    while True:
        now = time.time()
        payload = build_cloud_snapshot(store, cache, stream_status)
        write_snapshot(config.snapshot_path, payload)
        if now >= next_prune:
            prune = prune_store(store)
            payload["storage_prune"] = prune
            write_snapshot(config.snapshot_path, payload)
            next_prune = now + config.prune_interval_sec
        await asyncio.sleep(config.poll_interval_sec)


async def _liquidation_loop(store: BtcusdtMarketDataStore, stream_status: dict[str, Any]) -> None:
    while True:
        try:
            await _consume_liquidations(store, stream_status)
        except Exception as exc:  # noqa: BLE001 - long-running collector must reconnect.
            stream_status.update({"status": "reconnecting", "last_error": f"{type(exc).__name__}: {redact_text(exc)}", "last_error_ms": utc_ms()})
            print(f"[btcusdt-marketdata] liquidation stream reconnect after {redact_text(exc)}", flush=True)
            await asyncio.sleep(3)


async def _consume_liquidations(store: BtcusdtMarketDataStore, stream_status: dict[str, Any]) -> None:
    import websockets

    async with websockets.connect(BINANCE_FORCE_ORDER_WS, ping_interval=20, ping_timeout=20) as ws:
        stream_status.update({"status": "connected", "connected_ms": utc_ms(), "url": BINANCE_FORCE_ORDER_WS})
        stream_status.pop("last_error", None)
        stream_status.pop("last_error_ms", None)
        async for message in ws:
            stream_status["last_message_ms"] = utc_ms()
            for row in force_order_messages(json.loads(message)):
                stream_status["total_force_order_messages"] = int(stream_status.get("total_force_order_messages") or 0) + 1
                event = parse_force_order(row)
                if event["symbol"] != "BTCUSDT":
                    continue
                stream_status["btc_force_order_messages"] = int(stream_status.get("btc_force_order_messages") or 0) + 1
                if event["liquidated_side"] == "unknown" or event["notional_usd"] <= 0:
                    continue
                store.record_liquidation(event)
                stream_status["last_btc_event_ms"] = int(event["event_ms"])


def build_cloud_snapshot(store: BtcusdtMarketDataStore, cache: SectionCache | None = None, stream_status: dict[str, Any] | None = None) -> dict[str, Any]:
    now_ms = utc_ms()
    config = get_gateway_config().btcusdt_marketdata
    cache = cache or SectionCache()
    public = cache.get_or_refresh(
        section="public_snapshot",
        store=store,
        now_ms=now_ms,
        refresh_sec=config.public_refresh_interval_sec,
        collect=lambda: build_btcusdt_snapshot(now_ms=now_ms, include_okx_account=False),
        accept=_public_snapshot_ok,
        default=_minimal_public_snapshot(now_ms),
    )
    snapshot = dict(public.payload)
    snapshot["snapshot_time_ms"] = now_ms
    snapshot["snapshot_time_cst"] = cst_datetime(now_ms)
    snapshot["source_mode"] = "tokyo_marketdata_appliance"
    snapshot["section_cache"] = {"public_snapshot": public.status}
    _attach_enrichment(snapshot, store, cache, now_ms)
    snapshot["liquidation_density_24h"] = store.liquidation_density_24h(bucket_usd=config.liquidation_bucket_usd, now_ms=now_ms, stream_status=_liquidation_status_view(stream_status, now_ms))
    account = cache.get_or_refresh(
        section="okx_account",
        store=store,
        now_ms=now_ms,
        refresh_sec=config.account_refresh_interval_sec,
        collect=_safe_okx_account,
        accept=lambda payload: payload.get("status") == "ok",
        default={"source": "OKX private account/trade API", "status": "error", "counts": {"positions": 0, "open_orders": 0, "algo_orders": 0}},
    )
    snapshot["okx_account"] = account.payload
    snapshot["section_cache"]["okx_account"] = account.status
    snapshot["llm_feature_vectors"] = build_llm_feature_vectors(snapshot, basis_history_bps=store.basis_history_bps(since_ms=now_ms - 24 * 60 * 60 * 1000))
    snapshot["readings"] = _cloud_readings(snapshot)
    return snapshot


def _attach_enrichment(snapshot: dict[str, Any], store: BtcusdtMarketDataStore, cache: SectionCache, now_ms: int) -> None:
    config = get_gateway_config().btcusdt_marketdata
    funding = cache.get_or_refresh(
        section="funding_basis",
        store=store,
        now_ms=now_ms,
        refresh_sec=config.enrichment_refresh_interval_sec,
        collect=_collect_funding_basis,
        default={"source": "Binance/OKX funding APIs", "status": "error"},
    )
    top_delta = cache.get_or_refresh(
        section="top_trader_position_delta",
        store=store,
        now_ms=now_ms,
        refresh_sec=config.enrichment_refresh_interval_sec,
        collect=_collect_top_trader_delta,
        default={"source": "Binance topLongShortPositionRatio", "status": "error"},
    )
    snapshot["funding_basis"] = funding.payload
    snapshot["top_trader_position_delta"] = top_delta.payload
    snapshot.setdefault("section_cache", {})["funding_basis"] = funding.status
    snapshot["section_cache"]["top_trader_position_delta"] = top_delta.status
    snapshot["generated_ms"] = now_ms
    snapshot["generated_at_cst"] = cst_datetime(now_ms)


def _collect_funding_basis() -> dict[str, Any]:
    http = HttpClient()
    try:
        return collect_funding_basis(http)
    finally:
        http.close()


def _collect_top_trader_delta() -> dict[str, Any]:
    http = HttpClient()
    try:
        return collect_top_trader_delta(http)
    finally:
        http.close()


def _safe_okx_account() -> dict[str, Any]:
    try:
        return add_okx_account_counts(collect_okx_btc_account())
    except Exception as exc:  # noqa: BLE001
        return {"source": "OKX private account/trade API", "status": "error", "error": f"{type(exc).__name__}: {redact_text(exc)}"}


def _public_snapshot_ok(payload: dict[str, Any]) -> bool:
    okx = payload.get("okx_market") or {}
    momentum = payload.get("binance_momentum") or {}
    return bool(okx.get("last") and okx.get("orderbook_geometry") and momentum.get("vpp_baseline_24h") and momentum.get("liquidity_buckets_24h") and payload.get("binance_ofi_3m"))


def _new_liquidation_status() -> dict[str, Any]:
    return {
        "status": "starting",
        "url": BINANCE_FORCE_ORDER_WS,
        "started_ms": utc_ms(),
        "total_force_order_messages": 0,
        "btc_force_order_messages": 0,
    }


def _liquidation_status_view(stream_status: dict[str, Any] | None, now_ms: int) -> dict[str, Any]:
    status = dict(stream_status or _new_liquidation_status())
    for key in ("started_ms", "connected_ms", "last_message_ms", "last_btc_event_ms", "last_error_ms"):
        if status.get(key):
            status[key.replace("_ms", "_cst")] = cst_datetime(int(status[key]))
    started = int(status.get("started_ms") or now_ms)
    status["uptime_sec"] = max(0.0, (now_ms - started) / 1000)
    return status


def _minimal_public_snapshot(now_ms: int) -> dict[str, Any]:
    return {
        "mode": "btcusdt_market_snapshot",
        "symbol": "BTCUSDT",
        "okx_inst_id": "BTC-USDT-SWAP",
        "snapshot_time_cst": cst_datetime(now_ms),
        "data_sources": ["OKX public market API", "Binance USD-M Futures public API"],
        "errors": {"public_snapshot": "initial public collector failed"},
        "collector_timings_ms": {},
        "fetch_strategy": {"http": "degraded"},
        "readings": [],
    }


def prune_store(store: BtcusdtMarketDataStore) -> dict[str, Any]:
    config = get_gateway_config().btcusdt_marketdata
    return store.prune(
        now_ms=utc_ms(),
        liquidation_retention_ms=int(config.retention_liquidation_hours * 60 * 60 * 1000),
        summary_retention_ms=int(config.retention_summary_hours * 60 * 60 * 1000),
        max_bytes=config.max_storage_bytes,
        managed_dirs=[config.sqlite_path.parent, config.snapshot_path.parent],
    )


def _cloud_readings(snapshot: dict[str, Any]) -> list[str]:
    readings = list(snapshot.get("readings") or [])
    liq = snapshot.get("liquidation_density_24h") or {}
    readings.insert(0, f"24h 已发生强平：多头 {liq.get('long_liq_usd', 0):.0f}U，空头 {liq.get('short_liq_usd', 0):.0f}U；这不是未来清算热力图。")
    funding = ((snapshot.get("funding_basis") or {}).get("binance") or {})
    if funding:
        readings.insert(1, f"Binance basis {funding.get('basis_usd', 0):.2f}U / {funding.get('basis_bps', 0):.2f}bps，last funding {funding.get('last_funding_rate', 0):.6f}。")
    return readings[:6]
