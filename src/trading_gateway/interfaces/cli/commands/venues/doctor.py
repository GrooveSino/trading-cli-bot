from __future__ import annotations

from typing import Any

from trading_gateway.application.market.specs import get_market_spec
from trading_gateway.application.marketdata.multi import load_remote_market_snapshot, remote_failure_reason
from trading_gateway.interfaces.cli.commands.venues.lifecycle import lifecycle_confirm_phrase
from trading_gateway.interfaces.cli.commands.venues.readiness import okx_sim_trade_readiness


def okx_sim_doctor(symbol: str, *, remote: bool = True) -> dict[str, Any]:
    spec = get_market_spec(symbol)
    readiness = okx_sim_trade_readiness(spec.okx_ccxt_symbol)
    checks = [*readiness["checks"], _remote_snapshot_check(spec.key, remote)]
    ready = all(row["status"] in {"ok", "skipped"} for row in checks)
    return {
        "mode": "okx_sim_readiness",
        "status": "ok" if ready else "not_ready",
        "symbol": spec.key,
        "ccxt_symbol": spec.okx_ccxt_symbol,
        "live_confirm_phrase": lifecycle_confirm_phrase(spec, "buy", 10),
        "checks": checks,
    }


def _remote_snapshot_check(symbol: str, remote: bool) -> dict[str, Any]:
    if not remote:
        return {"name": "okx_sim_remote_snapshot", "status": "skipped", "reason": "remote disabled"}
    snapshot = load_remote_market_snapshot("okx-sim", symbol, remote=remote)
    if snapshot is None:
        return {"name": "okx_sim_remote_snapshot", "status": "error", "reason": remote_failure_reason() or "unavailable"}
    remote_status = snapshot.get("remote_snapshot") or {}
    return {
        "name": "okx_sim_remote_snapshot",
        "status": "ok",
        "transport": remote_status.get("transport"),
        "age_sec": remote_status.get("age_sec"),
        "source_status": remote_status.get("status"),
    }
