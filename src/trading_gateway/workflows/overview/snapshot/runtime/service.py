from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import UTC, datetime
from time import perf_counter, time
from typing import Any, Callable

from trading_gateway.workflows.overview.snapshot.runtime.fetch import exchange_error, fetch_exchange_snapshot
from trading_gateway.domain.models import SUPPORTED_EXCHANGES, ExchangeCreds
from trading_gateway.application.wallet.policy import validate_private_account_exchanges
from trading_gateway.application.wallet.summary import build_summary_payload

Fetcher = Callable[[str], dict[str, Any]]


def build_account_snapshot(
    exchanges: list[str] | None = None,
    *,
    credentials: dict[str, ExchangeCreds] | None = None,
    nonzero_only: bool = True,
    include_empty_positions: bool = False,
    fetcher: Fetcher | None = None,
    account_mode: str | None = None,
) -> dict[str, Any]:
    started = perf_counter()
    picked = validate_private_account_exchanges(exchanges, account_mode)
    worker = fetcher or (
        lambda exchange: fetch_exchange_snapshot(
            exchange,
            credentials=credentials,
            nonzero_only=nonzero_only,
            include_empty_positions=include_empty_positions,
            account_mode=account_mode,
        )
    )
    rows = _fetch_parallel(picked, worker)
    warnings = [warning for row in rows for warning in row.get("warnings", [])]
    return {
        "generated_at": datetime.now(UTC).isoformat(),
        "generated_at_ms": int(time() * 1000),
        "query_ms": int((perf_counter() - started) * 1000),
        "exchanges": rows,
        "totals": _totals(rows),
        "warnings": warnings,
    }


def snapshot_to_summary_payload(snapshot: dict[str, Any]) -> dict[str, Any]:
    rows: list[dict[str, Any]] = []
    for exchange in snapshot.get("exchanges", []):
        spot = exchange.get("spot") or {}
        perp = exchange.get("perp") or {}
        rows.append(_summary_row(exchange, "spot", spot))
        rows.append(
            _summary_row(
                exchange,
                "perp",
                perp,
            )
        )
    payload = build_summary_payload(rows)
    payload["query_ms"] = snapshot.get("query_ms")
    payload["generated_at"] = snapshot.get("generated_at")
    return payload


def _fetch_parallel(exchanges: list[str], fetcher: Fetcher) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    with ThreadPoolExecutor(max_workers=min(4, max(1, len(exchanges))), thread_name_prefix="account-snapshot") as pool:
        futures = {pool.submit(fetcher, exchange): exchange for exchange in exchanges}
        for future in as_completed(futures):
            exchange = futures[future]
            try:
                rows.append(future.result())
            except Exception as exc:  # noqa: BLE001 - one venue must not hide the rest.
                rows.append(exchange_error(exchange, exc))
    return sorted(rows, key=lambda row: SUPPORTED_EXCHANGES.index(row["exchange"]) if row["exchange"] in SUPPORTED_EXCHANGES else 99)


def _summary_row(
    exchange: dict[str, Any],
    market: str,
    account: dict[str, Any],
) -> dict[str, Any]:
    assets = account.get("assets") or []
    usdt = _find_asset(assets, "USDT")
    return {
        "exchange": exchange["exchange"],
        "market": market,
        "account": account.get("account_type") or "separate",
        "assets": [_summary_asset(row) for row in assets],
        "equity_usdt": account.get("equity_usdt") or (usdt.get("total") if usdt else None),
        "available_usdt": account.get("available_usdt") or (usdt.get("free") if usdt else None),
        "positions_count": account.get("open_positions_count"),
        "query_ms": account.get("query_ms", exchange.get("query_ms", 0)),
        "error": None if account.get("status") == "ok" else account.get("status"),
    }


def _summary_asset(row: dict[str, Any]) -> dict[str, str]:
    return {"asset": row.get("asset", ""), "total": row.get("total", ""), "free": row.get("free", ""), "used": row.get("locked", "")}


def _find_asset(assets: list[dict[str, Any]], asset: str) -> dict[str, Any]:
    return next((row for row in assets if row.get("asset") == asset), {})


def _totals(rows: list[dict[str, Any]]) -> dict[str, int]:
    return {
        "exchanges": len(rows),
        "assets": sum(
            int((row.get("spot") or {}).get("asset_count") or 0) + int((row.get("perp") or {}).get("asset_count") or 0)
            for row in rows
        ),
        "open_positions": sum(int((row.get("perp") or {}).get("open_positions_count") or 0) for row in rows),
    }
