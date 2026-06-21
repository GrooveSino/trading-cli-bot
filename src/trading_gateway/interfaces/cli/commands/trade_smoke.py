from __future__ import annotations

from typing import Annotated, Any

import typer

from trading_gateway.application.trade_smoke.planning import build_order_plan, validate_live_request
from trading_gateway.application.trade_smoke.trading import run_trade_smoke
from trading_gateway.domain.models import OrderIntent, display_market, public_market_choices
from trading_gateway.infrastructure.exchange.factory import build_ccxt_client, close_client
from trading_gateway.infrastructure.exchange.static_client import StaticPriceClient
from trading_gateway.infrastructure.exchange.account_modes import account_mode_choices
from trading_gateway.interfaces.cli import help as cli_help
from trading_gateway.support.formatting import print_json, write_report


ExchangeOpt = Annotated[str, typer.Option(help="exchange: binance/okx/gate/mexc")]
MarketOpt = Annotated[str, typer.Option(help=f"market: {public_market_choices()}")]
JsonOpt = Annotated[bool, typer.Option("--json", help="print machine-readable JSON")]
AccountModeOpt = Annotated[str | None, typer.Option("--account-mode", help=f"private account mode for live smoke: {account_mode_choices()}; default is live; use sim for OKX demo")]


def register_trade_commands(app: typer.Typer) -> None:
    app.command("plan", help=cli_help.PLAN, epilog=cli_help.TRADE_PLAN_EPILOG)(trade_plan)
    app.command("smoke", help=cli_help.SMOKE, epilog=cli_help.SMOKE_EPILOG)(trade_smoke)


def trade_plan(
    exchange: ExchangeOpt,
    market: MarketOpt,
    symbol: Annotated[str, typer.Option()],
    side: Annotated[str, typer.Option(help="buy/sell")],
    quote_usdt: Annotated[float, typer.Option("--quote-usdt")],
    leverage: Annotated[int, typer.Option()] = 1,
    account_mode: AccountModeOpt = None,
    margin_mode: Annotated[str, typer.Option("--margin-mode")] = "cross",
    position_mode: Annotated[str, typer.Option("--position-mode")] = "oneway",
    last_price: Annotated[float | None, typer.Option("--last-price")] = None,
    json_output: JsonOpt = False,
) -> None:
    args = locals()
    plan = build_plan(args)
    payload = {"mode": "plan", **plan.to_dict()}
    if json_output:
        print_json(payload)
        return
    print(f"plan {plan.exchange} {display_market(plan.market)} {plan.side} {plan.symbol} amount={plan.amount}")
    print(f"confirm: {plan.live_confirm_phrase}")


def trade_smoke(
    exchange: ExchangeOpt,
    market: MarketOpt,
    symbol: Annotated[str, typer.Option()],
    side: Annotated[str, typer.Option(help="buy/sell")],
    quote_usdt: Annotated[float, typer.Option("--quote-usdt")],
    leverage: Annotated[int, typer.Option()] = 1,
    account_mode: AccountModeOpt = None,
    margin_mode: Annotated[str, typer.Option("--margin-mode")] = "cross",
    position_mode: Annotated[str, typer.Option("--position-mode")] = "oneway",
    last_price: Annotated[float | None, typer.Option("--last-price")] = None,
    json_output: JsonOpt = False,
    live: Annotated[bool, typer.Option()] = False,
    confirm: Annotated[str, typer.Option()] = "",
    no_close: Annotated[bool, typer.Option("--no-close")] = False,
    report_dir: Annotated[str, typer.Option("--report-dir")] = "",
) -> None:
    args = locals()
    intent = order_intent(args)
    try:
        validate_live_request(intent, live=live, confirm=confirm)
    except ValueError as exc:
        raise typer.BadParameter(str(exc), param_hint="--confirm") from exc
    client = plan_client(args, private=live)
    try:
        result = run_trade_smoke(
            client,
            intent,
            live=live,
            confirm=confirm,
            close_after=not no_close,
            last_price=last_price,
        )
    finally:
        close_client(client)
    report_path = write_report(report_dir, "trade_smoke_report.json", result)
    if report_path:
        result["report_path"] = str(report_path)
    print_json(result) if json_output else print(result)


def build_plan(args: dict[str, Any]) -> Any:
    client = plan_client(args)
    try:
        return build_order_plan(client, order_intent(args), last_price=args.get("last_price"))
    finally:
        close_client(client)


def plan_client(args: dict[str, Any], *, private: bool = False) -> Any:
    if args.get("last_price") is not None and not private:
        return StaticPriceClient(args["symbol"], args["market"], args["last_price"])
    return build_ccxt_client(args["exchange"], args["market"], require_private=private, account_mode=args.get("account_mode"))


def order_intent(args: dict[str, Any]) -> OrderIntent:
    return OrderIntent(
        exchange=args["exchange"],
        market=args["market"],
        symbol=args["symbol"],
        side=args["side"],
        quote_usdt=args["quote_usdt"],
        leverage=args["leverage"],
        margin_mode=args["margin_mode"],
        position_mode=args["position_mode"],
        account_mode=args.get("account_mode"),
    )
