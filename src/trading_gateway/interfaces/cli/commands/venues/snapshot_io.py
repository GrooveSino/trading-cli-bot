from __future__ import annotations

import time

from trading_gateway.application.market.multi import (
    build_market_bundle,
    build_market_snapshot,
    render_bundle_llm_context,
    render_bundle_table_markdown,
    render_llm_context,
    render_market_markdown,
)
from trading_gateway.application.marketdata.multi import load_remote_market_snapshot, remote_failure_reason
from trading_gateway.support.formatting import print_json


def market_command(venue: str, symbol: str, json_output: bool, llm: bool, account: bool, remote: bool, max_remote_age_sec: float | None) -> None:
    started = time.perf_counter()
    snapshot = load_market_snapshot(venue, symbol, account, remote, max_remote_age_sec)
    snapshot["cli_elapsed_ms"] = int((time.perf_counter() - started) * 1000)
    if json_output:
        print_json(snapshot)
        return
    renderer = render_llm_context if llm else render_market_markdown
    print(renderer(snapshot), end="")


def market_bundle_command(venue: str, symbols: list[str], json_output: bool, llm: bool, account: bool, remote: bool, max_remote_age_sec: float | None) -> None:
    started = time.perf_counter()

    def load(profile: str, symbol: str) -> dict:
        return load_market_snapshot(profile, symbol, account, remote, max_remote_age_sec)

    bundle = build_market_bundle(venue, symbols, load, started_at=started)
    if json_output:
        print_json(bundle)
        return
    renderer = render_bundle_llm_context if llm else render_bundle_table_markdown
    print(renderer(bundle), end="")


def load_market_snapshot(venue: str, symbol: str, account: bool, remote: bool, max_remote_age_sec: float | None) -> dict:
    snapshot = load_remote_market_snapshot(venue, symbol, remote=remote, max_age_sec=max_remote_age_sec)
    if snapshot is None:
        snapshot = build_market_snapshot(venue, symbol, include_account=account)
        reason = "remote disabled by --local" if not remote else remote_failure_reason() or "remote unavailable"
        snapshot["remote_snapshot"] = {"status": "local_fallback", "reason": reason}
    elif not account:
        strip_account_overlay(snapshot)
    return snapshot


def strip_account_overlay(snapshot: dict) -> None:
    skipped = {
        "source": snapshot.get("venue_profile", {}).get("id"),
        "status": "skipped",
        "counts": {"positions": 0, "open_orders": 0, "algo_orders": 0},
        "positions": [],
        "open_orders": [],
        "algo_orders": {},
    }
    snapshot["account_overlay"] = skipped
    if snapshot.get("venue_profile", {}).get("exchange") == "okx":
        snapshot["okx_account"] = skipped
