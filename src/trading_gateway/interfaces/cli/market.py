from __future__ import annotations

import itertools
import sys
import threading
import time
from contextlib import contextmanager
from pathlib import Path
from typing import Annotated

import typer

from trading_gateway.application.market.btcusdt.features import build_llm_feature_vectors
from trading_gateway.application.market.btcusdt.public_view import market_snapshot_public_view
from trading_gateway.application.market.multi import render_llm_context
from trading_gateway.application.marketdata.btcusdt import load_remote_btcusdt_snapshot, remote_failure_reason
from trading_gateway.application.market.btcusdt_snapshot import build_btcusdt_snapshot, render_markdown
from trading_gateway.interfaces.cli import help as cli_help
from trading_gateway.support.formatting import print_json

JsonOpt = Annotated[bool, typer.Option("--json", help="Print machine-readable JSON instead of Markdown.", rich_help_panel="Output")]
DEFAULT_BTCUSDT_REPORT = Path("var/reports/btcusdt-market-snapshot.md")


def register_market_commands(app: typer.Typer) -> None:
    market_app = typer.Typer(add_completion=False, help=cli_help.MARKET, epilog=cli_help.MARKET_EPILOG, rich_markup_mode=None)
    market_app.command(
        "btcusdt",
        help="Fetch BTCUSDT/BTC-USDT-SWAP orderbook, OI, CVD, whale-flow, RSI, VPP, and optional OKX BTC account state.",
        epilog=cli_help.MARKET_BTCUSDT_EPILOG,
    )(market_btcusdt)
    app.add_typer(market_app, name="market", help=cli_help.MARKET)


def market_btcusdt(
    json_output: JsonOpt = False,
    llm: Annotated[
        bool,
        typer.Option("--llm/--table", help="Print full LLM-friendly context or the audit table.", rich_help_panel="Output"),
    ] = True,
    write: Annotated[
        bool,
        typer.Option("--write/--no-write", help="Overwrite the Markdown report after rendering the snapshot.", rich_help_panel="Output"),
    ] = False,
    output: Annotated[
        Path,
        typer.Option("--output", help="Report path used with --write. Parent directories are created automatically.", rich_help_panel="Output"),
    ] = DEFAULT_BTCUSDT_REPORT,
    okx_account: Annotated[
        bool,
        typer.Option(
            "--okx-account/--no-okx-account",
            help="Include OKX BTC-USDT-SWAP positions, open orders, and algo orders.",
            rich_help_panel="Account Overlay",
        ),
    ] = True,
    binance_account: Annotated[
        bool,
        typer.Option(
            "--binance-account/--no-binance-account",
            help="Compatibility flag. No Binance private-account overlay is shown.",
            rich_help_panel="Account Overlay",
        ),
    ] = False,
    remote: Annotated[
        bool,
        typer.Option("--remote/--local", help="Prefer hosted marketdata snapshot over local live collection.", rich_help_panel="Remote Snapshot"),
    ] = True,
    remote_host: Annotated[
        str | None,
        typer.Option("--remote-host", help="Hosted marketdata alias used for cache tagging and SSH fallback.", rich_help_panel="Remote Snapshot"),
    ] = None,
    max_remote_age_sec: Annotated[
        float | None,
        typer.Option("--max-remote-age-sec", help="Mark remote snapshot stale after N seconds.", rich_help_panel="Remote Snapshot"),
    ] = None,
) -> None:
    if binance_account:
        raise typer.BadParameter("Binance private-account overlay is not implemented for market btcusdt.", param_hint="--binance-account")
    started = time.perf_counter()
    with _loading("loading BTCUSDT market snapshot", enabled=not json_output):
        snapshot = load_remote_btcusdt_snapshot(remote=remote, remote_host=remote_host, max_age_sec=max_remote_age_sec)
    if snapshot is None:
        snapshot = build_btcusdt_snapshot(include_okx_account=okx_account)
        snapshot["remote_snapshot"] = {"status": "local_fallback", "reason": "remote disabled by --local" if not remote else remote_failure_reason() or "remote unavailable"}
    _ensure_llm_features(snapshot)
    snapshot["cli_elapsed_ms"] = int((time.perf_counter() - started) * 1000)
    output_snapshot = market_snapshot_public_view(snapshot)
    if json_output:
        print_json(output_snapshot)
        return
    markdown = render_llm_context(output_snapshot) if llm else render_markdown(output_snapshot)
    if write:
        output.parent.mkdir(parents=True, exist_ok=True)
        output.write_text(markdown, encoding="utf-8")
    print(markdown, end="")


def _ensure_llm_features(snapshot: dict) -> None:
    if not snapshot.get("llm_feature_vectors"):
        snapshot["llm_feature_vectors"] = build_llm_feature_vectors(snapshot)


@contextmanager
def _loading(label: str, *, enabled: bool):
    if not enabled:
        yield
        return
    done = threading.Event()
    thread = threading.Thread(target=_spin, args=(label, done), daemon=True)
    thread.start()
    try:
        yield
    finally:
        done.set()
        thread.join(timeout=0.2)
        sys.stderr.write("\r\033[K")
        sys.stderr.flush()


def _spin(label: str, done: threading.Event) -> None:
    for frame in itertools.cycle("⠋⠙⠹⠸⠼⠴⠦⠧⠇⠏"):
        if done.is_set():
            return
        sys.stderr.write(f"\r{frame} {label}...")
        sys.stderr.flush()
        done.wait(0.12)
