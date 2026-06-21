from __future__ import annotations

import sys
from typing import Annotated

import typer

from trading_gateway.app.config import DEFAULT_CONFIG_FILE, load_dotenv_file, load_gateway_config
from trading_gateway.domain.models import public_market_choices
from trading_gateway.application.wallet.policy import private_account_choices, private_account_mode_choices
from trading_gateway.interfaces.cli import help as cli_help
from trading_gateway.interfaces.cli.commands.bootstrap import command_tail, echo_received, print_removed_command_hint
from trading_gateway.interfaces.cli.commands.ops import register_lab_commands, register_maintenance_commands
from trading_gateway.interfaces.cli.commands.runtime import register_runtime_commands
from trading_gateway.interfaces.cli.commands.trade_smoke import register_trade_commands
from trading_gateway.interfaces.cli.commands.venues import register_venue_commands
from trading_gateway.interfaces.cli.market import register_market_commands
from trading_gateway.interfaces.cli.risk import register_risk_commands
from trading_gateway.interfaces.cli.wallet import (
    register_wallet_commands,
    wallet_balance,
    wallet_orders,
    wallet_positions,
    wallet_snapshot,
    wallet_summary,
    wallet_transfer,
)
from trading_gateway.support.capabilities import build_capability_matrix
from trading_gateway.support.formatting import print_json
from trading_gateway.application.wallet.summary_runner import print_wallet_summary

JsonOpt = Annotated[bool, typer.Option("--json", help="print machine-readable JSON")]

app = typer.Typer(
    add_completion=False,
    invoke_without_command=True,
    context_settings={"help_option_names": ["-h", "--help"], "max_content_width": 120, "terminal_width": 120},
    help=cli_help.APP,
    epilog=cli_help.APP_EPILOG,
    rich_markup_mode=None,
)
trade_app = typer.Typer(add_completion=False, help=cli_help.TRADE, rich_markup_mode=None)
app.add_typer(trade_app, name="trade", help=cli_help.TRADE)
register_trade_commands(trade_app)
risk_app = typer.Typer(add_completion=False, help=cli_help.RISK, epilog=cli_help.RISK_EPILOG, rich_markup_mode=None)
app.add_typer(risk_app, name="risk", help=cli_help.RISK)
register_risk_commands(risk_app)
register_market_commands(app)
maintenance_app = typer.Typer(add_completion=False, help=cli_help.MAINTENANCE)
app.add_typer(maintenance_app, name="maintenance", help=cli_help.MAINTENANCE)
register_maintenance_commands(maintenance_app)
register_lab_commands(app)
register_runtime_commands(app)
register_venue_commands(app)
wallet_app = typer.Typer(add_completion=False, help=cli_help.WALLET, rich_markup_mode=None)
app.add_typer(wallet_app, name="wallet", help=cli_help.WALLET)
register_wallet_commands(wallet_app)


@app.callback()
def root(
    ctx: typer.Context,
    config_file: Annotated[str, typer.Option("--config-file", help="Trading Gateway TOML config path")] = str(DEFAULT_CONFIG_FILE),
    env_file: Annotated[str | None, typer.Option("--env-file", help="override dotenv path from config")] = None,
) -> None:
    echo_received()
    try:
        config = load_gateway_config(config_file)
    except ValueError as exc:
        raise typer.BadParameter(str(exc), param_hint="--config-file") from exc
    load_dotenv_file(env_file or config.dotenv_path)
    if ctx.invoked_subcommand is None:
        print_wallet_summary(None, json_output=False)
        raise typer.Exit(0)

@app.command("capabilities", help=cli_help.CAPABILITIES)
def capabilities(json_output: JsonOpt = False) -> None:
    reports = [row.to_dict() for row in build_capability_matrix()]
    payload = {"mode": "static_ccxt_capabilities", "private_verified": False, "reports": reports}
    if json_output:
        print_json(payload)
        return
    print("STATIC ccxt capability matrix; private exchange APIs are not verified by this command.")
    print("Use wallet balance / positions / orders or live trade smoke for real private checks.")
    print("EXCHANGE MARKET CCXT_TRADE CCXT_TRANSFER ADAPTER_TRADE ADAPTER_TRANSFER PRIVATE_VERIFIED NOTES")
    for row in reports:
        print(
            f"{row['exchange']:<7} {row['market']:<6} "
            f"{_yes_no(row['trade_supported']):<10} "
            f"{_yes_no(row['transfer_supported']):<13} "
            f"{_yes_no(row['adapter_trade_implemented']):<13} "
            f"{_yes_no(row['adapter_transfer_implemented']):<16} "
            f"{'not_checked':<16} notes={'; '.join(row['notes']) or '-'}"
        )

