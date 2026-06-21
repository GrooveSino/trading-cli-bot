from __future__ import annotations

from typing import Annotated

import typer

from trading_gateway.application.market.multi import normalize_bundle_symbols
from trading_gateway.application.market.specs import get_market_spec, get_venue_profile
from trading_gateway.interfaces.cli.commands.trade_smoke import trade_plan as smoke_plan, trade_smoke
from trading_gateway.interfaces.cli.commands.venues.doctor import okx_sim_doctor
from trading_gateway.interfaces.cli.commands.venues.lifecycle import run_okx_sim_lifecycle
from trading_gateway.interfaces.cli.commands.venues.lifecycle_view import render_sim_lifecycle
from trading_gateway.interfaces.cli.commands.venues.snapshot_io import market_bundle_command, market_command
from trading_gateway.support.formatting import print_json

JsonOpt = Annotated[bool, typer.Option("--json", help="print machine-readable JSON")]


def register_venue_commands(app: typer.Typer) -> None:
    app.add_typer(_quick_namespace("okx-live"), name="live", help="OKX live shortcuts for market and account reads.")
    app.add_typer(_quick_namespace("okx-sim"), name="sim", help="OKX demo shortcuts for market, account, doctor, and lifecycle tests.")
    app.add_typer(_namespace("okx-live"), name="okx", help="OKX live commands: market, account, and guarded trading.")


def _namespace(venue: str) -> typer.Typer:
    root = typer.Typer(add_completion=False, rich_markup_mode=None)
    market = typer.Typer(add_completion=False, help="Read hosted or local market snapshots.")
    account = typer.Typer(add_completion=False, help="Read symbol-scoped account overlay.")
    trade = typer.Typer(add_completion=False, help="Plan or run guarded trade smoke tests.")
    market.command("btc", help="Read BTC market snapshot.")(_market_fn(venue, "btc"))
    market.command("eth", help="Read ETH market snapshot.")(_market_fn(venue, "eth"))
    market.command("all", help="Read BTC and ETH market snapshots.")(_market_all_fn(venue))
    account.command("btc", help="Read BTC account overlay.")(_account_fn(venue, "btc"))
    account.command("eth", help="Read ETH account overlay.")(_account_fn(venue, "eth"))
    trade.command("plan", help="Build a venue-scoped dry trade plan.")(_trade_plan_fn(venue))
    trade.command("smoke", help="Run a venue-scoped smoke test after exact confirmation.")(_trade_smoke_fn(venue))
    trade.command("lifecycle", help="Verify OKX sim cancel/open/close lifecycle after exact confirmation.")(_trade_lifecycle_fn(venue))
    root.command("doctor", help="Check OKX sim readiness without placing orders.")(_doctor_fn(venue))
    root.add_typer(market, name="market")
    root.add_typer(account, name="account")
    root.add_typer(trade, name="trade")
    return root


def _market_fn(venue: str, symbol: str):
    def command(
        extra_symbols: Annotated[list[str] | None, typer.Argument(help="optional extra symbols, e.g. eth")] = None,
        json_output: JsonOpt = False,
        llm: Annotated[bool, typer.Option("--llm/--table", help="print full LLM-friendly context or the audit table")] = True,
        account: Annotated[bool, typer.Option("--account/--no-account", help="include private account overlay")] = True,
        remote: Annotated[bool, typer.Option("--remote/--local", help="prefer hosted snapshot over local collection")] = True,
        max_remote_age_sec: Annotated[float | None, typer.Option("--max-remote-age-sec", help="mark remote snapshot stale after N seconds")] = None,
    ) -> None:
        symbols = normalize_bundle_symbols([symbol, *(extra_symbols or [])])
        if len(symbols) == 1:
            _market_command(venue, symbols[0], json_output, llm, account, remote, max_remote_age_sec)
            return
        _market_bundle_command(venue, symbols, json_output, llm, account, remote, max_remote_age_sec)

    return command


def _quick_namespace(venue: str) -> typer.Typer:
    root = typer.Typer(add_completion=False, rich_markup_mode=None)
    root.add_typer(_quick_symbol_app(venue, "btc"), name="btc", help=f"{_quick_label(venue)} BTC shortcut.")
    root.add_typer(_quick_symbol_app(venue, "eth"), name="eth", help=f"{_quick_label(venue)} ETH shortcut.")
    root.command("all", help=f"Read {_quick_label(venue)} BTC and ETH snapshots.")(_quick_all_fn(venue))
    return root


