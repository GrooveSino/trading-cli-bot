from __future__ import annotations

import json
from concurrent.futures import ThreadPoolExecutor, as_completed
from time import perf_counter
from time import time as epoch_time
from typing import Any

from rich.console import Console
from rich.progress import BarColumn, Progress, SpinnerColumn, TaskProgressColumn, TextColumn, TimeElapsedColumn

from trading_gateway.infrastructure.exchange.factory import build_ccxt_client, close_client
from trading_gateway.domain.models import MARKET_TYPES
from trading_gateway.app.config import get_gateway_config
from trading_gateway.support.formatting import print_json
from trading_gateway.support.redaction import redact_text
from trading_gateway.application.wallet.policy import validate_private_account_exchanges
from trading_gateway.application.wallet.summary_fast import fetch_fast_summary_market
from trading_gateway.application.wallet.summary import build_summary_payload, format_summary_table, summarize_market

def print_wallet_summary(
    exchanges: list[str] | None,
    *,
    json_output: bool,
    progress_enabled: bool = True,
    include_positions: bool = False,
    cache_ttl_sec: float = 0,
    account_mode: str | None = None,
) -> None:
    started = perf_counter()
    picked = validate_private_account_exchanges(exchanges, account_mode)
    cache_key = _cache_key(picked, include_positions, account_mode)
    cached = _read_cache(cache_key, cache_ttl_sec)
    if cached:
        print_json(cached) if json_output else print(format_summary_table(cached))
        return
    tasks = [(exchange, market) for exchange in picked for market in MARKET_TYPES]
    rows = _fetch_rows_parallel(
        tasks,
        progress_enabled=progress_enabled and not json_output,
        include_positions=include_positions,
        account_mode=account_mode,
    )
    payload = build_summary_payload(rows)
    payload["query_ms"] = int((perf_counter() - started) * 1000)
    payload["cache_hit"] = False
    if cache_ttl_sec > 0:
        _write_cache(cache_key, payload)
    print_json(payload) if json_output else print(format_summary_table(payload))


def _fetch_rows_parallel(
    tasks: list[tuple[str, str]],
    *,
    progress_enabled: bool,
    include_positions: bool,
    account_mode: str | None,
) -> list[dict[str, Any]]:
    exchanges = list(dict.fromkeys(exchange for exchange, _market in tasks))
    work_items = _work_items(exchanges, include_positions, account_mode)
    workers = min(6, max(1, len(work_items)))
    rows: list[dict[str, Any]] = []
    console = Console(stderr=True)
    progress = _progress(console, progress_enabled)
    with progress:
        task_id = progress.add_task("[cyan]query wallet balances", total=len(tasks))
        with ThreadPoolExecutor(max_workers=workers, thread_name_prefix="wallet-summary") as pool:
            futures = {pool.submit(fn): label for label, fn in work_items}
            for future in as_completed(futures):
                label = futures[future]
                exchange_rows = future.result()
                rows.extend(exchange_rows)
                progress.update(task_id, advance=len(exchange_rows), description=f"[cyan]done {label}")
    return rows


def _work_items(exchanges: list[str], include_positions: bool, account_mode: str | None) -> list[tuple[str, Any]]:
    items = []
    for exchange in exchanges:
        if exchange in {"gate", "okx"} or include_positions:
            items.append((exchange, lambda exchange=exchange: _fetch_summary_exchange_for_mode(exchange, include_positions, account_mode)))
            continue
        for market in MARKET_TYPES:
            label = f"{exchange} {market}"
            items.append((label, lambda exchange=exchange, market=market: [fetch_summary_market(exchange, market, account_mode=account_mode)]))
    return items


def _fetch_summary_exchange_for_mode(exchange: str, include_positions: bool, account_mode: str | None) -> list[dict[str, Any]]:
    if account_mode is None:
        return fetch_summary_exchange(exchange, include_positions)
    return fetch_summary_exchange(exchange, include_positions, account_mode=account_mode)


def _progress(console: Console, enabled: bool) -> Progress:
    return Progress(
        SpinnerColumn(style="cyan"),
        TextColumn("{task.description}"),
        BarColumn(bar_width=None, complete_style="green", finished_style="green"),
        TaskProgressColumn(),
        TimeElapsedColumn(),
        console=console,
        transient=True,
        disable=not enabled or not console.is_terminal,
    )