@app.command("summary", help=cli_help.SUMMARY)
def summary(
    exchange: Annotated[list[str] | None, typer.Option("--exchange", help="repeat to limit private exchanges; defaults to okx")] = None,
    account_mode: Annotated[str | None, typer.Option("--account-mode", help=f"private account mode: {private_account_mode_choices()}; default is live; use sim for OKX demo")] = None,
    json_output: JsonOpt = False,
    progress: Annotated[bool, typer.Option("--progress/--no-progress", help="show colored progress bar")] = True,
    with_positions: Annotated[bool, typer.Option("--with-positions", help="also query perp positions count")] = False,
    cache_ttl_sec: Annotated[float, typer.Option("--cache-ttl-sec", help="reuse local summary cache within N seconds")] = 0,
) -> None:
    wallet_summary(exchange, account_mode, json_output, progress, with_positions, cache_ttl_sec)

@app.command("snapshot", help=cli_help.SNAPSHOT)
def snapshot(
    exchange: Annotated[list[str] | None, typer.Option("--exchange", help="repeat to limit private exchanges; defaults to okx")] = None,
    account_mode: Annotated[str | None, typer.Option("--account-mode", help=f"private account mode: {private_account_mode_choices()}; default is live; use sim for OKX demo")] = None,
    json_output: JsonOpt = False,
    nonzero_only: Annotated[bool, typer.Option("--nonzero-only/--all-assets", help="hide zero asset rows")] = True,
    active_positions_only: Annotated[bool, typer.Option("--active-positions-only/--all-positions", help="hide flat perp positions")] = True,
) -> None:
    wallet_snapshot(exchange, account_mode, json_output, nonzero_only, active_positions_only)

@app.command("balance", help=cli_help.TOP_BALANCE)
def balance(
    exchange: Annotated[str, typer.Argument(help=f"private account exchange: {private_account_choices()}")],
    market: Annotated[str, typer.Argument(help=public_market_choices(include_both=True))] = "both",
    account_mode: Annotated[str | None, typer.Option("--account-mode", help=f"private account mode: {private_account_mode_choices()}; default is live; use sim for OKX demo")] = None,
    nonzero_only: Annotated[bool, typer.Option("--nonzero-only/--all-assets", help="hide zero asset rows")] = True,
    raw: Annotated[bool, typer.Option("--raw", help="show debug raw exchange/ccxt wallet payload")] = False,
) -> None:
    wallet_balance(exchange, market, account_mode, nonzero_only, raw)

@app.command("positions", help=cli_help.TOP_POSITIONS)
def positions(
    exchange: Annotated[str, typer.Argument(help=f"private account exchange: {private_account_choices()}")],
    symbol: Annotated[str | None, typer.Argument(help="optional perp symbol, e.g. BTC/USDT:USDT")] = None,
    account_mode: Annotated[str | None, typer.Option("--account-mode", help=f"private account mode: {private_account_mode_choices()}; default is live; use sim for OKX demo")] = None,
) -> None:
    wallet_positions(exchange, symbol, account_mode)


@app.command("orders", help=cli_help.TOP_ORDERS)
def orders(
    exchange: Annotated[str, typer.Argument(help=f"private account exchange: {private_account_choices()}")],
    market: Annotated[str, typer.Argument(help="spot/perp")],
    symbol: Annotated[str, typer.Argument(help="symbol, e.g. BTC/USDT")],
    account_mode: Annotated[str | None, typer.Option("--account-mode", help=f"private account mode: {private_account_mode_choices()}; default is live; use sim for OKX demo")] = None,
) -> None:
    wallet_orders(exchange, market, symbol, account_mode)


@app.command("transfer", help=cli_help.TOP_TRANSFER)
def transfer(
    exchange: Annotated[str, typer.Argument(help=f"private account exchange: {private_account_choices()}")],
    code: Annotated[str, typer.Argument(help="asset code, e.g. USDT")],
    amount: Annotated[float, typer.Argument(help="amount")],
    from_account: Annotated[str, typer.Argument(help="source account, e.g. spot")],
    to_account: Annotated[str, typer.Argument(help="destination account, e.g. perp")],
    live: Annotated[bool, typer.Option()] = False,
    confirm: Annotated[str, typer.Option()] = "",
) -> None:
    wallet_transfer(exchange, code, amount, from_account, to_account, live, confirm)

def _yes_no(value: object) -> str:
    return "yes" if bool(value) else "no"


def main(argv: list[str] | None = None) -> int:
    args = list(sys.argv[1:] if argv is None else argv)
    if print_removed_command_hint(command_tail(args)):
        return 2
    app(args=args, prog_name="tbot")
    return 0
