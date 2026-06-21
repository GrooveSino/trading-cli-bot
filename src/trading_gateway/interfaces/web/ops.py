from __future__ import annotations

from typing import Any, Callable

from trading_gateway.workflows.overview.snapshot.runtime.service import build_account_snapshot, snapshot_to_summary_payload
from trading_gateway.workflows.pair_trade.execution import run_close_execution as run_pair_close_execution, run_live_execution as run_pair_execution
from trading_gateway.workflows.pair_trade.planning import build_pair_close_plan, build_pair_plan
from trading_gateway.workflows.single_leg.execution import run_live_execution as run_single_leg_execution
from trading_gateway.workflows.single_leg.planning import SingleLegIntent, build_single_leg_trade_plan
from trading_gateway.application.wallet.wallet import run_transfer, transfer_confirm_phrase
from trading_gateway.workflows.transfer.planning import transfer_planning_metadata
from trading_gateway.interfaces.daemon.runtime import get_daemon_runtime
from trading_gateway.interfaces.web.planning import pair_account_state, refresh_route_after_live, single_leg_account_state
from trading_gateway.infrastructure.exchange.factory import build_ccxt_client, close_client
from trading_gateway.app.config import get_gateway_config
from trading_gateway.support.redaction import redact_mapping
from trading_gateway.interfaces.web.runtime import transfer_intent
from trading_gateway.workflows.pair_trade.recovery.status import build_pair_status
from trading_gateway.application.wallet.policy import validate_private_account_exchange

Progress = Callable[[dict[str, Any]], None]


def snapshot() -> dict[str, Any]:
    return build_account_snapshot()


def summary() -> dict[str, Any]:
    return snapshot_to_summary_payload(snapshot())


def orders(exchange: str, market: str, symbol: str, account_mode: str | None = None) -> dict[str, Any]:
    exchange = validate_private_account_exchange(exchange, account_mode)
    client = build_ccxt_client(exchange, market, require_private=True, account_mode=account_mode)
    try:
        return redact_mapping({"open_orders": client.fetch_open_orders(symbol) or []})
    finally:
        close_client(client)


def transfer_plan(body: dict[str, Any]) -> dict[str, Any]:
    intent = transfer_intent(body)
    return {
        "status": "dry_run",
        "transfer": intent.__dict__,
        "confirm": transfer_confirm_phrase(intent),
        "planning_data_sources": transfer_planning_metadata(),
    }


def transfer_run(body: dict[str, Any]) -> dict[str, Any]:
    intent = transfer_intent(body)
    if bool(body.get("live")):
        runtime = get_daemon_runtime()
        runtime.ensure_routes_ready([(intent.exchange, "spot")])
        client = runtime.route_client(intent.exchange, "spot")
        result = run_transfer(client, intent, live=True, confirm=str(body.get("confirm") or ""))
        refresh_route_after_live(runtime, [(intent.exchange, "spot")], result)
        return result
    client = build_ccxt_client(intent.exchange, "spot", require_private=False)
    try:
        return run_transfer(client, intent, live=False, confirm=str(body.get("confirm") or ""))
    finally:
        close_client(client)


def pair_plan(body: dict[str, Any]) -> dict[str, Any]:
    spot_exchange = str(body.get("spot_exchange") or "binance")
    perp_exchange = str(body.get("perp_exchange") or "binance")
    spot, perp = _pair_clients(body, private=body.get("last_price") is None)
    try:
        account_state = pair_account_state(spot_exchange, perp_exchange, spot, perp)
        plan = build_pair_plan(
            spot,
            perp,
            spot_exchange=spot_exchange,
            perp_exchange=perp_exchange,
            symbol=str(body.get("symbol") or ""),
            quote_usdt=float(body.get("quote_usdt") or 0),
            universe_path=get_gateway_config().route_universe,
            account_state=account_state["state"],
            planning_usage=account_state["usage"],
        )
        return {"mode": "pair_trading_plan", "plan": plan}
    finally:
        close_client(spot)
        close_client(perp)


def pair_close_plan(body: dict[str, Any]) -> dict[str, Any]:
    spot_exchange = str(body.get("spot_exchange") or "binance")
    perp_exchange = str(body.get("perp_exchange") or "binance")
    spot, perp = _pair_clients(body, private=body.get("last_price") is None)
    try:
        account_state = pair_account_state(spot_exchange, perp_exchange, spot, perp)
        plan = build_pair_close_plan(
            spot,
            perp,
            spot_exchange=spot_exchange,
            perp_exchange=perp_exchange,
            symbol=str(body.get("symbol") or ""),
            universe_path=get_gateway_config().route_universe,
            account_state=account_state["state"],
            planning_usage=account_state["usage"],
        )
        return {"mode": "pair_close_plan", "plan": plan}
    finally:
        close_client(spot)
        close_client(perp)


def pair_run(body: dict[str, Any], progress: Progress | None = None) -> dict[str, Any]:
    if body.get("last_price") not in (None, ""):
        raise ValueError("pair run does not accept last_price; use pair-plan for static preview")
    runtime = get_daemon_runtime()
    spot_exchange = str(body.get("spot_exchange") or "binance")
    perp_exchange = str(body.get("perp_exchange") or "binance")
    runtime.ensure_routes_ready([(spot_exchange, "spot"), (perp_exchange, "perp")])
    spot = runtime.route_client(spot_exchange, "spot")
    perp = runtime.route_client(perp_exchange, "perp")
    try:
        account_state = pair_account_state(spot_exchange, perp_exchange, spot, perp)
        plan = build_pair_plan(
            spot,
            perp,
            spot_exchange=spot_exchange,
            perp_exchange=perp_exchange,
            symbol=str(body.get("symbol") or ""),
            quote_usdt=float(body.get("quote_usdt") or 0),
            universe_path=get_gateway_config().route_universe,
            account_state=account_state["state"],
            planning_usage=account_state["usage"],
        )
        result = run_pair_execution(spot, perp, plan, confirm=str(body.get("confirm") or ""), timeout_sec=_optional_float(body.get("timeout_sec")), normal_max_requotes=_optional_int(body.get("max_requotes")), progress=progress)
        refresh_route_after_live(runtime, [(spot_exchange, "spot"), (perp_exchange, "perp")], result)
        return result
    finally:
        pass


