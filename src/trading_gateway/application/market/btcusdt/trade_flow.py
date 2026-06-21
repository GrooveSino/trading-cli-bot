from __future__ import annotations

import math
from concurrent.futures import ThreadPoolExecutor
from typing import Any

from .shared import BINANCE_FAPI, SYMBOL, HttpClient, cst_datetime


def collect_trade_flow(client: HttpClient, now_ms: int, symbol: str = SYMBOL) -> dict[str, Any]:
    start_ms, end_ms = fifteen_minute_window(now_ms)
    trade_payload = fetch_agg_trades(client, start_ms, end_ms, symbol=symbol)
    trades = trade_payload["trades"]
    cvd_payload = trade_flow_payload(trades)
    whale_payload = whale_flow_payload(trades)
    ofi_payload = order_flow_imbalance_payload(trades, end_ms - 3 * 60 * 1000, end_ms)
    return {
        "binance_trade_flow": {
            "source": "Binance /fapi/v1/aggTrades",
            "window": "15m",
            "from_cst": cvd_payload.get("from_cst"),
            "to_cst": cvd_payload.get("to_cst"),
            "agg_trade_count": len(trades),
            "fetch_strategy": trade_payload["fetch_strategy"],
        },
        "binance_cvd": cvd_payload,
        "binance_whale_flow": whale_payload,
        "binance_ofi_3m": ofi_payload,
        "fetch_strategy": trade_payload["fetch_strategy"],
    }


def collect_cvd(client: HttpClient, now_ms: int, symbol: str = SYMBOL) -> dict[str, Any]:
    start_ms, end_ms = fifteen_minute_window(now_ms)
    return trade_flow_payload(fetch_agg_trades(client, start_ms, end_ms, symbol=symbol)["trades"])


def trade_flow_payload(trades: dict[int, dict[str, Any]]) -> dict[str, Any]:
    buy_usd = sell_usd = buy_btc = sell_btc = 0.0
    whale_buy_usd = whale_sell_usd = 0.0
    whale_buy_count = whale_sell_count = 0
    first_ms = last_ms = None
    for trade in trades.values():
        trade_ms = int(trade["T"])
        first_ms = trade_ms if first_ms is None else min(first_ms, trade_ms)
        last_ms = trade_ms if last_ms is None else max(last_ms, trade_ms)
        price = float(trade["p"])
        qty = float(trade["q"])
        notional = price * qty
        if trade.get("m"):
            sell_usd += notional
            sell_btc += qty
            if notional >= 100_000:
                whale_sell_usd += notional
                whale_sell_count += 1
        else:
            buy_usd += notional
            buy_btc += qty
            if notional >= 100_000:
                whale_buy_usd += notional
                whale_buy_count += 1
    return {
        "source": "Binance /fapi/v1/aggTrades",
        "window": "15m",
        "from_cst": cst_datetime(first_ms) if first_ms else None,
        "to_cst": cst_datetime(last_ms) if last_ms else None,
        "agg_trade_count": len(trades),
        "taker_buy_usd": buy_usd,
        "taker_sell_usd": sell_usd,
        "delta_usd": buy_usd - sell_usd,
        "taker_buy_btc": buy_btc,
        "taker_sell_btc": sell_btc,
        "delta_btc": buy_btc - sell_btc,
        "whale_over_100k": {
            "buy_usd": whale_buy_usd,
            "sell_usd": whale_sell_usd,
            "delta_usd": whale_buy_usd - whale_sell_usd,
            "buy_count": whale_buy_count,
            "sell_count": whale_sell_count,
        },
    }


def collect_whale_flow(client: HttpClient, now_ms: int, symbol: str = SYMBOL) -> dict[str, Any]:
    start_ms, end_ms = fifteen_minute_window(now_ms)
    return whale_flow_payload(fetch_agg_trades(client, start_ms, end_ms, symbol=symbol)["trades"])


