from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from trading_gateway.app.config import get_gateway_config

MINIMAL_ROUTE_UNIVERSE = {
    "schema_version": 4,
    "generated_at": "embedded",
    "allow_same_exchange": True,
    "total_symbols": 2,
    "symbols": [
        {"symbol": "BTC/USDT", "spot": ["binance", "gate", "mexc", "okx"], "perp": ["binance", "gate", "mexc", "okx"]},
        {"symbol": "ETH/USDT", "spot": ["binance", "gate", "mexc", "okx"], "perp": ["binance", "gate", "mexc", "okx"]},
    ],
}


def load_route_universe(path: str | Path | None = None) -> dict[str, Any]:
    target = Path(path) if path is not None else get_gateway_config().route_universe
    if not target.exists():
        if target.name == "symbol_route_universe.json":
            return dict(MINIMAL_ROUTE_UNIVERSE)
        raise FileNotFoundError(f"route universe not found: {target}")
    payload = json.loads(target.read_text(encoding="utf-8"))
    if payload.get("schema_version") != 4:
        raise ValueError("route universe schema_version must be 4")
    symbols = payload.get("symbols") or []
    if not symbols:
        raise ValueError("route universe symbols is empty")
    return payload


def trading_symbols_for_exchange(exchange: str, path: str | Path | None = None) -> dict[str, Any]:
    payload = load_route_universe(path)
    name = str(exchange).lower()
    symbols = payload["symbols"]
    spot = sorted(row["symbol"] for row in symbols if name in (row.get("spot") or []))
    perp = sorted(row["symbol"] for row in symbols if name in (row.get("perp") or []))
    return {
        "schema_version": payload["schema_version"],
        "generated_at": payload.get("generated_at"),
        "total_symbols": len(symbols),
        f"{name}_spot_symbols": spot,
        f"{name}_perp_symbols": perp,
    }


def validate_trading_symbol(
    symbol: str,
    market: str,
    exchange: str = "binance",
    path: str | Path | None = None,
) -> dict[str, Any]:
    normalized = _normalize_symbol(symbol)
    universe = load_route_universe(path)
    rows = {row["symbol"].upper(): row for row in universe["symbols"]}
    row = rows.get(normalized.upper())
    side = "perp" if market == "perp" else "spot"
    name = exchange.lower()
    if not row:
        return {"supported": False, "symbol": normalized, "reason": f"{normalized} not in route universe"}
    if name not in (row.get(side) or []):
        return {"supported": False, "symbol": normalized, "reason": f"{normalized} not in {name} {side} universe"}
    return {"supported": True, "symbol": normalized, "row": row}


def _normalize_symbol(symbol: str) -> str:
    text = str(symbol or "").strip().upper()
    if "/" in text:
        return text.split(":")[0]
    if text.endswith("USDT"):
        return f"{text.removesuffix('USDT')}/USDT"
    return text