def _quick_symbol_app(venue: str, symbol: str) -> typer.Typer:
    root = typer.Typer(add_completion=False, invoke_without_command=True, rich_markup_mode=None)

    @root.callback(invoke_without_command=True)
    def market(
        ctx: typer.Context,
        json_output: JsonOpt = False,
        llm: Annotated[bool, typer.Option("--llm/--table", help="print full LLM context or the audit table")] = True,
        account: Annotated[bool, typer.Option("--account/--no-account", help="include private account overlay")] = False,
        remote: Annotated[bool, typer.Option("--remote/--local", help="prefer hosted snapshot over local collection")] = True,
        max_remote_age_sec: Annotated[float | None, typer.Option("--max-remote-age-sec", help="mark remote snapshot stale after N seconds")] = None,
    ) -> None:
        if ctx.invoked_subcommand is None:
            market_command(venue, symbol, json_output, llm, account, remote, max_remote_age_sec)

    root.command("account", help=f"Read {_quick_label(venue)} {symbol.upper()} account overlay.")(_quick_account_fn(venue, symbol))
    if venue == "okx-sim":
        root.command("doctor", help="Check OKX Sim credentials and sandbox readiness.")(_quick_doctor_fn(symbol))
        root.command("test", help="Run an OKX Sim order lifecycle dry-run or --yes execution.")(_quick_test_fn(symbol))
    return root


def _quick_all_fn(venue: str):
    def command(
        json_output: JsonOpt = False,
        llm: Annotated[bool, typer.Option("--llm/--table", help="print full LLM context or the audit table")] = True,
        account: Annotated[bool, typer.Option("--account/--no-account", help="include private account overlay")] = False,
        remote: Annotated[bool, typer.Option("--remote/--local", help="prefer hosted snapshot over local collection")] = True,
        max_remote_age_sec: Annotated[float | None, typer.Option("--max-remote-age-sec", help="mark remote snapshot stale after N seconds")] = None,
    ) -> None:
        market_bundle_command(venue, ["btc", "eth"], json_output, llm, account, remote, max_remote_age_sec)

    return command


def _quick_account_fn(venue: str, symbol: str):
    def command(json_output: JsonOpt = False) -> None:
        _account_payload_command(venue, symbol, json_output)

    return command


def _quick_doctor_fn(symbol: str):
    def command(
        json_output: JsonOpt = False,
        remote: Annotated[bool, typer.Option("--remote/--local", help="check hosted snapshot availability")] = True,
    ) -> None:
        payload = okx_sim_doctor(symbol, remote=remote)
        print_json(payload) if json_output else print(_render_doctor(payload), end="")

    return command


def _quick_test_fn(symbol: str):
    def command(
        usd: Annotated[float, typer.Option("--usd", help="quote USDT amount for the lifecycle test")] = 5.0,
        sell: Annotated[bool, typer.Option("--sell", help="use sell side for swap lifecycle; spot still closes after buy")] = False,
        swap: Annotated[bool, typer.Option("--swap", help="test swap lifecycle instead of default spot lifecycle")] = False,
        yes: Annotated[bool, typer.Option("--yes", help="execute on OKX Sim; omit for dry-run")] = False,
        json_output: JsonOpt = False,
    ) -> None:
        side = "sell" if sell else "buy"
        market = "swap" if swap else "spot"
        profile = get_venue_profile("okx-sim")
        confirm = _sim_confirm(symbol, side, usd) if yes else ""
        payload = run_okx_sim_lifecycle(profile, symbol, side, usd, 1, "cross", yes, confirm, market)
        _add_account_mode_hint(payload)
        if json_output:
            print_json(payload)
        else:
            print(render_sim_lifecycle(payload, executed=yes), end="")
        if yes and payload.get("status") != "ok":
            typer.echo(f"Error: {payload.get('error') or payload.get('status')}", err=True)
            raise typer.Exit(1)

    return command


def _market_all_fn(venue: str):
    def command(
        json_output: JsonOpt = False,
        llm: Annotated[bool, typer.Option("--llm/--table", help="print full LLM-friendly context or the audit table")] = True,
        account: Annotated[bool, typer.Option("--account/--no-account", help="include private account overlay")] = True,
        remote: Annotated[bool, typer.Option("--remote/--local", help="prefer hosted snapshot over local collection")] = True,
        max_remote_age_sec: Annotated[float | None, typer.Option("--max-remote-age-sec", help="mark remote snapshot stale after N seconds")] = None,
    ) -> None:
        _market_bundle_command(venue, ["btc", "eth"], json_output, llm, account, remote, max_remote_age_sec)

    return command


def _account_fn(venue: str, symbol: str):
    def command(json_output: JsonOpt = False) -> None:
        _account_payload_command(venue, symbol, json_output)

    return command


def _trade_plan_fn(venue: str):
    def command(
        symbol: Annotated[str, typer.Argument(help="symbol: btc/eth")],
        side: Annotated[str, typer.Option("--side", help="buy/sell")],
        quote_usdt: Annotated[float, typer.Option("--quote-usdt")],
        last_price: Annotated[float | None, typer.Option("--last-price")] = None,
        json_output: JsonOpt = False,
    ) -> None:
        profile = get_venue_profile(venue)
        spec = get_market_spec(symbol)
        smoke_plan(profile.exchange, "perp", spec.okx_ccxt_symbol, side, quote_usdt, 1, profile.account_mode, "cross", "oneway", last_price, json_output)

    return command


