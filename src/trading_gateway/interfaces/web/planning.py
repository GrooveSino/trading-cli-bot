from __future__ import annotations

from typing import Any

from trading_gateway.app.config import get_gateway_config
from trading_gateway.interfaces.daemon.runtime import get_daemon_runtime
from trading_gateway.workflows.overview.planning_provider import resolve_planning_account_state


def pair_account_state(spot_exchange: str, perp_exchange: str, spot: Any, perp: Any) -> dict[str, Any]:
    runtime = _safe_runtime()
    if runtime is not None:
        return _pair_account_state_from_runtime(runtime, spot_exchange, perp_exchange)
    return {
        "state": {
            "spot_balance": _fetch_client_account_state(spot, "spot")[0],
            "perp_balance": _fetch_client_account_state(perp, "perp")[0],
            "perp_positions": _fetch_client_account_state(perp, "perp")[1],
        },
        "usage": _exchange_fetch_usage("daemon_unavailable"),
    }


def single_leg_account_state(intent: Any, client: Any) -> dict[str, Any]:
    runtime = _safe_runtime()
    if runtime is not None and not uses_static_price_client(client):
        return _single_leg_account_state_from_runtime(runtime, intent)
    balance, positions = _fetch_client_account_state(client, intent.market)
    return {
        "state": {"balance": balance, "positions": positions},
        "usage": _exchange_fetch_usage("daemon_unavailable"),
    }


def refresh_route_after_live(runtime: Any, routes: list[tuple[str, str] | tuple[str, str, str | None]], result: dict[str, Any]) -> None:
    if not get_gateway_config().planning.refresh_routes_after_live_completion:
        return
    status = str(result.get("final_status") or result.get("status") or "")
    if status not in {"live", "target_reached", "asset_target_reached", "pair_target_reached"}:
        return
    for route in routes:
        exchange, market, mode = _route_parts(route)
        runtime.refresh_route_account_state(exchange, market, mode)


def uses_static_price_client(client: Any) -> bool:
    return client.__class__.__name__ == "StaticLabClient"


def _pair_account_state_from_runtime(runtime: Any, spot_exchange: str, perp_exchange: str) -> dict[str, Any]:
    spot_state = resolve_planning_account_state(
        cached_reader=lambda: runtime.route_account_state(spot_exchange, "spot"),
        fetcher=lambda: _fetch_client_account_state(runtime.route_client(spot_exchange, "spot"), "spot"),
    )
    perp_state = resolve_planning_account_state(
        cached_reader=lambda: runtime.route_account_state(perp_exchange, "perp"),
        fetcher=lambda: _fetch_client_account_state(runtime.route_client(perp_exchange, "perp"), "perp"),
    )
    return {
        "state": {
            "spot_balance": spot_state.balance,
            "perp_balance": perp_state.balance,
            "perp_positions": perp_state.positions,
        },
        "usage": _merge_usage(spot_state.usage, perp_state.usage),
    }


def _single_leg_account_state_from_runtime(runtime: Any, intent: Any) -> dict[str, Any]:
    market = "perp" if intent.market == "perp" else "spot"
    state = resolve_planning_account_state(
        cached_reader=lambda: runtime.route_account_state(intent.exchange, market, getattr(intent, "account_mode", None)),
        fetcher=lambda: _fetch_client_account_state(runtime.route_client(intent.exchange, market, getattr(intent, "account_mode", None)), market),
    )
    return {"state": {"balance": state.balance, "positions": state.positions}, "usage": state.usage.to_mapping()}


def _fetch_client_account_state(client: Any, market: str) -> tuple[dict[str, Any], list[dict[str, Any]]]:
    normalized = "perp" if market in {"perp", "swap"} else "spot"
    balance = client.fetch_balance() if hasattr(client, "fetch_balance") else {}
    positions = client.fetch_positions() if normalized == "perp" and hasattr(client, "fetch_positions") else []
    return balance or {}, positions or []


def _merge_usage(spot_usage: Any, perp_usage: Any) -> dict[str, Any]:
    if spot_usage.account_state_source == "daemon_cache" and perp_usage.account_state_source == "daemon_cache":
        age_values = [value for value in (spot_usage.account_state_age_sec, perp_usage.account_state_age_sec) if value is not None]
        return {
            "account_state_source": "daemon_cache",
            "account_state_age_sec": max(age_values) if age_values else None,
            "market_data_source": "live",
            "fallback_reason": spot_usage.fallback_reason or perp_usage.fallback_reason,
        }
    return _exchange_fetch_usage(spot_usage.fallback_reason or perp_usage.fallback_reason or "cache_mixed")


def _exchange_fetch_usage(reason: str | None) -> dict[str, Any]:
    return {
        "account_state_source": "exchange_fetch",
        "account_state_age_sec": None,
        "market_data_source": "live",
        "fallback_reason": reason,
    }


def _safe_runtime() -> Any | None:
    try:
        return get_daemon_runtime()
    except Exception:  # noqa: BLE001
        return None


def _route_parts(route: tuple[str, str] | tuple[str, str, str | None]) -> tuple[str, str, str | None]:
    if len(route) == 2:
        return route[0], route[1], None
    return route[0], route[1], route[2]
