from __future__ import annotations

from typing import Annotated, Any

import typer

from trading_gateway.workflows.overview.snapshot.runtime.fetch import fetch_exchange_snapshot
from trading_gateway.workflows.overview.snapshot.runtime.cli import print_account_snapshot
from trading_gateway.interfaces.cli import help as cli_help
from trading_gateway.interfaces.cli.validation import validate_market
from trading_gateway.interfaces.daemon.client import DaemonClientError, daemon_http_post, ensure_daemon_ready_for_live
from trading_gateway.infrastructure.exchange.factory import build_ccxt_client, close_client
from trading_gateway.domain.models import TransferIntent, normalize_market, public_market_choices
from trading_gateway.support.formatting import print_json
from trading_gateway.support.redaction import redact_text
from trading_gateway.application.wallet.policy import private_account_choices, private_account_mode_choices, validate_private_account_exchange, validate_private_account_exchanges
from trading_gateway.application.wallet.wallet import fetch_wallet_snapshot, run_transfer
from trading_gateway.application.wallet.summary_runner import print_wallet_summary

JsonOpt = Annotated[bool, typer.Option("--json", help="print machine-readable JSON")]
ExchangeOpt = Annotated[str, typer.Option(help=f"private account exchange: {private_account_choices()}")]
MarketOpt = Annotated[str, typer.Option(help=f"market: {public_market_choices()}")]
AccountModeOpt = Annotated[str | None, typer.Option("--account-mode", help=f"private account mode: {private_account_mode_choices()}; default is live; use sim for OKX demo")]


def register_wallet_commands(wallet_app: typer.Typer) -> None:
    wallet_app.callback(invoke_without_command=True)(_wallet_root)
    wallet_app.command("summary", help=cli_help.SUMMARY)(wallet_summary)
    wallet_app.command("snapshot", help=cli_help.SNAPSHOT)(wallet_snapshot)
    wallet_app.command("balance", help=cli_help.BALANCE)(wallet_balance)
    wallet_app.command("positions", help=cli_help.POSITIONS)(wallet_positions)
    wallet_app.command("orders", help=cli_help.ORDERS)(wallet_orders)
    wallet_app.command("transfer", help=cli_help.TRANSFER)(wallet_transfer)


def _wallet_root(
    ctx: typer.Context,
) -> None:
    if ctx.args:
        _print_wallet_hint(ctx.args)
        raise typer.Exit(2)
    if ctx.invoked_subcommand is not None:
        return
    print_wallet_summary(None, json_output=False)
    raise typer.Exit(0)


def wallet_summary(
    exchange: Annotated[list[str] | None, typer.Option("--exchange", help="repeat to limit private exchanges; defaults to okx")] = None,
    account_mode: AccountModeOpt = None,
    json_output: JsonOpt = False,
    progress: Annotated[bool, typer.Option("--progress/--no-progress", help="show colored progress bar")] = True,
    with_positions: Annotated[bool, typer.Option("--with-positions", help="also query perp positions count")] = False,
    cache_ttl_sec: Annotated[float, typer.Option("--cache-ttl-sec", help="reuse local summary cache within N seconds")] = 0,
) -> None:
    exchanges = _validate_private_exchanges(exchange, account_mode)
    print_wallet_summary(exchanges, json_output=json_output, progress_enabled=progress, include_positions=with_positions, cache_ttl_sec=cache_ttl_sec, account_mode=account_mode)


def wallet_snapshot(
    exchange: Annotated[list[str] | None, typer.Option("--exchange", help="repeat to limit private exchanges; defaults to okx")] = None,
    account_mode: AccountModeOpt = None,
    json_output: JsonOpt = False,
    nonzero_only: Annotated[bool, typer.Option("--nonzero-only/--all-assets", help="hide zero asset rows")] = True,
    active_positions_only: Annotated[bool, typer.Option("--active-positions-only/--all-positions", help="hide flat perp positions")] = True,
) -> None:
    exchanges = _validate_private_exchanges(exchange, account_mode)
    print_account_snapshot(exchanges, json_output=json_output, nonzero_only=nonzero_only, active_positions_only=active_positions_only, account_mode=account_mode)


