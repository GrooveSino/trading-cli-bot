from __future__ import annotations

import json
import urllib.parse
import urllib.request
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from typing import Any

BINANCE_FAPI = "https://fapi.binance.com"
OKX_API = "https://www.okx.com"
SYMBOL = "BTCUSDT"
OKX_INST_ID = "BTC-USDT-SWAP"
CST = timezone(timedelta(hours=8))
DEFAULT_TIMEOUT_SEC = 10.0


@dataclass
class HttpClient:
    timeout_sec: float = DEFAULT_TIMEOUT_SEC

    def __post_init__(self) -> None:
        self._client = None
        self.transport_name = "urllib_no_keepalive"
        try:
            import httpx  # type: ignore[import-not-found]
        except ModuleNotFoundError:
            return
        self._client = httpx.Client(timeout=self.timeout_sec, headers={"User-Agent": "TradingGateway/1.0"})
        self.transport_name = "httpx_keepalive"

    def get_json(self, url: str, params: dict[str, Any] | None = None) -> Any:
        if self._client is not None:
            response = self._client.get(url, params=params)
            response.raise_for_status()
            return response.json()
        if params:
            url = f"{url}?{urllib.parse.urlencode(params)}"
        request = urllib.request.Request(url, headers={"User-Agent": "TradingGateway/1.0"})
        with urllib.request.urlopen(request, timeout=self.timeout_sec) as response:  # noqa: S310 - caller supplies fixed exchange URLs.
            return json.loads(response.read().decode("utf-8"))

    def close(self) -> None:
        if self._client is not None:
            self._client.close()


def levels(rows: list[list[str]], contract_btc: float) -> list[dict[str, float]]:
    return [{"price": float(price), "contracts": float(size), "notional_usd": float(price) * float(size) * contract_btc} for price, size, *_ in rows]


def depth_band(last: float, asks: list[dict[str, float]], bids: list[dict[str, float]], pct_value: float) -> dict[str, float | None | bool]:
    ask = sum(row["notional_usd"] for row in asks if last <= row["price"] <= last * (1 + pct_value))
    bid = sum(row["notional_usd"] for row in bids if last * (1 - pct_value) <= row["price"] <= last)
    ask_cover = (max((row["price"] for row in asks), default=last) - last) / last if last else 0
    bid_cover = (last - min((row["price"] for row in bids), default=last)) / last if last else 0
    return {
        "ask_notional_usd": ask,
        "bid_notional_usd": bid,
        "bid_ask_ratio": bid / ask if ask else None,
        "ask_bid_ratio": ask / bid if bid else None,
        "ask_coverage_pct": ask_cover * 100,
        "bid_coverage_pct": bid_cover * 100,
        "coverage_complete": ask_cover >= pct_value and bid_cover >= pct_value,
    }


def near_level(walls: list[dict[str, float]], level: float, *, tolerance: float) -> dict[str, float] | None:
    candidates = [row for row in walls if abs(row["price"] - level) <= tolerance]
    return max(candidates, key=lambda row: row["notional_usd"]) if candidates else None


def oi_delta(rows: list[tuple[int, float, float]], steps: int, *, latest_price: float | None = None) -> dict[str, Any]:
    latest = rows[-1]
    previous = rows[-1 - steps]
    btc_delta = latest[1] - previous[1]
    value_delta = latest[2] - previous[2]
    return {
        "btc_delta": btc_delta,
        "estimated_notional_delta_usd": btc_delta * latest_price if latest_price is not None else None,
        "exchange_value_delta_usd": value_delta,
        "usd_delta": btc_delta * latest_price if latest_price is not None else value_delta,
        "from_cst": cst_datetime(previous[0]),
        "to_cst": cst_datetime(latest[0]),
    }


def kline_summary(row: list[Any]) -> dict[str, Any]:
    open_price = float(row[1])
    high = float(row[2])
    low = float(row[3])
    close = float(row[4])
    volume_btc = float(row[5])
    quote_volume = float(row[7])
    pct_change = (close - open_price) / open_price * 100 if open_price else 0.0
    range_pct = (high - low) / open_price * 100 if open_price else 0.0
    upper_wick = (high - max(open_price, close)) / open_price * 100 if open_price else 0.0
    lower_wick = (min(open_price, close) - low) / open_price * 100 if open_price else 0.0
    return {
        "open_cst": cst_datetime(int(row[0])),
        "close_cst": cst_datetime(int(row[6])),
        "open": open_price,
        "high": high,
        "low": low,
        "close": close,
        "base_volume_btc": volume_btc,
        "quote_volume_usd": quote_volume,
        "pct_change": pct_change,
        "range_pct": range_pct,
        "upper_wick_pct": upper_wick,
        "lower_wick_pct": lower_wick,
        "vpp_by_close": quote_volume / abs(pct_change) if abs(pct_change) > 0 else None,
        "vpp_by_range": quote_volume / range_pct if range_pct > 0 else None,
    }


def rsi(closes: list[float], period: int = 14) -> float | None:
    if len(closes) < period + 1:
        return None
    gains: list[float] = []
    losses: list[float] = []
    for index in range(1, period + 1):
        change = closes[index] - closes[index - 1]
        gains.append(max(change, 0.0))
        losses.append(max(-change, 0.0))
    avg_gain = sum(gains) / period
    avg_loss = sum(losses) / period
    for index in range(period + 1, len(closes)):
        change = closes[index] - closes[index - 1]
        avg_gain = (avg_gain * (period - 1) + max(change, 0.0)) / period
        avg_loss = (avg_loss * (period - 1) + max(-change, 0.0)) / period
    if avg_loss == 0:
        return 100.0
    return 100 - 100 / (1 + avg_gain / avg_loss)


def cst_datetime(ms: int | None) -> str | None:
    if ms is None:
        return None
    return datetime.fromtimestamp(ms / 1000, timezone.utc).astimezone(CST).strftime("%Y-%m-%d %H:%M:%S")


def fmt_money(value: Any) -> str:
    if value is None:
        return "N/A"
    number = float(value)
    sign = "-" if number < 0 else ""
    number = abs(number)
    if number >= 1_000_000_000:
        return f"{sign}${number / 1_000_000_000:.2f}B"
    if number >= 1_000_000:
        return f"{sign}${number / 1_000_000:.2f}M"
    if number >= 1_000:
        return f"{sign}${number / 1_000:.2f}K"
    return f"{sign}${number:.2f}"


def fmt_price(value: Any) -> str:
    return "N/A" if value is None else f"{float(value):,.1f}"


def fmt_contracts(value: Any) -> str:
    return "N/A" if value is None else f"{float(value):,.2f}"


def fmt_number(value: Any, digits: int) -> str:
    return "N/A" if value is None else f"{float(value):,.{digits}f}"


def pct(value: Any) -> str:
    return "N/A" if value is None else f"{float(value) * 100:.2f}%"
