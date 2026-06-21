from __future__ import annotations

from pathlib import Path
from time import perf_counter
from typing import Annotated, Any

import typer

from trading_gateway.app.config import get_gateway_config
from trading_gateway.application.maintenance.okx_position_maintenance import (
    DEFAULT_JOURNAL_DIR,
    DEFAULT_STATE_PATH,
    MaintenanceConfig,
    append_journal,
    render_journal_entry,
    run_okx_maintenance,
    write_state,
)
from trading_gateway.infrastructure.exchange.factory import build_ccxt_client, close_client
from trading_gateway.infrastructure.exchange.account_modes import account_mode_choices
from trading_gateway.interfaces.cli import help as cli_help
from trading_gateway.interfaces.cli.commands.pair_workflow import (
    _wait_for_job,
    pair_close_plan,
    pair_close_resume,
    pair_close_run,
    pair_close_status,
    pair_plan,
    pair_resume,
    pair_run,
    pair_status,
)
from trading_gateway.interfaces.cli.commands.planning import build_single_leg_cli_plan
from trading_gateway.interfaces.cli.presenters import print_plan_brief, print_run_brief
from trading_gateway.interfaces.daemon.client import DaemonClientError, daemon_http_post, ensure_daemon_ready_for_live
from trading_gateway.support.formatting import print_json
from trading_gateway.support.redaction import redact_text

JsonOpt = Annotated[bool, typer.Option("--json", help="print machine-readable JSON")]
AccountModeOpt = Annotated[str | None, typer.Option("--account-mode", help=f"private account mode for live run: {account_mode_choices()}; default is live; use sim for OKX demo")]


def register_lab_commands(app: typer.Typer) -> None:
    app.command("plan", help=cli_help.LAB_PLAN, epilog=cli_help.LAB_PLAN_EPILOG)(lab_plan)
    app.command("run", help=cli_help.LAB_RUN, epilog=cli_help.LAB_RUN_EPILOG)(lab_run)
    app.command("pair-plan", help=cli_help.PAIR_PLAN)(pair_plan)
    app.command("pair-run", help=cli_help.PAIR_RUN)(pair_run)
    app.command("pair-close-plan", help=cli_help.PAIR_CLOSE_PLAN)(pair_close_plan)
    app.command("pair-close-run", help=cli_help.PAIR_CLOSE_RUN)(pair_close_run)
    app.command("pair-status", help=cli_help.PAIR_STATUS)(pair_status)
    app.command("pair-resume", help=cli_help.PAIR_RESUME)(pair_resume)
    app.command("pair-close-status", help=cli_help.PAIR_STATUS)(pair_close_status)
    app.command("pair-close-resume", help=cli_help.PAIR_RESUME)(pair_close_resume)


def register_maintenance_commands(app: typer.Typer) -> None:
    app.command("okx", help="Run a dry-run OKX adaptive position-maintenance audit.")(maintenance_okx)


def lab_plan(
    exchange: Annotated[str, typer.Argument(help="exchange: binance/okx/gate/mexc")],
    market: Annotated[str, typer.Argument(help="spot/perp")],
    action: Annotated[str, typer.Argument(help="spot: buy/sell; perp: open-long/open-short/close-long/close-short")],
    symbol: Annotated[str, typer.Argument(help="route universe symbol, e.g. SOL/USDT")],
    quote_usdt: Annotated[float | None, typer.Argument(help="quote USDT; optional for close actions")] = None,
    account_mode: AccountModeOpt = None,
    bbo: Annotated[bool, typer.Option("--bbo", help="use configured maker/BBO order")] = False,
    limit_price: Annotated[float | None, typer.Option("--limit-price", help="fixed limit order price; leaves a resting order when used for live perp")] = None,
    take_profit: Annotated[float | None, typer.Option("--take-profit", help="OKX perp attached take-profit trigger price")] = None,
    stop_loss: Annotated[float | None, typer.Option("--stop-loss", help="OKX perp attached stop-loss trigger price")] = None,
    margin_mode: Annotated[str | None, typer.Option("--margin-mode", help="OKX perp margin mode: cross/isolated")] = None,
    leverage: Annotated[int | None, typer.Option("--leverage", help="perp leverage override for this order")] = None,
    last_price: Annotated[float | None, typer.Option("--last-price", help="static price for dry planning/tests")] = None,
    json_output: JsonOpt = False,
) -> None:
    started = perf_counter()
    plan = build_lab_plan(exchange, market, action, symbol, quote_usdt, bbo, last_price, limit_price, take_profit, stop_loss, margin_mode, leverage, account_mode)
    query_ms = _elapsed_ms(started)
    print_plan_brief(plan, query_ms)
    if json_output:
        print_json({"mode": "single_leg_plan", "query_ms": query_ms, "plan": plan})