def _trade_smoke_fn(venue: str):
    def command(
        symbol: Annotated[str, typer.Argument(help="symbol: btc/eth")],
        side: Annotated[str, typer.Option("--side", help="buy/sell")],
        quote_usdt: Annotated[float, typer.Option("--quote-usdt")],
        last_price: Annotated[float | None, typer.Option("--last-price")] = None,
        live: Annotated[bool, typer.Option("--live/--no-live")] = False,
        confirm: Annotated[str, typer.Option("--confirm")] = "",
        json_output: JsonOpt = False,
    ) -> None:
        profile = get_venue_profile(venue)
        spec = get_market_spec(symbol)
        trade_smoke(profile.exchange, "perp", spec.okx_ccxt_symbol, side, quote_usdt, 1, profile.account_mode, "cross", "oneway", last_price, json_output, live, confirm, False, "")

    return command


def _doctor_fn(venue: str):
    def command(
        symbol: Annotated[str, typer.Argument(help="symbol: btc/eth")],
        remote: Annotated[bool, typer.Option("--remote/--local", help="check hosted snapshot availability")] = True,
    ) -> None:
        if venue != "okx-sim":
            typer.echo("Error: doctor is for OKX sim only; use tbot sim btc doctor", err=True)
            raise typer.Exit(1)
        payload = okx_sim_doctor(symbol, remote=remote)
        print_json(payload)

    return command


def _trade_lifecycle_fn(venue: str):
    def command(
        symbol: Annotated[str, typer.Argument(help="symbol: btc/eth")],
        side: Annotated[str, typer.Option("--side", help="buy/sell")],
        quote_usdt: Annotated[float, typer.Option("--quote-usdt")],
        market: Annotated[str, typer.Option("--market", help="spot or swap; spot works in OKX demo Spot mode")] = "spot",
        leverage: Annotated[int, typer.Option("--leverage")] = 1,
        margin_mode: Annotated[str, typer.Option("--margin-mode")] = "cross",
        live: Annotated[bool, typer.Option("--live/--no-live")] = False,
        confirm: Annotated[str, typer.Option("--confirm")] = "",
        json_output: JsonOpt = False,
    ) -> None:
        try:
            payload = run_okx_sim_lifecycle(get_venue_profile(venue), symbol, side, quote_usdt, leverage, margin_mode, live, confirm, market)
        except ValueError as exc:
            message = str(exc)
            if "confirmation mismatch" in message:
                raise typer.BadParameter(message, param_hint="--confirm") from exc
            typer.echo(f"Error: {message}", err=True)
            raise typer.Exit(1)
        if json_output:
            print_json(payload)
            if live and payload.get("status") != "ok":
                typer.echo(f"Error: {payload.get('error') or payload.get('status')}", err=True)
                raise typer.Exit(1)
            return
        print(render_sim_lifecycle(payload, executed=live), end="")
        if live and payload.get("status") != "ok":
            raise typer.Exit(1)

    return command


def _market_command(venue: str, symbol: str, json_output: bool, llm: bool, account: bool, remote: bool, max_remote_age_sec: float | None) -> None:
    market_command(venue, symbol, json_output, llm, account, remote, max_remote_age_sec)


def _market_bundle_command(venue: str, symbols: list[str], json_output: bool, llm: bool, account: bool, remote: bool, max_remote_age_sec: float | None) -> None:
    market_bundle_command(venue, symbols, json_output, llm, account, remote, max_remote_age_sec)


def _account_payload_command(venue: str, symbol: str, json_output: bool) -> None:
    from trading_gateway.application.market.multi import build_market_snapshot

    snapshot = build_market_snapshot(venue, symbol, include_account=True)
    payload = {
        "mode": "account_overlay",
        "venue_profile": snapshot["venue_profile"],
        "symbol": symbol,
        "account_overlay": snapshot["account_overlay"],
    }
    print_json(payload) if json_output else print_json(payload)


def _render_doctor(payload: dict) -> str:
    lines = ["OKX Sim doctor", "", f"status: {payload.get('status', '-')}"]
    for row in payload.get("checks") or []:
        suffix = f" error={row.get('error')}" if row.get("error") else ""
        lines.append(f"- {row.get('name')}: {row.get('status')}{suffix}")
    return "\n".join(lines) + "\n"


def _add_account_mode_hint(payload: dict) -> None:
    text = str(payload.get("error") or "")
    if "51010" in text or "account mode" in text.lower():
        payload["hint"] = "OKX Sim is likely in Spot mode. Use default spot test or switch demo account mode before --swap."


def _sim_confirm(symbol: str, side: str, usd: float) -> str:
    from trading_gateway.interfaces.cli.commands.venues.lifecycle import lifecycle_confirm_phrase

    return lifecycle_confirm_phrase(get_market_spec(symbol), side, usd)


def _quick_label(venue: str) -> str:
    return "OKX Live" if venue == "okx-live" else "OKX Sim"
