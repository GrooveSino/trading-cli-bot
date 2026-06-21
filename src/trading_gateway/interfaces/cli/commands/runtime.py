from __future__ import annotations

from typing import Annotated, Any

import typer
from rich.console import Console
from rich.panel import Panel
from rich.table import Table

from trading_gateway.application.marketdata.btcusdt import run_btcusdt_marketdata_daemon
from trading_gateway.application.marketdata.http import run_snapshot_http_server
from trading_gateway.application.marketdata.multi import run_multi_marketdata_collector
from trading_gateway.interfaces.cli import help as cli_help
from trading_gateway.interfaces.daemon.client import DaemonClientError, daemon_http_get, read_daemon_metadata
from trading_gateway.interfaces.daemon.server import start_daemon, stop_daemon
from trading_gateway.interfaces.web.server import run_web
from trading_gateway.support.formatting import print_json


def register_runtime_commands(app: typer.Typer) -> None:
    app.command("web", help=cli_help.WEB)(web)
    _register_marketdata_commands(app)
    daemon_app = typer.Typer(add_completion=False, help="Manage the local Trading Gateway live daemon.")
    daemon_app.command("start", help="Start the localhost live trading daemon.")(daemon_start)
    daemon_app.command("status", help="Show daemon reachability and per-route health.")(daemon_status)
    daemon_app.command("stop", help="Stop the localhost live trading daemon.")(daemon_stop)
    app.add_typer(daemon_app, name="daemon")


def _register_marketdata_commands(app: typer.Typer) -> None:
    marketdata_app = typer.Typer(add_completion=False, help="Run hosted marketdata collectors and snapshot writers.")
    marketdata_app.command("collector", help="Run multi venue/symbol marketdata collector for OKX live and OKX sim BTC/ETH.")(marketdata_collector)
    marketdata_app.command("btcusdt-daemon", help="Compatibility collector for legacy OKX live BTC snapshots.")(btcusdt_daemon)
    marketdata_app.command("btcusdt-http", help="Serve read-only multi-snapshot JSON over private HTTP.")(btcusdt_http)
    app.add_typer(marketdata_app, name="marketdata")


def marketdata_collector(
    once: Annotated[bool, typer.Option("--once", help="Collect all configured snapshots, write them, and exit.")] = False,
    json_output: Annotated[bool, typer.Option("--json", help="Print generated snapshot summary JSON when used with --once.")] = False,
    account: Annotated[bool, typer.Option("--account/--no-account", help="include private account overlays in generated snapshots")] = True,
) -> None:
    run_multi_marketdata_collector(once=once, json_output=json_output, include_account=account)


def btcusdt_daemon(
    once: Annotated[bool, typer.Option("--once", help="Collect one snapshot, write it, and exit.")] = False,
    json_output: Annotated[bool, typer.Option("--json", help="Print generated snapshot JSON when used with --once.")] = False,
) -> None:
    run_btcusdt_marketdata_daemon(once=once, json_output=json_output)


def btcusdt_http(
    host: Annotated[str | None, typer.Option("--host", help="HTTP bind host; use Tailscale/firewall isolation.")] = None,
    port: Annotated[int | None, typer.Option("--port", help="HTTP snapshot service port.")] = None,
) -> None:
    run_snapshot_http_server(host=host, port=port)


def web(
    host: Annotated[str, typer.Option("--host", help="bind host; localhost is recommended")] = "127.0.0.1",
    port: Annotated[int, typer.Option("--port", help="dashboard port")] = 8765,
    open_browser: Annotated[bool, typer.Option("--open/--no-open", help="open browser after startup")] = True,
    reload: Annotated[bool, typer.Option("--reload", help="reload web server during development")] = False,
) -> None:
    run_web(host, port, open_browser=open_browser, reload=reload)


def daemon_start() -> None:
    start_daemon()


def daemon_stop() -> None:
    stop_daemon()


def daemon_status(
    json_output: Annotated[bool, typer.Option("--json", help="print machine-readable JSON")] = False,
) -> None:
    metadata = read_daemon_metadata()
    try:
        payload = daemon_http_get("/api/daemon/status", metadata=metadata)
    except DaemonClientError:
        payload = _unreachable_daemon_payload(metadata)
    if json_output:
        print_json(payload)
        return
    _print_daemon_status(payload)


def _unreachable_daemon_payload(metadata: dict[str, Any] | None) -> dict[str, Any]:
    return {
        "mode": "trading_gateway_daemon",
        "status": "unreachable",
        "pid": None if not metadata else metadata.get("pid"),
        "host": None if not metadata else metadata.get("host"),
        "port": None if not metadata else metadata.get("port"),
        "config_file": None if not metadata else metadata.get("config_file"),
        "active_live_job": None,
        "routes": [],
    }


def _print_daemon_status(payload: dict[str, Any]) -> None:
    table = Table.grid(padding=(0, 2))
    table.add_column(style="bold")
    table.add_column()
    table.add_row("status", str(payload.get("status")))
    table.add_row("pid", _text(payload.get("pid")))
    table.add_row("host", _text(payload.get("host")))
    table.add_row("port", _text(payload.get("port")))
    table.add_row("config", _text(payload.get("config_file")))
    table.add_row("active_live_job", _text((payload.get("active_live_job") or {}).get("job_id")))
    route_table = _daemon_route_table(payload)
    Console(stderr=True, width=120).print(Panel(table, title="Trading Gateway Daemon"))
    if payload.get("routes"):
        Console(stderr=True, width=120).print(route_table)


def _daemon_route_table(payload: dict[str, Any]) -> Table:
    route_table = Table(show_header=True, header_style="bold")
    route_table.add_column("Route")
    route_table.add_column("Status")
    route_table.add_column("LastRefreshAgeSec")
    route_table.add_column("LastError")
    for row in payload.get("routes") or []:
        route_table.add_row(
            _text(row.get("route")),
            _text(row.get("status")),
            _text(row.get("last_private_refresh_age_sec")),
            _text(row.get("last_error")),
        )
    return route_table


def _text(value: Any) -> str:
    return "-" if value is None else str(value)