def lab_run(
    exchange: Annotated[str, typer.Argument(help="exchange: binance/okx/gate/mexc")],
    market: Annotated[str, typer.Argument(help="spot/perp")],
    action: Annotated[str, typer.Argument(help="spot: buy/sell; perp: open-long/open-short/close-long/close-short")],
    symbol: Annotated[str, typer.Argument(help="route universe symbol, e.g. SOL/USDT")],
    quote_usdt: Annotated[float | None, typer.Argument(help="quote USDT; optional for close actions")] = None,
    account_mode: AccountModeOpt = None,
    bbo: Annotated[bool, typer.Option("--bbo", help="use configured maker/BBO order")] = False,
    limit_price: Annotated[float | None, typer.Option("--limit-price", help="fixed limit order price; leaves a resting order when used for live perp")] = None,
    take_profit: Annotated[float | None, typer.Option("--take-profit", help="OKX perp attached take-profit trigger price")] = None,
    stop_loss: Annotated[float | None, typer.Option("--stop-loss", help="OKX perp attached stop-loss trigger price")] = None,
    margin_mode: Annotated[str | None, typer.Option("--margin-mode", help="OKX perp margin mode: cross/isolated")] = None,
    leverage: Annotated[int | None, typer.Option("--leverage", help="perp leverage override for this order")] = None,
    confirm: Annotated[str, typer.Option("--confirm", help="exact live confirmation phrase")] = "",
    timeout_sec: Annotated[float | None, typer.Option("--timeout-sec", help="override config order_timeout_sec")] = None,
    max_requotes: Annotated[int | None, typer.Option("--max-requotes", help="override config max_requotes")] = None,
    json_output: JsonOpt = False,
) -> None:
    started = perf_counter()
    try:
        payload = _run_lab_job(
            exchange,
            market,
            action,
            symbol,
            quote_usdt,
            bbo,
            limit_price,
            take_profit,
            stop_loss,
            margin_mode,
            leverage,
            account_mode,
            confirm,
            timeout_sec,
            max_requotes,
        )
    except DaemonClientError as exc:
        raise typer.BadParameter(str(exc), param_hint="daemon") from exc
    payload["query_ms"] = _elapsed_ms(started)
    print_run_brief(payload)
    if json_output:
        print_json(payload)
    if payload.get("final_status") in {"blocked", "submit_error", "target_not_reached", "asset_target_not_reached", "close_all_pending", "force_close_failed"}:
        raise typer.Exit(1)


def _run_lab_job(
    exchange: str,
    market: str,
    action: str,
    symbol: str,
    quote_usdt: float | None,
    bbo: bool,
    limit_price: float | None,
    take_profit: float | None,
    stop_loss: float | None,
    margin_mode: str | None,
    leverage: int | None,
    account_mode: str | None,
    confirm: str,
    timeout_sec: float | None,
    max_requotes: int | None,
) -> dict[str, Any]:
    ensure_daemon_ready_for_live(config_file=get_gateway_config().path)
    build_lab_plan(exchange, market, action, symbol, quote_usdt, bbo, None, limit_price, take_profit, stop_loss, margin_mode, leverage, account_mode)
    body = {
        "exchange": exchange,
        "market": market,
        "action": action,
        "symbol": symbol,
        "quote_usdt": quote_usdt,
        "bbo": bbo,
        "limit_price": limit_price,
        "take_profit": take_profit,
        "stop_loss": stop_loss,
        "margin_mode": margin_mode,
        "leverage": leverage,
        "account_mode": account_mode,
        "confirm": confirm,
        "timeout_sec": timeout_sec,
        "max_requotes": max_requotes,
    }
    started_job = daemon_http_post("/api/lab/run", body)
    return _wait_for_job(started_job["job_id"])


