from __future__ import annotations

import os
import sys
import time

import typer


def echo_received() -> None:
    if is_help_request():
        return
    command = cli_command_label()
    transport = cli_transport()
    elapsed_ms = first_response_ms()
    typer.echo(
        f"tbot: received '{command}', processing... transport={transport} first_response_ms={elapsed_ms}",
        err=True,
    )


def first_response_ms() -> int:
    raw = os.environ.get("TG_CLI_START_TS", "")
    try:
        started_at = float(raw)
    except ValueError:
        started_at = time.time()
    return max(0, int((time.time() - started_at) * 1000))


def cli_command_label(argv: list[str] | None = None) -> str:
    commands, _ = parsed_cli_tokens(argv)
    if not commands:
        return "summary"
    return " ".join(commands[:2]) if commands[0] in {"trade", "daemon"} and len(commands) > 1 else commands[0]


def cli_transport(argv: list[str] | None = None) -> str:
    commands, args = parsed_cli_tokens(argv)
    if not commands:
        return "local"
    first = commands[0]
    second = commands[1] if len(commands) > 1 else ""
    if first in {"run", "pair-run", "pair-close-run"}:
        return "daemon"
    if first == "transfer" and "--live" in args:
        return "daemon"
    if first == "wallet" and second == "transfer" and "--live" in args:
        return "daemon"
    return "local"


def is_help_request(argv: list[str] | None = None) -> bool:
    args = list(sys.argv[1:] if argv is None else argv)
    return "-h" in args or "--help" in args


def parsed_cli_tokens(argv: list[str] | None = None) -> tuple[list[str], list[str]]:
    args = list(sys.argv[1:] if argv is None else argv)
    index = 0
    while index < len(args):
        arg = args[index]
        if arg in {"--config-file", "--env-file"}:
            index += 2
            continue
        if arg.startswith("-"):
            index += 1
            continue
        break
    tail = args[index:]
    if not tail:
        return [], args
    command = tail[0]
    if command in {"trade", "daemon"} and len(tail) > 1 and not tail[1].startswith("-"):
        return [command, tail[1]], tail
    return [command], tail


def print_removed_command_hint(args: list[str]) -> bool:
    if not args:
        return False
    if args[0] == "binance":
        typer.echo("binance subcommands were removed from the public CLI.", err=True)
        typer.echo("Use top-level reads instead: tbot summary | snapshot | positions | orders", err=True)
        return True
    if args[0] == "gate-sim":
        typer.echo("gate-sim commands were removed from the public CLI.", err=True)
        typer.echo("Use OKX demo instead: tbot sim btc | tbot sim btc test", err=True)
        return True
    return False


def command_tail(args: list[str]) -> list[str]:
    index = 0
    while index < len(args):
        arg = args[index]
        if arg in {"--config-file", "--env-file"}:
            index += 2
            continue
        if arg.startswith("-"):
            index += 1
            continue
        break
    return args[index:]
