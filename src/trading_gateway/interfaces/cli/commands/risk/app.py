from __future__ import annotations

import sys
from typing import Annotated

import typer

from trading_gateway.application.risk.okx_algo import (
    build_okx_bracket_plan,
    build_okx_json_plan,
    cancel_okx_algo_orders,
    fetch_okx_algo_orders,
    fetch_okx_recent_history,
    load_okx_json_plan,
    place_okx_bracket_orders,
    place_okx_guarded_json_plan_orders,
    place_okx_json_plan_orders,
    prepare_okx_json_plan_for_live,
)
from trading_gateway.support.formatting import print_json

from .live import require_confirm, with_okx_client
from .rendering import _print_algo_orders, _print_live_result, _print_plan
from .epilog import BRACKET_EPILOG, CANCEL_EPILOG, ORDERS_EPILOG, PLAN_EPILOG

JsonOpt = Annotated[bool, typer.Option("--json", help="Print machine-readable JSON instead of human-readable text.", rich_help_panel="Output")]


def register_risk_commands(app: typer.Typer) -> None:
    app.command("apply", help="Compile or submit a strict OKX JSON order plan.")(risk_apply)
    app.command("guarded-apply", help="Precheck flat OKX state, submit a strict JSON plan, and verify resting or filled outcome.")(risk_guarded_apply)
    app.command("plan", help="Build an OKX reduce-only TP/SL plan for an existing position; never places orders.", epilog=PLAN_EPILOG)(risk_plan)
    app.command("bracket", help="Place OKX reduce-only TP/SL algo orders after exact confirmation.", epilog=BRACKET_EPILOG)(risk_bracket)
    app.command("orders", help="List pending OKX algo orders, optionally filtered to one instrument.", epilog=ORDERS_EPILOG)(risk_orders)
    app.command("history", help="Read recent OKX order, fill, bill, and algo history; never places orders.")(risk_history)
    app.command("cancel", help="Cancel pending OKX algo orders after exact confirmation.", epilog=CANCEL_EPILOG)(risk_cancel)


def risk_apply(
    plan_file: Annotated[str, typer.Argument(help="JSON plan path, or - to read JSON from stdin")],
    live: Annotated[bool, typer.Option("--live/--dry-run", help="submit live orders only with --live", rich_help_panel="Live Safety")] = False,
    confirm: Annotated[str, typer.Option("--confirm", help="exact confirmation phrase from dry-run plan", rich_help_panel="Live Safety")] = "",
    json_output: JsonOpt = False,
) -> None:
    try:
        raw_source = sys.stdin.read() if str(plan_file).strip() == "-" else str(plan_file)
        raw_plan = load_okx_json_plan(raw_source)
        compiled = build_okx_json_plan(raw_plan)
    except ValueError as exc:
        raise typer.BadParameter(str(exc)) from exc
    if not live:
        print_json(compiled) if json_output else _print_live_result(compiled)
        return
    require_confirm(confirm, compiled["confirm_phrase"], "json plan")
    live_plan = prepare_okx_json_plan_for_live(raw_plan)
    payload = with_okx_client(lambda client: place_okx_json_plan_orders(client, live_plan))
    print_json(payload) if json_output else _print_live_result(payload)


def risk_guarded_apply(
    plan_file: Annotated[str, typer.Argument(help="JSON plan path, or - to read JSON from stdin")],
    live: Annotated[bool, typer.Option("--live/--dry-run", help="submit live orders only with --live", rich_help_panel="Live Safety")] = False,
    confirm: Annotated[str, typer.Option("--confirm", help="exact confirmation phrase from dry-run plan", rich_help_panel="Live Safety")] = "",
    json_output: JsonOpt = False,
) -> None:
    try:
        raw_source = sys.stdin.read() if str(plan_file).strip() == "-" else str(plan_file)
        raw_plan = load_okx_json_plan(raw_source)
        compiled = build_okx_json_plan(raw_plan)
    except ValueError as exc:
        raise typer.BadParameter(str(exc)) from exc
    if not live:
        print_json(compiled) if json_output else _print_live_result(compiled)
        return
    require_confirm(confirm, compiled["confirm_phrase"], "guarded json plan")
    live_plan = prepare_okx_json_plan_for_live(raw_plan)
    payload = with_okx_client(lambda client: place_okx_guarded_json_plan_orders(client, live_plan))
    print_json(payload) if json_output else _print_live_result(payload)


def risk_plan(
    exchange: Annotated[str, typer.Argument(help="currently only okx")],
    symbol: Annotated[str, typer.Argument(help="OKX instrument id, e.g. BTC-USDT-SWAP")],
    side: Annotated[str, typer.Argument(help="current position side: long/short")],
    size: Annotated[float, typer.Argument(help="position size to close, in OKX contract units")],
    take_profit: Annotated[float | None, typer.Option("--take-profit", help="take-profit trigger price", rich_help_panel="TP/SL")] = None,
    stop_loss: Annotated[float | None, typer.Option("--stop-loss", help="stop-loss trigger price", rich_help_panel="TP/SL")] = None,
    margin_mode: Annotated[str, typer.Option("--margin-mode", help="cross/isolated", rich_help_panel="Execution Settings")] = "cross",
    trigger_px_type: Annotated[str, typer.Option("--trigger-px-type", help="last/index/mark", rich_help_panel="Execution Settings")] = "last",
    order_px: Annotated[str, typer.Option("--order-px", help="-1 means market order after trigger on OKX", rich_help_panel="Execution Settings")] = "-1",
    json_output: JsonOpt = False,
) -> None:
    plan = _build_plan(exchange, symbol, side, size, take_profit, stop_loss, margin_mode, trigger_px_type, order_px)
    if json_output:
        print_json(plan)
        return
    _print_plan(plan)


