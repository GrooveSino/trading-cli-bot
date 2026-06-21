from __future__ import annotations

import json
import hashlib
import os
import subprocess
import time
import asyncio
from pathlib import Path
from typing import Any
from urllib.error import HTTPError, URLError
from urllib.request import ProxyHandler, Request, build_opener, urlopen

from trading_gateway.app.config import get_gateway_config
from trading_gateway.application.market.multi import build_market_snapshot
from trading_gateway.application.market.multi.derivatives import collect_global_derivatives
from trading_gateway.application.market.specs import get_market_spec, get_venue_profile, snapshot_filename, snapshot_slug, supported_snapshot_pairs
from trading_gateway.application.market.btcusdt.shared import HttpClient
from trading_gateway.application.marketdata.btcusdt.liquidations import force_order_messages, parse_force_order, utc_ms
from trading_gateway.application.marketdata.btcusdt.storage import read_snapshot, write_snapshot
from trading_gateway.application.marketdata.btcusdt.storage import BtcusdtMarketDataStore
from trading_gateway.support.redaction import redact_text

_LAST_FAILURE: str | None = None
BINANCE_FORCE_ORDER_WS = "wss://fstream.binance.com/ws/!forceOrder@arr"


def load_remote_market_snapshot(venue: str, symbol: str, *, remote: bool, max_age_sec: float | None = None) -> dict[str, Any] | None:
    global _LAST_FAILURE
    if not remote:
        _LAST_FAILURE = "remote disabled"
        return None
    config = get_gateway_config().btcusdt_marketdata
    slug = snapshot_slug(venue, symbol)
    max_age = config.max_remote_age_sec if max_age_sec is None else max_age_sec
    cache = Path("var/reports") / f"{slug}-market-snapshot.remote.json"
    cached = _read_cache(cache)
    if cached and _fresh(cached, max_age):
        return _with_status(cached, "cached", cache, 0, "cache")
    started = time.perf_counter()
    fetched = _fetch_http(venue, symbol, cache)
    transport = "http" if fetched else None
    if fetched is None and config.remote_ssh_fallback:
        fetched = _fetch_ssh(venue, symbol, cache)
        transport = "ssh" if fetched else None
    if fetched is None:
        _LAST_FAILURE = "remote unavailable"
        return None
    _LAST_FAILURE = None
    return _with_status(fetched, "fresh", cache, int((time.perf_counter() - started) * 1000), transport)


def remote_failure_reason() -> str | None:
    return _LAST_FAILURE


def run_multi_marketdata_collector(*, once: bool = False, json_output: bool = False, include_account: bool = True) -> dict[str, Any] | None:
    config = get_gateway_config().btcusdt_marketdata
    store = BtcusdtMarketDataStore(config.sqlite_path)
    try:
        if once:
            result = build_multi_marketdata_snapshots(store, include_account=include_account)
            if json_output:
                print(json.dumps(result, ensure_ascii=False, indent=2, sort_keys=True))
            return result
        asyncio.run(_run_forever(store))
        return None
    finally:
        store.close()


def build_multi_marketdata_snapshots(store: BtcusdtMarketDataStore, stream_status: dict[str, Any] | None = None, *, include_account: bool = True) -> dict[str, Any]:
    config = get_gateway_config().btcusdt_marketdata
    now_ms = utc_ms()
    results: dict[str, Any] = {"mode": "multi_marketdata_snapshots", "generated_ms": now_ms, "snapshots": {}}
    http = HttpClient()
    try:
        derivatives = _shared_derivatives(http, now_ms)
        for venue, spec in supported_snapshot_pairs():
            slug = snapshot_slug(venue.id, spec.key)
            payload = _build_hosted_snapshot(store, venue.id, spec.key, now_ms, stream_status, include_account, derivatives.get(spec.key), http)
            path = _snapshot_path(venue.id, spec.key)
            write_snapshot(path, payload)
            if slug == "okx-live-btc":
                write_snapshot(config.snapshot_path, payload)
            results["snapshots"][slug] = {"path": str(path), "status": payload.get("account_overlay", {}).get("status")}
    finally:
        http.close()
    prune = store.prune(
        now_ms=now_ms,
        liquidation_retention_ms=int(config.retention_liquidation_hours * 60 * 60 * 1000),
        summary_retention_ms=int(config.retention_summary_hours * 60 * 60 * 1000),
        max_bytes=config.max_storage_bytes,
        managed_dirs=[config.sqlite_path.parent, config.snapshot_path.parent],
    )
    results["storage_prune"] = prune
    return results


def _shared_derivatives(client: HttpClient, now_ms: int) -> dict[str, dict[str, Any]]:
    payload: dict[str, dict[str, Any]] = {}
    for _, spec in supported_snapshot_pairs():
        if spec.key in payload:
            continue
        try:
            payload[spec.key] = collect_global_derivatives(client, spec, now_ms)
        except Exception as exc:  # noqa: BLE001 - one symbol should not stop all hosted snapshots.
            payload[spec.key] = {"source": "USD-M futures public reference data", "symbol": spec.derivatives_symbol, "error": f"{type(exc).__name__}: {redact_text(exc)}"}
    return payload