def fetch_summary_exchange(exchange: str, include_positions: bool = False, account_mode: str | None = None) -> list[dict[str, Any]]:
    try:
        client = build_ccxt_client(exchange, "spot", require_private=True, account_mode=account_mode, timeout_ms=get_gateway_config().wallet_summary_timeout_ms)
    except Exception as exc:  # noqa: BLE001 - report missing credentials per market.
        return [_error_row(exchange, market, exc) for market in MARKET_TYPES]
    try:
        return [fetch_summary_market_with_client(client, exchange, market, include_positions) for market in MARKET_TYPES]
    finally:
        close_client(client)


def fetch_summary_market(exchange: str, market: str, include_positions: bool = False, account_mode: str | None = None) -> dict[str, Any]:
    try:
        client = build_ccxt_client(exchange, market, require_private=True, account_mode=account_mode, timeout_ms=get_gateway_config().wallet_summary_timeout_ms)
    except Exception as exc:  # noqa: BLE001 - keep other parallel rows alive.
        return _error_row(exchange, market, exc)
    try:
        return fetch_summary_market_with_client(client, exchange, market, include_positions)
    finally:
        close_client(client)


def fetch_summary_market_with_client(client: Any, exchange: str, market: str, include_positions: bool) -> dict[str, Any]:
    started = perf_counter()
    try:
        row = fetch_fast_summary_market(client, exchange, market, include_positions=include_positions)
    except Exception as exc:  # noqa: BLE001 - CLI summary should keep scanning other venues.
        row = _fetch_ccxt_summary(client, exchange, market, exc, include_positions)
    if not include_positions:
        row["positions_count"] = None
    row["query_ms"] = int((perf_counter() - started) * 1000)
    return row


def _fetch_ccxt_summary(
    client: Any,
    exchange: str,
    market: str,
    fast_error: Exception,
    include_positions: bool,
) -> dict[str, Any]:
    if _is_network_error(fast_error):
        return summarize_market(exchange, market, None, error=redact_text(f"{type(fast_error).__name__}: {fast_error}"))
    if client is None:
        return summarize_market(exchange, market, None, error=redact_text(f"{type(fast_error).__name__}: {fast_error}"))
    try:
        balances = client.fetch_balance()
        positions = _safe_fetch_positions(client) if market == "swap" and include_positions else []
        row = summarize_market(exchange, market, balances, positions)
        row["backend"] = "ccxt_fallback"
        return row
    except Exception as exc:  # noqa: BLE001 - keep other venue rows available.
        message = f"{type(fast_error).__name__}: {fast_error}; fallback {type(exc).__name__}: {exc}"
        return summarize_market(exchange, market, None, error=redact_text(message))


def _is_network_error(exc: Exception) -> bool:
    name = type(exc).__name__.lower()
    return "network" in name or "timeout" in name


def _error_row(exchange: str, market: str, exc: Exception) -> dict[str, Any]:
    row = summarize_market(exchange, market, None, error=redact_text(f"{type(exc).__name__}: {exc}"))
    row["positions_count"] = None
    row["query_ms"] = 0
    return row


def _safe_fetch_positions(client: Any) -> list[dict[str, Any]]:
    try:
        return client.fetch_positions() or [] if callable(getattr(client, "fetch_positions", None)) else []
    except Exception:
        return []


def _cache_key(exchanges: list[str], include_positions: bool, account_mode: str | None) -> str:
    return json.dumps({"account_mode": account_mode, "exchanges": exchanges, "include_positions": include_positions}, sort_keys=True)


def _read_cache(cache_key: str, ttl_sec: float) -> dict[str, Any] | None:
    cache_path = get_gateway_config().wallet_summary_cache
    if ttl_sec <= 0 or not cache_path.exists():
        return None
    try:
        cached = json.loads(cache_path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return None
    if cached.get("cache_key") != cache_key:
        return None
    age = epoch_time() - float(cached.get("created_at", 0))
    if age < 0 or age > ttl_sec:
        return None
    payload = cached.get("payload")
    if not isinstance(payload, dict):
        return None
    payload = dict(payload)
    payload["cache_hit"] = True
    payload["cache_age_ms"] = int(age * 1000)
    return payload


def _write_cache(cache_key: str, payload: dict[str, Any]) -> None:
    cache_path = get_gateway_config().wallet_summary_cache
    try:
        cache_path.parent.mkdir(parents=True, exist_ok=True)
        cache_path.write_text(
            json.dumps({"cache_key": cache_key, "created_at": epoch_time(), "payload": payload}, sort_keys=True),
            encoding="utf-8",
        )
    except OSError:
        return
