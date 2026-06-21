from __future__ import annotations

from dataclasses import dataclass
from typing import Literal

MarketSymbol = Literal["btc", "eth"]
VenueId = Literal["okx-live", "okx-sim"]


@dataclass(frozen=True)
class MarketSpec:
    key: MarketSymbol
    base: str
    derivatives_symbol: str
    okx_inst_id: str
    okx_ccxt_symbol: str


@dataclass(frozen=True)
class VenueProfile:
    id: VenueId
    exchange: str
    account_mode: str
    display_name: str
    market_source: str
    private_source: str


MARKET_SPECS: dict[str, MarketSpec] = {
    "btc": MarketSpec("btc", "BTC", "BTCUSDT", "BTC-USDT-SWAP", "BTC/USDT:USDT"),
    "eth": MarketSpec("eth", "ETH", "ETHUSDT", "ETH-USDT-SWAP", "ETH/USDT:USDT"),
}

VENUE_PROFILES: dict[str, VenueProfile] = {
    "okx-live": VenueProfile("okx-live", "okx", "live", "OKX Live", "OKX public market API", "OKX live private account/trade API"),
    "okx-sim": VenueProfile("okx-sim", "okx", "sim", "OKX Sim", "OKX public market API", "OKX demo private account/trade API"),
}


def get_market_spec(symbol: str) -> MarketSpec:
    key = normalize_market_symbol(symbol)
    return MARKET_SPECS[key]


def normalize_market_symbol(symbol: str) -> MarketSymbol:
    text = str(symbol or "").strip().lower().replace("-", "").replace("/", "")
    aliases = {"btcusdt": "btc", "btc": "btc", "ethusdt": "eth", "eth": "eth"}
    if text not in aliases:
        raise ValueError("symbol must be btc or eth")
    return aliases[text]  # type: ignore[return-value]


def get_venue_profile(venue: str) -> VenueProfile:
    key = normalize_venue(venue)
    return VENUE_PROFILES[key]


def normalize_venue(venue: str) -> VenueId:
    text = str(venue or "").strip().lower().replace("_", "-")
    aliases = {
        "okx": "okx-live",
        "okx-live": "okx-live",
        "okxsim": "okx-sim",
        "okx-sim": "okx-sim",
        "okx-demo": "okx-sim",
        "demo": "okx-sim",
    }
    if text not in aliases:
        raise ValueError("venue must be okx-live or okx-sim")
    return aliases[text]  # type: ignore[return-value]


def snapshot_slug(venue: str, symbol: str) -> str:
    return f"{normalize_venue(venue)}-{normalize_market_symbol(symbol)}"


def snapshot_filename(venue: str, symbol: str) -> str:
    return f"{snapshot_slug(venue, symbol)}-market-snapshot.json"


def supported_snapshot_pairs() -> list[tuple[VenueProfile, MarketSpec]]:
    return [(venue, spec) for venue in VENUE_PROFILES.values() for spec in MARKET_SPECS.values()]