def _build_hosted_snapshot(
    store: BtcusdtMarketDataStore,
    venue: str,
    symbol: str,
    now_ms: int,
    stream_status: dict[str, Any] | None,
    include_account: bool,
    derivatives: dict[str, Any] | None,
    client: HttpClient,
) -> dict[str, Any]:
    spec = get_market_spec(symbol)
    slug = snapshot_slug(venue, symbol)
    payload = build_market_snapshot(venue, symbol, client, now_ms=now_ms, include_account=include_account, global_derivatives=derivatives)
    payload["source_mode"] = "tokyo_marketdata_appliance"
    payload["generated_ms"] = now_ms
    payload["generated_at_cst"] = payload.get("snapshot_time_cst")
    payload["liquidation_density_24h"] = store.liquidation_density_24h(
        bucket_usd=get_gateway_config().btcusdt_marketdata.liquidation_bucket_usd,
        now_ms=now_ms,
        stream_status=_stream_view(stream_status, spec.derivatives_symbol, now_ms),
        symbol=spec.derivatives_symbol,
    )
    store.record_snapshot_section(f"public_snapshot:{slug}", payload, now_ms)
    history = store.basis_history_bps(since_ms=now_ms - 24 * 60 * 60 * 1000, section=f"funding_basis:{slug}")
    if payload.get("funding_basis"):
        store.record_snapshot_section(f"funding_basis:{slug}", payload["funding_basis"], now_ms)
    from trading_gateway.application.market.btcusdt.features import build_llm_feature_vectors

    payload["llm_feature_vectors"] = build_llm_feature_vectors(payload, basis_history_bps=history)
    payload["section_cache"] = {"public_snapshot": {"status": "fresh", "age_sec": 0.0}}
    return payload


async def _run_forever(store: BtcusdtMarketDataStore) -> None:
    status = _new_stream_status()
    tasks = [asyncio.create_task(_liquidation_loop(store, status)), asyncio.create_task(_snapshot_loop(store, status))]
    try:
        await asyncio.gather(*tasks)
    finally:
        for task in tasks:
            task.cancel()


async def _snapshot_loop(store: BtcusdtMarketDataStore, stream_status: dict[str, Any]) -> None:
    interval = get_gateway_config().btcusdt_marketdata.poll_interval_sec
    while True:
        build_multi_marketdata_snapshots(store, stream_status, include_account=True)
        await asyncio.sleep(interval)


async def _liquidation_loop(store: BtcusdtMarketDataStore, stream_status: dict[str, Any]) -> None:
    while True:
        try:
            await _consume_liquidations(store, stream_status)
        except Exception as exc:  # noqa: BLE001 - reconnect forever on a hosted collector.
            stream_status.update({"status": "reconnecting", "last_error": f"{type(exc).__name__}: {redact_text(exc)}", "last_error_ms": utc_ms()})
            await asyncio.sleep(3)


async def _consume_liquidations(store: BtcusdtMarketDataStore, stream_status: dict[str, Any]) -> None:
    import websockets

    symbols = {spec.derivatives_symbol for _, spec in supported_snapshot_pairs()}
    async with websockets.connect(BINANCE_FORCE_ORDER_WS, ping_interval=20, ping_timeout=20) as ws:
        stream_status.update({"status": "connected", "connected_ms": utc_ms(), "url": BINANCE_FORCE_ORDER_WS})
        stream_status.pop("last_error", None)
        stream_status.pop("last_error_ms", None)
        async for message in ws:
            stream_status["last_message_ms"] = utc_ms()
            for row in force_order_messages(json.loads(message)):
                event = parse_force_order(row)
                _bump(stream_status, "total_force_order_messages")
                if event["symbol"] not in symbols:
                    continue
                _bump(stream_status.setdefault("messages_by_symbol", {}), event["symbol"])
                if event["liquidated_side"] != "unknown" and event["notional_usd"] > 0:
                    store.record_liquidation(event)
                    stream_status.setdefault("last_event_ms_by_symbol", {})[event["symbol"]] = int(event["event_ms"])


def _new_stream_status() -> dict[str, Any]:
    return {"status": "starting", "url": BINANCE_FORCE_ORDER_WS, "started_ms": utc_ms(), "total_force_order_messages": 0, "messages_by_symbol": {}}


def _stream_view(status: dict[str, Any] | None, symbol: str, now_ms: int) -> dict[str, Any]:
    source = dict(status or _new_stream_status())
    by_symbol = source.get("messages_by_symbol") or {}
    last_by_symbol = source.get("last_event_ms_by_symbol") or {}
    source["symbol"] = symbol
    source["symbol_force_order_messages"] = int(by_symbol.get(symbol) or 0)
    source["btc_force_order_messages"] = source["symbol_force_order_messages"] if symbol == "BTCUSDT" else 0
    if last_by_symbol.get(symbol):
        source["last_symbol_event_ms"] = int(last_by_symbol[symbol])
    started = int(source.get("started_ms") or now_ms)
    source["uptime_sec"] = max(0.0, (now_ms - started) / 1000)
    return source