def wallet_balance(
    exchange: ExchangeOpt,
    market: Annotated[str, typer.Option(help=f"market: {public_market_choices(include_both=True)}")] = "both",
    account_mode: AccountModeOpt = None,
    nonzero_only: Annotated[bool, typer.Option("--nonzero-only/--all-assets", help="hide zero asset rows")] = True,
    raw: Annotated[bool, typer.Option("--raw", help="show debug raw exchange/ccxt wallet payload")] = False,
) -> None:
    exchange = _validate_private_exchange(exchange, account_mode)
    market_name = str(market or "").strip().lower()
    if market_name == "both":
        normalized_market = "both"
    else:
        normalized_market = normalize_market(market_name)
    if normalized_market not in {"spot", "swap", "both"}:
        raise typer.BadParameter("market must be spot, perp, or both", param_hint="--market")
    if raw:
        _print_raw_wallet_balance(exchange, normalized_market, nonzero_only, account_mode)
        return
    snapshot = fetch_exchange_snapshot(
        exchange,
        nonzero_only=nonzero_only,
        include_empty_positions=False,
        markets=("spot", "perp") if normalized_market == "both" else ("perp",) if normalized_market == "swap" else ("spot",),
        account_mode=account_mode,
    )
    if normalized_market == "both":
        print_json(snapshot)
        return
    if normalized_market == "swap":
        print_json(_account_view(snapshot, "perp"))
        return
    print_json(_account_view(snapshot, "spot"))


def wallet_positions(exchange: ExchangeOpt, symbol: Annotated[str | None, typer.Option()] = None, account_mode: AccountModeOpt = None) -> None:
    exchange = _validate_private_exchange(exchange, account_mode)
    snapshot = fetch_exchange_snapshot(exchange, nonzero_only=True, include_empty_positions=True, markets=("perp",), account_mode=account_mode)
    _raise_snapshot_account_error(snapshot, "perp")
    rows = (snapshot.get("perp") or {}).get("positions") or []
    if symbol:
        rows = [row for row in rows if _matches_position_symbol(row, symbol)]
    print_json({"positions": rows})


def wallet_orders(exchange: ExchangeOpt, market: MarketOpt, symbol: Annotated[str, typer.Option()], account_mode: AccountModeOpt = None) -> None:
    exchange = _validate_private_exchange(exchange, account_mode)
    validate_market(market)
    try:
        print_json({"open_orders": _wallet_snapshot(exchange, market, symbol, account_mode=account_mode).open_orders})
    except Exception as exc:  # noqa: BLE001 - CLI boundary returns concise redacted exchange errors.
        raise typer.BadParameter(redact_text(f"{type(exc).__name__}: {exc}")) from exc


def wallet_transfer(
    exchange: ExchangeOpt,
    code: Annotated[str, typer.Option()],
    amount: Annotated[float, typer.Option()],
    from_account: Annotated[str, typer.Option("--from")],
    to_account: Annotated[str, typer.Option("--to")],
    live: Annotated[bool, typer.Option()] = False,
    confirm: Annotated[str, typer.Option()] = "",
) -> None:
    exchange = _validate_private_exchange(exchange)
    intent = TransferIntent(exchange, code, amount, from_account, to_account)
    if live:
        try:
            ensure_daemon_ready_for_live()
            print_json(
                daemon_http_post(
                    "/api/transfer/run",
                    {
                        "exchange": exchange,
                        "code": code,
                        "amount": amount,
                        "from_account": from_account,
                        "to_account": to_account,
                        "live": True,
                        "confirm": confirm,
                    },
                )
            )
            return
        except DaemonClientError as exc:
            raise typer.BadParameter(str(exc), param_hint="daemon") from exc
    client = object()
    try:
        print_json(run_transfer(client, intent, live=False, confirm=confirm))
    finally:
        close_client(client)


