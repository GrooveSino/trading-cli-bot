from __future__ import annotations

from typing import Any

from trading_gateway.application.market.btcusdt.shared import OKX_API, HttpClient, cst_datetime, depth_band, levels, near_level
from trading_gateway.application.market.specs import MarketSpec, VenueProfile


def collect_public_market(client: HttpClient, venue: VenueProfile, spec: MarketSpec) -> dict[str, Any]:
    if venue.exchange == "okx":
        return _collect_okx(client, spec)
    raise ValueError(f"unsupported venue: {venue.id}")


def _collect_okx(client: HttpClient, spec: MarketSpec) -> dict[str, Any]:
    instrument = client.get_json(f"{OKX_API}/api/v5/public/instruments", {"instType": "SWAP", "instId": spec.okx_inst_id})["data"][0]
    ticker = client.get_json(f"{OKX_API}/api/v5/market/ticker", {"instId": spec.okx_inst_id})["data"][0]
    book = client.get_json(f"{OKX_API}/api/v5/market/books", {"instId": spec.okx_inst_id, "sz": "400"})["data"][0]
    contract_base = float(instrument["ctVal"])
    last = float(ticker["last"])
    asks = levels(book["asks"], contract_base)
    bids = levels(book["bids"], contract_base)
    return _market_payload(
        source="OKX /api/v5/market/books + ticker + instruments",
        timestamp_cst=cst_datetime(int(book["ts"])),
        last=last,
        best_bid=float(ticker["bidPx"]),
        best_ask=float(ticker["askPx"]),
        contract_base=contract_base,
        asks=asks,
        bids=bids,
        key_levels=(62400, 62500, 62800, 63000) if spec.key == "btc" else (3400, 3500, 3600, 3800),
    )


def _market_payload(
    *,
    source: str,
    timestamp_cst: str | None,
    last: float,
    best_bid: float,
    best_ask: float,
    contract_base: float,
    asks: list[dict[str, float]],
    bids: list[dict[str, float]],
    key_levels: tuple[int, ...],
) -> dict[str, Any]:
    ask_walls = sorted([row for row in asks if last <= row["price"] <= last * 1.015], key=lambda row: row["notional_usd"], reverse=True)
    super_asks = [row for row in ask_walls if row["notional_usd"] >= 1_000_000]
    bid_walls = sorted([row for row in bids if last * 0.985 <= row["price"] <= last], key=lambda row: row["notional_usd"], reverse=True)
    return {
        "source": source,
        "timestamp_cst": timestamp_cst,
        "last": last,
        "best_bid": best_bid,
        "best_ask": best_ask,
        "contract_base": contract_base,
        "depth_bands": {label: depth_band(last, asks, bids, pct) for label, pct in [("0.5%", 0.005), ("1.0%", 0.01), ("1.5%", 0.015)]},
        "top_ask_walls": ask_walls[:10],
        "top_bid_walls": bid_walls[:10],
        "super_ask_walls": super_asks,
        "key_super_ask_levels": {str(level): near_level(super_asks, level, tolerance=30.0) for level in key_levels},
        "orderbook_geometry": _geometry(last, asks, bids),
    }


def _geometry(last: float, asks: list[dict[str, float]], bids: list[dict[str, float]]) -> dict[str, Any]:
    weighted_ask = _weighted_side(last, asks, is_ask=True)
    weighted_bid = _weighted_side(last, bids, is_ask=False)
    denom = weighted_bid + weighted_ask
    return {"weighted_bid": weighted_bid, "weighted_ask": weighted_ask, "weighted_gravity": (weighted_bid - weighted_ask) / denom if denom else None, "range": "1.5%"}


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