def _bump(payload: dict[str, Any], key: str) -> None:
    payload[key] = int(payload.get(key) or 0) + 1


def _snapshot_path(venue: str, symbol: str) -> Path:
    return get_gateway_config().btcusdt_marketdata.snapshot_path.parent / snapshot_filename(venue, symbol)


def _fetch_http(venue: str, symbol: str, cache: Path) -> dict[str, Any] | None:
    global _LAST_FAILURE
    config = get_gateway_config().btcusdt_marketdata
    token = (os.getenv(config.remote_http_token_env) or "").strip()
    if not token or not config.remote_http_url:
        _LAST_FAILURE = "http skipped: missing token or remote_http_url"
        return None
    base = config.remote_http_url.rsplit("/snapshot/", 1)[0]
    url = f"{base}/snapshot/{get_venue_profile(venue).id}/{get_market_spec(symbol).key}"
    proxy_url = "" if _is_tailscale_url(url) else config.remote_http_proxy_url
    request = Request(url, headers={"Authorization": f"Bearer {token}", "User-Agent": "TradingGateway/1.0"})
    try:
        with _open_http(request, config.remote_http_timeout_sec, proxy_url) as response:
            payload = json.loads(response.read().decode("utf-8"))
    except (OSError, HTTPError, URLError, json.JSONDecodeError) as exc:
        _LAST_FAILURE = f"http failed: {type(exc).__name__}: {redact_text(exc)}"
        return None
    if not _compatible(payload, venue, symbol):
        _LAST_FAILURE = "http failed: incompatible snapshot"
        return None
    write_snapshot(cache, payload)
    return payload


def _is_tailscale_url(url: str) -> bool:
    return "://100." in url


def _open_http(request: Request, timeout_sec: float, proxy_url: str) -> Any:
    if not proxy_url:
        # Bypass ambient HTTP proxies for tailnet/private URLs.
        return build_opener(ProxyHandler({})).open(request, timeout=timeout_sec)  # noqa: S310 - URL comes from local config.
    return build_opener(ProxyHandler({"http": proxy_url, "https": proxy_url})).open(request, timeout=timeout_sec)


def _fetch_ssh(venue: str, symbol: str, cache: Path) -> dict[str, Any] | None:
    global _LAST_FAILURE
    config = get_gateway_config().btcusdt_marketdata
    slug = snapshot_slug(venue, symbol)
    remote_path = f"~/services/trading-cli-bot/var/reports/{snapshot_filename(venue, symbol)}"
    command = [
        "ssh",
        "-o",
        "BatchMode=yes",
        "-o",
        "ConnectTimeout=3",
        "-o",
        "ConnectionAttempts=1",
        "-o",
        "ServerAliveInterval=2",
        "-o",
        "ServerAliveCountMax=1",
        "-o",
        "ControlMaster=auto",
        "-o",
        "ControlPersist=60",
        "-o",
        f"ControlPath={_control_path(config.remote_host, cache)}",
        config.remote_host,
        f"cat {remote_path}",
    ]
    try:
        result = subprocess.run(command, text=True, capture_output=True, check=False, timeout=6)
    except (OSError, subprocess.TimeoutExpired) as exc:
        _LAST_FAILURE = f"ssh failed: {type(exc).__name__}"
        return None
    if result.returncode != 0:
        _LAST_FAILURE = f"ssh failed: exit {result.returncode}: {redact_text(result.stderr.strip())}"
        return None
    try:
        payload = json.loads(result.stdout)
    except json.JSONDecodeError:
        _LAST_FAILURE = "ssh failed: invalid JSON"
        return None
    if not _compatible(payload, venue, symbol):
        _LAST_FAILURE = "ssh failed: incompatible snapshot"
        return None
    write_snapshot(cache, payload)
    return payload


def _control_path(host: str, cache: Path) -> str:
    digest = hashlib.sha1(f"{host}:{cache}".encode("utf-8")).hexdigest()[:16]  # noqa: S324 - stable local socket name only.
    return f"/tmp/tbot-marketdata-{digest}.sock"


def _read_cache(cache: Path) -> dict[str, Any] | None:
    try:
        return read_snapshot(cache) if cache.exists() else None
    except Exception:
        return None


def _compatible(payload: dict[str, Any], venue: str, symbol: str) -> bool:
    return (payload.get("venue_profile") or {}).get("id") == get_venue_profile(venue).id and payload.get("symbol") == get_market_spec(symbol).key


def _fresh(payload: dict[str, Any], max_age_sec: float) -> bool:
    stamp = payload.get("snapshot_time_ms") or payload.get("generated_ms")
    if stamp is None:
        return False
    return time.time() - int(stamp) / 1000 <= max_age_sec


def _with_status(payload: dict[str, Any], status: str, cache: Path, fetch_ms: int, transport: str | None) -> dict[str, Any]:
    payload = dict(payload)
    stamp = payload.get("snapshot_time_ms") or payload.get("generated_ms")
    age = None if stamp is None else max(0.0, time.time() - int(stamp) / 1000)
    payload["remote_snapshot"] = {"status": status, "transport": transport, "local_cache": str(cache), "age_sec": age, "fetch_ms": fetch_ms}
    return payload