def risk_bracket(
    exchange: Annotated[str, typer.Argument(help="currently only okx")],
    symbol: Annotated[str, typer.Argument(help="OKX instrument id, e.g. BTC-USDT-SWAP")],
    side: Annotated[str, typer.Argument(help="current position side: long/short")],
    size: Annotated[float, typer.Argument(help="position size to close, in OKX contract units")],
    take_profit: Annotated[float | None, typer.Option("--take-profit", help="take-profit trigger price", rich_help_panel="TP/SL")] = None,
    stop_loss: Annotated[float | None, typer.Option("--stop-loss", help="stop-loss trigger price", rich_help_panel="TP/SL")] = None,
    margin_mode: Annotated[str, typer.Option("--margin-mode", help="cross/isolated", rich_help_panel="Execution Settings")] = "cross",
    trigger_px_type: Annotated[str, typer.Option("--trigger-px-type", help="last/index/mark", rich_help_panel="Execution Settings")] = "last",
    order_px: Annotated[str, typer.Option("--order-px", help="-1 means market order after trigger on OKX", rich_help_panel="Execution Settings")] = "-1",
    live: Annotated[bool, typer.Option("--live/--dry-run", help="place live orders only with --live", rich_help_panel="Live Safety")] = False,
    confirm: Annotated[str, typer.Option("--confirm", help="exact confirmation phrase from risk plan", rich_help_panel="Live Safety")] = "",
    json_output: JsonOpt = False,
) -> None:
    plan = _build_plan(exchange, symbol, side, size, take_profit, stop_loss, margin_mode, trigger_px_type, order_px)
    if not live:
        if json_output:
            print_json(plan)
            return
        _print_plan(plan)
        return
    expected = plan["confirm_phrase"]
    require_confirm(confirm, expected, "live bracket")
    payload = with_okx_client(lambda client: place_okx_bracket_orders(client, plan))
    print_json(payload) if json_output else _print_live_result(payload)


def risk_orders(
    exchange: Annotated[str, typer.Argument(help="currently only okx")],
    symbol: Annotated[str | None, typer.Argument(help="optional OKX instrument id, e.g. BTC-USDT-SWAP")] = None,
    ord_type: Annotated[str, typer.Option("--ord-type", help="conditional, oco, trigger, or all")] = "conditional",
    json_output: JsonOpt = False,
) -> None:
    _validate_okx(exchange)
    try:
        payload = with_okx_client(lambda client: fetch_okx_algo_orders(client, symbol, ord_type=ord_type))
    except typer.BadParameter:
        raise
    except ValueError as exc:
        raise typer.BadParameter(str(exc), param_hint="--ord-type") from exc
    print_json(payload) if json_output else _print_algo_orders(payload)


def risk_cancel(
    exchange: Annotated[str, typer.Argument(help="currently only okx")],
    symbol: Annotated[str, typer.Argument(help="OKX instrument id, e.g. BTC-USDT-SWAP")],
    algo_ids: Annotated[list[str], typer.Argument(help="one or more OKX algoId values")],
    confirm: Annotated[str, typer.Option("--confirm", help="exact confirmation phrase")] = "",
    json_output: JsonOpt = False,
) -> None:
    _validate_okx(exchange)
    expected = f"LIVE_CANCEL_ALGOS:okx:{str(symbol).strip().upper()}:{','.join(algo_ids)}"
    require_confirm(confirm, expected, "cancel")
    payload = with_okx_client(lambda client: cancel_okx_algo_orders(client, symbol, algo_ids))
    print_json(payload) if json_output else _print_live_result(payload)


def risk_history(
    exchange: Annotated[str, typer.Argument(help="currently only okx")],
    symbol: Annotated[str | None, typer.Argument(help="optional OKX instrument id, e.g. BTC-USDT-SWAP")] = None,
    limit: Annotated[int, typer.Option("--limit", help="rows per history request, max 100")] = 20,
    kind: Annotated[str, typer.Option("--kind", help="algo history kind: trigger, conditional, oco, or all")] = "all",
    state: Annotated[str, typer.Option("--state", help="algo history state: effective, canceled, order_failed, or all")] = "all",
    algo_id: Annotated[str | None, typer.Option("--algo-id", help="filter rows related to an OKX algoId")] = None,
    order_id: Annotated[str | None, typer.Option("--order-id", help="filter rows related to an OKX ordId")] = None,
    json_output: JsonOpt = False,
) -> None:
    _validate_okx(exchange)
    payload = with_okx_client(
        lambda client: fetch_okx_recent_history(client, symbol, limit=limit, kind=kind, state=state, algo_id=algo_id, order_id=order_id)
    )
    print_json(payload) if json_output else _print_live_result(payload)


def _build_plan(
    exchange: str,
    symbol: str,
    side: str,
    size: float,
    take_profit: float | None,
    stop_loss: float | None,
    margin_mode: str,
    trigger_px_type: str,
    order_px: str,
) -> dict:
    try:
        return build_okx_bracket_plan(
            exchange,
            symbol,
            side,
            size,
            take_profit=take_profit,
            stop_loss=stop_loss,
            margin_mode=margin_mode,
            trigger_px_type=trigger_px_type,
            order_px=order_px,
        )
    except ValueError as exc:
        raise typer.BadParameter(str(exc)) from exc


def _validate_okx(exchange: str) -> None:
    if str(exchange or "").strip().lower() != "okx":
        raise typer.BadParameter("risk commands currently support only okx", param_hint="exchange")