def build_lab_plan(
    exchange: str,
    market: str,
    action: str,
    symbol: str,
    quote_usdt: float | None,
    bbo: bool,
    last_price: float | None,
    limit_price: float | None,
    take_profit: float | None,
    stop_loss: float | None,
    margin_mode: str | None,
    leverage: int | None,
    account_mode: str | None = None,
) -> dict[str, Any]:
    try:
        return build_single_leg_cli_plan(
            exchange=exchange,
            market=market,
            action=action,
            symbol=symbol,
            quote_usdt=quote_usdt,
            bbo=bbo,
            last_price=last_price,
            limit_price=limit_price,
            take_profit=take_profit,
            stop_loss=stop_loss,
            margin_mode=margin_mode,
            leverage=leverage,
            account_mode=account_mode,
        )
    except ValueError as exc:
        raise typer.BadParameter(str(exc), param_hint="ACTION") from exc


def maintenance_okx(
    state_path: Annotated[Path, typer.Option("--state-path", help="overwrite-only handoff state path")] = DEFAULT_STATE_PATH,
    journal_dir: Annotated[Path, typer.Option("--journal-dir", help="dated trade journal directory")] = DEFAULT_JOURNAL_DIR,
    target_positions: Annotated[int, typer.Option("--target-positions", min=1, help="protected-position workflow target")] = 4,
    refusal_limit: Annotated[int, typer.Option("--refusal-limit", min=0, help="daily refusal audit threshold")] = 6,
    scan_limit: Annotated[int, typer.Option("--scan-limit", min=10, help="maximum symbols reviewed when gap exists")] = 180,
    scan: Annotated[bool, typer.Option("--scan/--no-scan", help="scan candidates only when protected-position gap exists")] = True,
    write_journal: Annotated[bool, typer.Option("--write-journal/--no-write-journal", help="append an audit entry to dated journal")] = False,
    write_handoff: Annotated[bool, typer.Option("--write-state/--no-write-state", help="overwrite automation-session-state.md")] = False,
    json_output: JsonOpt = False,
) -> None:
    config = MaintenanceConfig(target_positions=target_positions, refusal_limit_per_day=refusal_limit, scan_limit=scan_limit)
    client = build_ccxt_client("okx", "swap", require_private=True, timeout_ms=20000)
    try:
        report = run_okx_maintenance(client, state_path=state_path, journal_dir=journal_dir, config=config, scan=scan)
    except Exception as exc:  # noqa: BLE001 - CLI boundary returns concise redacted exchange errors.
        raise typer.BadParameter(redact_text(f"{type(exc).__name__}: {exc}")) from exc
    finally:
        close_client(client)
    _emit_maintenance_report(report, config, write_journal, write_handoff, json_output)


def _emit_maintenance_report(
    report: object,
    config: MaintenanceConfig,
    write_journal: bool,
    write_handoff: bool,
    json_output: bool,
) -> None:
    if write_journal:
        append_journal(report)  # type: ignore[arg-type]
    if write_handoff:
        write_state(report, config)  # type: ignore[arg-type]
    payload = report.to_dict()  # type: ignore[attr-defined]
    payload["write_journal"] = write_journal
    payload["write_state"] = write_handoff
    if json_output:
        print_json(payload)
        return
    print(render_journal_entry(report))  # type: ignore[arg-type]
    print(f"write_journal={write_journal} path={report.journal_path}")  # type: ignore[attr-defined]
    print(f"write_state={write_handoff} path={report.state_path}")  # type: ignore[attr-defined]


def _elapsed_ms(started: float) -> int:
    return int((perf_counter() - started) * 1000)
