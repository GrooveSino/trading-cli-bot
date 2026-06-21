from __future__ import annotations

from typing import Any

from trading_gateway.application.market.btcusdt.binance_public import BINANCE_FAPI, collect_liquidations, collect_long_short_ratios, collect_open_interest
from trading_gateway.application.market.btcusdt.momentum import collect_momentum_bundle
from trading_gateway.application.market.btcusdt.shared import OKX_API, HttpClient, cst_datetime
from trading_gateway.application.market.btcusdt.trade_flow import collect_trade_flow
from trading_gateway.application.market.specs import MarketSpec


def collect_global_derivatives(client: HttpClient, spec: MarketSpec, now_ms: int) -> dict[str, Any]:
    symbol = spec.derivatives_symbol
    trade = collect_trade_flow(client, now_ms, symbol=symbol)
    momentum = collect_momentum_bundle(client, symbol=symbol)
    return {
        "source": "USD-M futures public reference data",
        "symbol": symbol,
        "oi": collect_open_interest(client, symbol=symbol),
        "trade_flow": trade["binance_trade_flow"],
        "cvd": trade["binance_cvd"],
        "whale_flow": trade["binance_whale_flow"],
        "ofi_3m": trade["binance_ofi_3m"],
        "ratios": collect_long_short_ratios(client, symbol=symbol),
        "momentum": momentum["binance_momentum"],
        "liquidations_30m": collect_liquidations(client, now_ms, symbol=symbol),
        "funding_basis": _funding_basis(client, spec),
        "top_trader_position_delta": _top_trader_delta(client, symbol),
        "fetch_strategy": {"aggTrades": trade.get("fetch_strategy")},
    }


def _funding_basis(client: HttpClient, spec: MarketSpec) -> dict[str, Any]:
    premium = client.get_json(f"{BINANCE_FAPI}/fapi/v1/premiumIndex", {"symbol": spec.derivatives_symbol})
    mark = float(premium["markPrice"])
    index = float(premium["indexPrice"])
    okx = client.get_json(f"{OKX_API}/api/v5/public/funding-rate", {"instId": spec.okx_inst_id})["data"][0]
    return {
        "source": "USD-M futures premiumIndex + OKX public funding-rate",
        "derivatives": {
            "mark_price": mark,
            "index_price": index,
            "basis_usd": mark - index,
            "basis_bps": (mark - index) / index * 10000 if index else None,
            "last_funding_rate": float(premium.get("lastFundingRate") or 0),
            "next_funding_time_cst": cst_datetime(int(premium.get("nextFundingTime") or 0)),
            "timestamp_cst": cst_datetime(int(premium.get("time") or 0)),
        },
        "binance": {
            "mark_price": mark,
            "index_price": index,
            "basis_usd": mark - index,
            "basis_bps": (mark - index) / index * 10000 if index else None,
            "last_funding_rate": float(premium.get("lastFundingRate") or 0),
            "next_funding_time_cst": cst_datetime(int(premium.get("nextFundingTime") or 0)),
            "timestamp_cst": cst_datetime(int(premium.get("time") or 0)),
        },
        "okx": {
            "funding_rate": float(okx.get("fundingRate") or 0),
            "next_funding_rate": float(okx.get("nextFundingRate") or 0),
            "funding_time_cst": cst_datetime(int(okx.get("fundingTime") or 0)),
            "next_funding_time_cst": cst_datetime(int(okx.get("nextFundingTime") or 0)),
        },
    }


def _top_trader_delta(client: HttpClient, symbol: str) -> dict[str, Any]:
    rows = client.get_json(f"{BINANCE_FAPI}/futures/data/topLongShortPositionRatio", {"symbol": symbol, "period": "5m", "limit": "13"})
    return {
        "source": "USD-M futures topLongShortPositionRatio",
        "current": _ratio_view(rows[-1]),
        "delta": {"15m": _ratio_delta(rows, 3), "1h": _ratio_delta(rows, 12)},
    }


def _ratio_view(row: dict[str, Any]) -> dict[str, Any]:
    return {
        "long_account": float(row["longAccount"]),
        "short_account": float(row["shortAccount"]),
        "long_short_ratio": float(row["longShortRatio"]),
        "timestamp_cst": cst_datetime(int(row["timestamp"])),
    }


def _ratio_delta(rows: list[dict[str, Any]], steps: int) -> dict[str, Any]:
    current = _ratio_view(rows[-1])
    previous = _ratio_view(rows[-1 - steps])
    return {
        "long_account_delta": current["long_account"] - previous["long_account"],
        "short_account_delta": current["short_account"] - previous["short_account"],
        "long_short_ratio_delta": current["long_short_ratio"] - previous["long_short_ratio"],
        "from_cst": previous["timestamp_cst"],
        "to_cst": current["timestamp_cst"],
    }