def _wallet_snapshot(exchange: str, market: str, symbol: str | None = None, *, nonzero_only: bool = True, account_mode: str | None = None) -> Any:
    exchange = validate_private_account_exchange(exchange, account_mode)
    client = build_ccxt_client(exchange, market, require_private=True, account_mode=account_mode)
    try:
        return fetch_wallet_snapshot(client, exchange, symbol, nonzero_only=nonzero_only)
    finally:
        close_client(client)


def _print_raw_wallet_balance(exchange: str, market: str, nonzero_only: bool, account_mode: str | None) -> None:
    if market == "both":
        print_json(
            {
                "exchange": exchange,
                "spot": _wallet_snapshot(exchange, "spot", nonzero_only=nonzero_only, account_mode=account_mode).to_dict(),
                "perp": _wallet_snapshot(exchange, "swap", nonzero_only=nonzero_only, account_mode=account_mode).to_dict(),
            }
        )
        return
    raw_market = "swap" if market == "swap" else market
    print_json(_wallet_snapshot(exchange, raw_market, nonzero_only=nonzero_only, account_mode=account_mode).to_dict())


def _account_view(snapshot: dict[str, Any], market: str) -> dict[str, Any]:
    account = dict(snapshot.get(market) or {})
    account["exchange"] = snapshot.get("exchange")
    if not account.get("query_ms"):
        account["query_ms"] = snapshot.get("query_ms", 0)
    return account


def _raise_snapshot_account_error(snapshot: dict[str, Any], market: str) -> None:
    account = snapshot.get(market) or {}
    status = str(account.get("status") or "")
    if status and status != "empty" and not status.startswith("ok"):
        raise typer.BadParameter(redact_text(status))
    warnings = snapshot.get("warnings") or []
    if warnings:
        raise typer.BadParameter(redact_text("; ".join(str(item) for item in warnings)))


def _matches_position_symbol(row: dict[str, Any], symbol: str) -> bool:
    aliases = _position_aliases(row)
    needle = str(symbol or "").strip().upper()
    compact = "".join(ch for ch in needle if ch.isalnum())
    return needle in aliases or compact in {"".join(ch for ch in item if ch.isalnum()) for item in aliases}


def _position_aliases(row: dict[str, Any]) -> set[str]:
    symbol = str(row.get("symbol") or "").upper()
    base = str(row.get("base") or "").upper()
    quote = str(row.get("quote") or "").upper()
    settle = str(row.get("settle") or "").upper()
    aliases = {symbol}
    if base and quote:
        aliases.add(f"{base}/{quote}")
        aliases.add(f"{base}_{quote}")
        aliases.add(f"{base}-{quote}")
        aliases.add(f"{base}{quote}")
        if settle:
            aliases.add(f"{base}/{quote}:{settle}")
    return {item for item in aliases if item}


def _print_wallet_hint(args: list[str]) -> None:
    command = args[0].removeprefix("--") if args and args[0].startswith("--") else ""
    if command in {"balance", "snapshot", "summary", "positions", "orders", "transfer"}:
        typer.echo(f"--{command} is not an option; use '{command}' as a command.", err=True)
        typer.echo("Examples:", err=True)
        typer.echo("  tbot balance okx perp", err=True)
        typer.echo(f"  tbot wallet {command} --help", err=True)
        return
    typer.echo("Unknown wallet argument. Run: tbot wallet --help", err=True)


def _validate_private_exchange(exchange: str, account_mode: str | None = None) -> str:
    try:
        return validate_private_account_exchange(exchange, account_mode)
    except ValueError as exc:
        raise typer.BadParameter(str(exc), param_hint="exchange") from exc


def _validate_private_exchanges(exchanges: list[str] | None, account_mode: str | None = None) -> list[str]:
    try:
        return validate_private_account_exchanges(exchanges, account_mode)
    except ValueError as exc:
        raise typer.BadParameter(str(exc), param_hint="--exchange") from exc
