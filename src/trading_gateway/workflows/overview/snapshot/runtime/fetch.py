from __future__ import annotations

from time import perf_counter
from typing import Any

from trading_gateway.workflows.overview.snapshot.parsing.adapters import (
    parse_binance_spot,
    parse_binance_swap,
    parse_gate_spot,
    parse_gate_swap,
    parse_mexc_spot,
    parse_mexc_swap,
    parse_okx_spot,
    parse_okx_swap,
)
from trading_gateway.infrastructure.exchange.factory import build_ccxt_client, build_ccxt_client_from_creds, close_client
from trading_gateway.domain.models import ExchangeCreds, display_market
from trading_gateway.app.config import get_gateway_config
from trading_gateway.support.redaction import redact_text
from trading_gateway.workflows.overview.snapshot.utilities.utils import decimal, num


def fetch_exchange_snapshot(
    exchange: str,
    *,
    credentials: dict[str, ExchangeCreds] | None = None,
    nonzero_only: bool = True,
    include_empty_positions: bool = False,
    markets: tuple[str, ...] | None = None,
    account_mode: str | None = None,
) -> dict[str, Any]:
    started = perf_counter()
    picked_markets = set(markets or ("spot", "perp"))
    warnings: list[str] = []
    payload: dict[str, Any] = {
        "exchange": exchange,
        "query_ms": int((perf_counter() - started) * 1000),
        "warnings": warnings,
    }
    if "spot" in picked_markets:
        payload["spot"] = _fetch_spot(exchange, credentials, nonzero_only, warnings, account_mode)
    if "perp" in picked_markets:
        payload["perp"] = _fetch_perp(exchange, credentials, include_empty_positions, warnings, account_mode)
    payload["status"] = "partial_error" if warnings else "ok"
    payload["query_ms"] = int((perf_counter() - started) * 1000)
    return payload


def exchange_error(exchange: str, exc: Exception) -> dict[str, Any]:
    message = _warning(exchange, "account", exc)
    return {
        "exchange": exchange,
        "status": message,
        "query_ms": 0,
        "spot": {"market": "spot", "assets": [], "asset_count": 0, "hidden_zero_count": 0, "positions": [], "open_positions_count": 0, "account_type": "spot", "position_mode": None, "equity_usdt": None, "available_usdt": None, "query_ms": 0, "status": message},
        "perp": {"market": "perp", "assets": [], "asset_count": 0, "hidden_zero_count": 0, "positions": [], "open_positions_count": 0, "account_type": "perp", "position_mode": None, "equity_usdt": None, "available_usdt": None, "query_ms": 0, "status": message},
        "warnings": [message],
    }


def _fetch_spot(exchange: str, credentials: dict[str, ExchangeCreds] | None, nonzero_only: bool, warnings: list[str], account_mode: str | None) -> dict[str, Any]:
    client = None
    started = perf_counter()
    try:
        client = _client(exchange, "spot", credentials, account_mode)
        if exchange == "binance":
            payload = parse_binance_spot(client.privateGetAccount({"omitZeroBalances": "true"}), nonzero_only=nonzero_only).to_dict()
            _enrich_asset_usdt_values(client, payload)
            payload["query_ms"] = int((perf_counter() - started) * 1000)
            return payload
        if exchange == "okx":
            payload = parse_okx_spot(client.privateGetAccountBalance({}), nonzero_only=nonzero_only).to_dict()
            _enrich_asset_usdt_values(client, payload)
            payload["query_ms"] = int((perf_counter() - started) * 1000)
            return payload
        if exchange == "gate":
            payload = parse_gate_spot(client.privateSpotGetAccounts(), nonzero_only=nonzero_only).to_dict()
            _enrich_asset_usdt_values(client, payload)
            payload["query_ms"] = int((perf_counter() - started) * 1000)
            return payload
        payload = parse_mexc_spot(client.spotPrivateGetAccount(), nonzero_only=nonzero_only).to_dict()
        _enrich_asset_usdt_values(client, payload)
        payload["query_ms"] = int((perf_counter() - started) * 1000)
        return payload
    except Exception as exc:  # noqa: BLE001 - swap can still be useful.
        return _failed_account(exchange, "spot", exc, warnings, _empty_spot)
    finally:
        close_client(client)


