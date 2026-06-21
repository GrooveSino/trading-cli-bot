from __future__ import annotations

from typing import Any

from trading_gateway.application.market.btcusdt.shared import BINANCE_FAPI, OKX_API, OKX_INST_ID, SYMBOL, HttpClient, cst_datetime


def collect_funding_basis(client: HttpClient) -> dict[str, Any]:
    premium = client.get_json(f"{BINANCE_FAPI}/fapi/v1/premiumIndex", {"symbol": SYMBOL})
    mark = float(premium["markPrice"])
    index = float(premium["indexPrice"])
    basis = mark - index
    okx = client.get_json(f"{OKX_API}/api/v5/public/funding-rate", {"instId": OKX_INST_ID})["data"][0]
    return {
        "source": "Binance /fapi/v1/premiumIndex + OKX /api/v5/public/funding-rate",
        "binance": {
            "mark_price": mark,
            "index_price": index,
            "basis_usd": basis,
            "basis_bps": basis / index * 10000 if index else None,
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


def collect_top_trader_delta(client: HttpClient) -> dict[str, Any]:
    rows = client.get_json(f"{BINANCE_FAPI}/futures/data/topLongShortPositionRatio", {"symbol": SYMBOL, "period": "5m", "limit": "13"})
    return {
        "source": "Binance /futures/data/topLongShortPositionRatio",
        "current": _ratio_view(rows[-1]),
        "delta": {
            "15m": _ratio_delta(rows, 3),
            "1h": _ratio_delta(rows, 12),
        },
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