def whale_flow_payload(trades: dict[int, dict[str, Any]]) -> dict[str, Any]:
    buy_usd = sell_usd = 0.0
    buy_count = sell_count = 0
    first_ms = last_ms = None
    for trade in trades.values():
        trade_ms = int(trade["T"])
        first_ms = trade_ms if first_ms is None else min(first_ms, trade_ms)
        last_ms = trade_ms if last_ms is None else max(last_ms, trade_ms)
        notional = float(trade["p"]) * float(trade["q"])
        if notional < 100_000:
            continue
        if trade.get("m"):
            sell_usd += notional
            sell_count += 1
        else:
            buy_usd += notional
            buy_count += 1
    return {
        "source": "Binance /fapi/v1/aggTrades",
        "window": "15m",
        "from_cst": cst_datetime(first_ms) if first_ms else None,
        "to_cst": cst_datetime(last_ms) if last_ms else None,
        "whale_over_100k": {"buy_usd": buy_usd, "sell_usd": sell_usd, "delta_usd": buy_usd - sell_usd, "buy_count": buy_count, "sell_count": sell_count},
    }


def order_flow_imbalance_payload(trades: dict[int, dict[str, Any]], start_ms: int, end_ms: int) -> dict[str, Any]:
    window_trades = sorted((trade for trade in trades.values() if start_ms <= int(trade["T"]) <= end_ms), key=lambda trade: (int(trade["T"]), int(trade["a"])))
    buy_usd = sell_usd = buy_btc = sell_btc = 0.0
    for trade in window_trades:
        price = float(trade["p"])
        qty = float(trade["q"])
        notional = price * qty
        if trade.get("m"):
            sell_usd += notional
            sell_btc += qty
        else:
            buy_usd += notional
            buy_btc += qty
    first_price = float(window_trades[0]["p"]) if window_trades else None
    last_price = float(window_trades[-1]["p"]) if window_trades else None
    price_change = (last_price - first_price) if first_price is not None and last_price is not None else None
    total = buy_usd + sell_usd
    return {
        "source": "Binance /fapi/v1/aggTrades",
        "window": "3m",
        "from_cst": cst_datetime(start_ms),
        "to_cst": cst_datetime(end_ms),
        "agg_trade_count": len(window_trades),
        "taker_buy_usd": buy_usd,
        "taker_sell_usd": sell_usd,
        "delta_usd": buy_usd - sell_usd,
        "taker_buy_btc": buy_btc,
        "taker_sell_btc": sell_btc,
        "delta_btc": buy_btc - sell_btc,
        "buy_share": buy_usd / total if total else None,
        "sell_share": sell_usd / total if total else None,
        "log_skew": _ofi_log_skew(buy_usd, sell_usd),
        "tier": _ofi_tier(buy_usd, sell_usd),
        "price_change_usd": price_change,
        "price_change_pct": price_change / first_price * 100 if price_change is not None and first_price else None,
    }


def _ofi_log_skew(buy_usd: float, sell_usd: float) -> float | None:
    if buy_usd > 0 and sell_usd > 0:
        return math.log(buy_usd / sell_usd)
    if buy_usd > 0:
        return 10.0
    if sell_usd > 0:
        return -10.0
    return None


def _ofi_tier(buy_usd: float, sell_usd: float) -> str:
    total = buy_usd + sell_usd
    if total <= 0:
        return "empty"
    buy_share = buy_usd / total
    sell_share = sell_usd / total
    if sell_share >= 0.85:
        return "extreme_sell"
    if buy_share >= 0.85:
        return "extreme_buy"
    if sell_share >= 0.70:
        return "sell_dominant"
    if buy_share >= 0.70:
        return "buy_dominant"
    return "neutral"


def fetch_agg_trades(client: HttpClient, start_ms: int, end_ms: int, symbol: str = SYMBOL) -> dict[str, Any]:
    try:
        trades, strategy = fetch_agg_trades_parallel(client, start_ms, end_ms, symbol=symbol)
        return {"trades": trades, "fetch_strategy": strategy}
    except Exception as exc:  # noqa: BLE001 - preserve completeness through the slower proven path.
        trades, strategy = fetch_agg_trades_serial(client, start_ms, end_ms, symbol=symbol)
        strategy["fallback"] = True
        strategy["fallback_reason"] = f"{type(exc).__name__}: {exc}"
        return {"trades": trades, "fetch_strategy": strategy}