def _fetch_perp(exchange: str, credentials: dict[str, ExchangeCreds] | None, include_empty_positions: bool, warnings: list[str], account_mode: str | None) -> dict[str, Any]:
    client = None
    started = perf_counter()
    try:
        client = _client(exchange, "swap", credentials, account_mode)
        if exchange == "binance":
            payload = parse_binance_swap(client.fapiPrivateV3GetAccount(), client.fapiPrivateV2GetPositionRisk(), include_empty_positions=include_empty_positions).to_dict()
            _enrich_stable_asset_usdt_values(payload)
            payload["query_ms"] = int((perf_counter() - started) * 1000)
            return payload
        if exchange == "okx":
            balance = client.privateGetAccountBalance({})
            payload = parse_okx_swap(client.privateGetAccountPositions({"instType": "SWAP"}), include_empty_positions=include_empty_positions, balance=balance).to_dict()
            _enrich_stable_asset_usdt_values(payload)
            payload["query_ms"] = int((perf_counter() - started) * 1000)
            return payload
        if exchange == "gate":
            payload = parse_gate_swap(client.privateFuturesGetSettleAccounts({"settle": "usdt"}), client.privateFuturesGetSettlePositions({"settle": "usdt"}), include_empty_positions=include_empty_positions).to_dict()
            _enrich_stable_asset_usdt_values(payload)
            payload["query_ms"] = int((perf_counter() - started) * 1000)
            return payload
        payload = parse_mexc_swap(client.contractPrivateGetAccountAssets(), client.contractPrivateGetPositionOpenPositions(), include_empty_positions=include_empty_positions).to_dict()
        _enrich_stable_asset_usdt_values(payload)
        payload["query_ms"] = int((perf_counter() - started) * 1000)
        return payload
    except Exception as exc:  # noqa: BLE001 - spot can still be useful.
        return _failed_account(exchange, "perp", exc, warnings, _empty_perp)
    finally:
        close_client(client)


def _client(exchange: str, market: str, credentials: dict[str, ExchangeCreds] | None, account_mode: str | None) -> Any:
    timeout_ms = get_gateway_config().account_snapshot_timeout_ms
    if credentials is None:
        return build_ccxt_client(exchange, market, require_private=True, account_mode=account_mode, timeout_ms=timeout_ms)
    creds = credentials.get(exchange)
    if not creds or not creds.api_key or not creds.api_secret:
        raise ValueError("missing credentials")
    return build_ccxt_client_from_creds(exchange, market, creds, account_mode=account_mode, timeout_ms=timeout_ms)


def _failed_account(exchange: str, market: str, exc: Exception, warnings: list[str], factory: Any) -> dict[str, Any]:
    message = _warning(exchange, market, exc)
    warnings.append(message)
    row = factory()
    row["status"] = message
    return row


def _empty_spot() -> dict[str, Any]:
    return {"market": "spot", "assets": [], "asset_count": 0, "hidden_zero_count": 0, "positions": [], "open_positions_count": 0, "account_type": "spot", "position_mode": None, "equity_usdt": None, "available_usdt": None, "query_ms": 0, "status": "empty"}


def _empty_perp() -> dict[str, Any]:
    return {"market": "perp", "assets": [], "asset_count": 0, "hidden_zero_count": 0, "positions": [], "open_positions_count": 0, "account_type": "perp", "position_mode": None, "equity_usdt": None, "available_usdt": None, "query_ms": 0, "status": "empty"}


def _warning(exchange: str, market: str, exc: Exception) -> str:
    return redact_text(f"{exchange} {display_market(market)} {type(exc).__name__}: {exc}")


def _enrich_asset_usdt_values(client: Any, account: dict[str, Any]) -> None:
    price_cache: dict[str, str | None] = {"USDT": "1"}
    for row in account.get("assets") or []:
        if row.get("usdt_value") not in (None, ""):
            continue
        asset = str(row.get("asset") or "").upper()
        total = decimal(row.get("total"))
        if not asset or total == 0:
            row["usdt_value"] = None
            continue
        price = price_cache.get(asset)
        if asset not in price_cache:
            price = _asset_usdt_price(client, asset)
            price_cache[asset] = price
        row["usdt_value"] = None if price in (None, "") else num(total * decimal(price))


def _enrich_stable_asset_usdt_values(account: dict[str, Any]) -> None:
    for row in account.get("assets") or []:
        if str(row.get("asset") or "").upper() in {"USDT", "USD", "USDC"} and row.get("usdt_value") in (None, ""):
            row["usdt_value"] = row.get("total")


def _asset_usdt_price(client: Any, asset: str) -> str | None:
    if asset in {"USDT", "USD", "USDC"}:
        return "1"
    try:
        ticker = client.fetch_ticker(f"{asset}/USDT") or {}
    except Exception:  # noqa: BLE001 - valuation is best-effort diagnostics only.
        return None
    for key in ("last", "close", "bid", "ask"):
        value = ticker.get(key)
        if decimal(value) > 0:
            return num(value)
    return None
