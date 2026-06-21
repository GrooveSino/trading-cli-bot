from __future__ import annotations

from typing import Any

from .shared import HttpClient, OKX_API, OKX_INST_ID, cst_datetime, depth_band, levels, near_level


def okx_book_context(client: HttpClient) -> dict[str, Any]:
    instrument = client.get_json(f"{OKX_API}/api/v5/public/instruments", {"instType": "SWAP", "instId": OKX_INST_ID})["data"][0]
    ticker = client.get_json(f"{OKX_API}/api/v5/market/ticker", {"instId": OKX_INST_ID})["data"][0]
    book = client.get_json(f"{OKX_API}/api/v5/market/books", {"instId": OKX_INST_ID, "sz": "400"})["data"][0]
    contract_btc = float(instrument["ctVal"])
    last = float(ticker["last"])
    asks = levels(book["asks"], contract_btc)
    bids = levels(book["bids"], contract_btc)
    return {"ticker": ticker, "book": book, "contract_btc": contract_btc, "last": last, "asks": asks, "bids": bids}


def collect_okx_market_bundle(client: HttpClient) -> dict[str, Any]:
    ctx = okx_book_context(client)
    depth = okx_depth_payload(ctx)
    walls = okx_wall_payload(ctx)
    geometry = okx_geometry_payload(ctx)
    return {"okx_depth_bands": depth, "okx_super_walls": walls, "okx_orderbook_geometry": geometry, "okx_market": {**depth, **walls, **geometry}}


def collect_okx_depth_bands(client: HttpClient) -> dict[str, Any]:
    ctx = okx_book_context(client)
    return okx_depth_payload(ctx)


def okx_depth_payload(ctx: dict[str, Any]) -> dict[str, Any]:
    last = ctx["last"]
    asks = ctx["asks"]
    bids = ctx["bids"]
    depth = {label: depth_band(last, asks, bids, pct) for label, pct in [("0.5%", 0.005), ("1.0%", 0.01), ("1.5%", 0.015)]}
    bid_walls = sorted([row for row in bids if last * 0.985 <= row["price"] <= last], key=lambda row: row["notional_usd"], reverse=True)
    ticker = ctx["ticker"]
    book = ctx["book"]
    return {
        "source": "OKX /api/v5/market/books + ticker + instruments",
        "timestamp_cst": cst_datetime(int(book["ts"])),
        "last": last,
        "best_ask": float(ticker["askPx"]),
        "best_bid": float(ticker["bidPx"]),
        "contract_btc": ctx["contract_btc"],
        "depth_bands": depth,
        "top_bid_walls": bid_walls[:10],
    }


def collect_okx_super_walls(client: HttpClient) -> dict[str, Any]:
    ctx = okx_book_context(client)
    return okx_wall_payload(ctx)


def okx_wall_payload(ctx: dict[str, Any]) -> dict[str, Any]:
    last = ctx["last"]
    asks = ctx["asks"]
    book = ctx["book"]
    ask_walls = sorted([row for row in asks if last <= row["price"] <= last * 1.015], key=lambda row: row["notional_usd"], reverse=True)
    super_asks = [row for row in ask_walls if row["notional_usd"] >= 1_000_000]
    key_levels = {str(level): near_level(super_asks, level, tolerance=30.0) for level in (62400, 62500, 62800, 63000)}
    return {
        "source": "OKX /api/v5/market/books + ticker + instruments",
        "timestamp_cst": cst_datetime(int(book["ts"])),
        "top_ask_walls": ask_walls[:10],
        "super_ask_walls": super_asks,
        "key_super_ask_levels": key_levels,
    }


def okx_geometry_payload(ctx: dict[str, Any]) -> dict[str, Any]:
    last = float(ctx["last"])
    weighted_ask = _weighted_side(last, ctx["asks"], is_ask=True)
    weighted_bid = _weighted_side(last, ctx["bids"], is_ask=False)
    denom = weighted_bid + weighted_ask
    gravity = (weighted_bid - weighted_ask) / denom if denom else None
    return {"orderbook_geometry": {"weighted_bid": weighted_bid, "weighted_ask": weighted_ask, "weighted_gravity": gravity, "range": "1.5%", "weight": "notional_usd/(distance_bps+1)"}}


def _weighted_side(last: float, rows: list[dict[str, float]], *, is_ask: bool) -> float:
    total = 0.0
    for row in rows:
        price = float(row["price"])
        if is_ask and not (last <= price <= last * 1.015):
            continue
        if not is_ask and not (last * 0.985 <= price <= last):
            continue
        distance_bps = abs(price - last) / last * 10_000 if last else 0
        total += float(row["notional_usd"]) / (distance_bps + 1)
    return total