def fifteen_minute_window(now_ms: int) -> tuple[int, int]:
    return now_ms - 15 * 60 * 1000, now_ms


def fetch_agg_trades_parallel(client: HttpClient, start_ms: int, end_ms: int, symbol: str = SYMBOL) -> tuple[dict[int, dict[str, Any]], dict[str, Any]]:
    slices = minute_slices(start_ms, end_ms)
    trades: dict[int, dict[str, Any]] = {}
    total_pages = 0
    workers = min(10, max(1, len(slices)))
    with ThreadPoolExecutor(max_workers=workers, thread_name_prefix="btcusdt-aggtrades") as pool:
        futures = [pool.submit(fetch_agg_trade_slice, client, start, end, symbol) for start, end in slices]
        for future in futures:
            slice_trades, pages = future.result()
            total_pages += pages
            trades.update(slice_trades)
    return trades, {
        "source": "Binance /fapi/v1/aggTrades",
        "mode": "parallel_minute_slices",
        "slices": len(slices),
        "workers": workers,
        "pages": total_pages,
        "fallback": False,
        "dedupe": "aggTradeId",
    }


def fetch_agg_trade_slice(client: HttpClient, start_ms: int, end_ms: int, symbol: str = SYMBOL) -> tuple[dict[int, dict[str, Any]], int]:
    trades: dict[int, dict[str, Any]] = {}
    params: dict[str, Any] = {"symbol": symbol, "startTime": start_ms, "endTime": end_ms, "limit": "1000"}
    pages = 0
    for _ in range(120):
        batch = client.get_json(f"{BINANCE_FAPI}/fapi/v1/aggTrades", params)
        if not batch:
            break
        pages += 1
        for trade in batch:
            trades[int(trade["a"])] = trade
        if len(batch) < 1000:
            break
        last_id = max(int(trade["a"]) for trade in batch)
        params = {"symbol": symbol, "fromId": last_id + 1, "endTime": end_ms, "limit": "1000"}
        last_trade_ms = max(int(trade["T"]) for trade in batch)
        if last_trade_ms > end_ms:
            break
    return {trade_id: trade for trade_id, trade in trades.items() if start_ms <= int(trade["T"]) <= end_ms}, pages


def fetch_agg_trades_serial(client: HttpClient, start_ms: int, end_ms: int, symbol: str = SYMBOL) -> tuple[dict[int, dict[str, Any]], dict[str, Any]]:
    trades: dict[int, dict[str, Any]] = {}
    params: dict[str, Any] = {"symbol": symbol, "startTime": start_ms, "endTime": end_ms, "limit": "1000"}
    pages = 0
    for _ in range(400):
        batch = client.get_json(f"{BINANCE_FAPI}/fapi/v1/aggTrades", params)
        if not batch:
            break
        pages += 1
        for trade in batch:
            trade_ms = int(trade["T"])
            if start_ms <= trade_ms <= end_ms:
                trades[int(trade["a"])] = trade
        if len(batch) < 1000:
            break
        last_id = max(int(trade["a"]) for trade in batch)
        params = {"symbol": symbol, "fromId": last_id + 1, "endTime": end_ms, "limit": "1000"}
        if max(int(trade["T"]) for trade in batch) >= end_ms:
            break
    return trades, {
        "source": "Binance /fapi/v1/aggTrades",
        "mode": "serial_from_id",
        "slices": 1,
        "workers": 1,
        "pages": pages,
        "fallback": False,
        "dedupe": "aggTradeId",
    }


def minute_slices(start_ms: int, end_ms: int) -> list[tuple[int, int]]:
    slices: list[tuple[int, int]] = []
    cursor = start_ms
    minute_ms = 60 * 1000
    while cursor < end_ms:
        stop = min(end_ms, cursor + minute_ms)
        slices.append((cursor, stop))
        cursor = stop
    if not slices:
        slices.append((start_ms, end_ms))
    return slices