def pair_close_run(body: dict[str, Any], progress: Progress | None = None) -> dict[str, Any]:
    if body.get("last_price") not in (None, ""):
        raise ValueError("pair close run does not accept last_price; use pair-close-plan for static preview")
    runtime = get_daemon_runtime()
    spot_exchange = str(body.get("spot_exchange") or "binance")
    perp_exchange = str(body.get("perp_exchange") or "binance")
    runtime.ensure_routes_ready([(spot_exchange, "spot"), (perp_exchange, "perp")])
    spot = runtime.route_client(spot_exchange, "spot")
    perp = runtime.route_client(perp_exchange, "perp")
    try:
        account_state = pair_account_state(spot_exchange, perp_exchange, spot, perp)
        plan = build_pair_close_plan(
            spot,
            perp,
            spot_exchange=spot_exchange,
            perp_exchange=perp_exchange,
            symbol=str(body.get("symbol") or ""),
            universe_path=get_gateway_config().route_universe,
            account_state=account_state["state"],
            planning_usage=account_state["usage"],
        )
        result = run_pair_close_execution(spot, perp, plan, confirm=str(body.get("confirm") or ""), timeout_sec=_optional_float(body.get("timeout_sec")), normal_max_requotes=_optional_int(body.get("max_requotes")), progress=progress)
        refresh_route_after_live(runtime, [(spot_exchange, "spot"), (perp_exchange, "perp")], result)
        return result
    finally:
        pass


def pair_status(pair_id: str) -> dict[str, Any]:
    from trading_gateway.workflows.pair_trade.journaling.journal import load_pair_journal, validate_pair_journal
    journal = load_pair_journal(pair_id)
    validate_pair_journal(journal)
    plan = journal["plan"]
    spot = build_ccxt_client(plan["spot_exchange"], "spot", require_private=True)
    perp = build_ccxt_client(plan["perp_exchange"], "swap", require_private=True)
    try:
        return build_pair_status(spot, perp, pair_id)
    finally:
        close_client(spot)
        close_client(perp)


def lab_plan(body: dict[str, Any]) -> dict[str, Any]:
    intent = _lab_intent(body)
    client = build_ccxt_client(intent.exchange, "swap" if intent.market == "perp" else "spot", require_private=body.get("last_price") in (None, ""), account_mode=intent.account_mode)
    try:
        account_state = single_leg_account_state(intent, client)
        return {
            "mode": "single_leg_plan",
            "plan": build_single_leg_trade_plan(
                client,
                intent,
                universe_path=get_gateway_config().route_universe,
                account_state=account_state["state"],
                planning_usage=account_state["usage"],
            ),
        }
    finally:
        close_client(client)


def lab_run(body: dict[str, Any], progress: Progress | None = None) -> dict[str, Any]:
    if body.get("last_price") not in (None, ""):
        raise ValueError("run does not accept last_price; use plan for static preview")
    intent = _lab_intent(body)
    runtime = get_daemon_runtime()
    daemon_market = intent.market
    runtime.ensure_routes_ready([(intent.exchange, daemon_market, intent.account_mode)])
    client = runtime.route_client(intent.exchange, daemon_market, intent.account_mode)
    try:
        account_state = single_leg_account_state(intent, client)
        plan = build_single_leg_trade_plan(
            client,
            intent,
            universe_path=get_gateway_config().route_universe,
            account_state=account_state["state"],
            planning_usage=account_state["usage"],
        )
        result = run_single_leg_execution(client, plan, confirm=str(body.get("confirm") or ""), timeout_sec=_optional_float(body.get("timeout_sec")), max_requotes=_optional_int(body.get("max_requotes")), progress=progress)
        refresh_route_after_live(runtime, [(intent.exchange, daemon_market, intent.account_mode)], result)
        return result
    finally:
        pass


def _pair_clients(body: dict[str, Any], *, private: bool) -> tuple[Any, Any]:
    spot_exchange = str(body.get("spot_exchange") or "binance")
    perp_exchange = str(body.get("perp_exchange") or "binance")
    return build_ccxt_client(spot_exchange, "spot", require_private=private), build_ccxt_client(perp_exchange, "swap", require_private=private)


def _lab_intent(body: dict[str, Any]) -> SingleLegIntent:
    return SingleLegIntent(
        exchange=str(body.get("exchange") or "binance"),
        market=str(body.get("market") or ""),
        action=str(body.get("action") or ""),
        symbol=str(body.get("symbol") or ""),
        quote_usdt=body.get("quote_usdt"),
        bbo=bool(body.get("bbo", True)),
        limit_price=_optional_float(body.get("limit_price")),
        take_profit=_optional_float(body.get("take_profit")),
        stop_loss=_optional_float(body.get("stop_loss")),
        margin_mode=body.get("margin_mode"),
        leverage=_optional_int(body.get("leverage")),
        account_mode=body.get("account_mode"),
    )


def _optional_float(value: Any) -> float | None:
    return None if value in (None, "") else float(value)


def _optional_int(value: Any) -> int | None:
    return None if value in (None, "") else int(value)
