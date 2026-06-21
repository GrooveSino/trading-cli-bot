from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor, as_completed
import os
import threading
import time
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any

from trading_gateway.infrastructure.exchange.factory import build_ccxt_client, close_client
from trading_gateway.infrastructure.exchange.account_modes import exchange_account_profile
from trading_gateway.app.config import read_exchange_creds
from trading_gateway.domain.models import display_market
from trading_gateway.app.config import get_gateway_config
from trading_gateway.interfaces.daemon.account_state import DaemonAccountStateStore
from trading_gateway.support.redaction import redact_text

ROUTES: tuple[tuple[str, str, str], ...] = (
    ("okx", "spot", "live"),
    ("okx", "swap", "live"),
    ("okx", "spot", "sim"),
    ("okx", "swap", "sim"),
)


@dataclass
class RouteState:
    exchange: str
    market: str
    account_mode: str
    client: Any | None = None
    status: str = "starting"
    refreshing: bool = False
    last_warmup_at: float | None = None
    last_private_refresh_at: float | None = None
    last_orderbook_refresh_at: float | None = None
    cached_balance: dict[str, Any] | None = None
    cached_positions: list[dict[str, Any]] | None = None
    last_error: str | None = None

    @property
    def key(self) -> str:
        return f"{self.exchange}:{self.market}:{self.account_mode}"


class DaemonRuntime:
    def __init__(self) -> None:
        self._config = get_gateway_config()
        states = [RouteState(exchange, market, mode) for exchange, market, mode in ROUTES if _route_enabled(exchange, mode)]
        self._routes = {state.key: state for state in states}
        self._lock = threading.RLock()
        self._stop_event = threading.Event()
        self._thread: threading.Thread | None = None
        self._started_at = time.time()
        self._active_job: dict[str, Any] | None = None
        self._account_state = DaemonAccountStateStore()

    def start(self) -> None:
        if self._thread and self._thread.is_alive():
            return
        self._thread = threading.Thread(target=self._refresh_loop, name="tg-daemon-refresh", daemon=True)
        self._thread.start()

    def stop(self) -> None:
        self._stop_event.set()
        if self._thread and self._thread.is_alive():
            self._thread.join(timeout=2)
        with self._lock:
            for route in self._routes.values():
                if route.client is not None:
                    close_client(route.client)
                    route.client = None

    def route_client(self, exchange: str, market: str, account_mode: str | None = None) -> Any:
        key = _route_key(exchange, market, account_mode)
        with self._lock:
            route = self._routes[key]
        self._refresh_route(route, force=True)
        with self._lock:
            if route.client is None or route.status != "ready":
                raise ValueError(f"daemon route not ready: {exchange} {display_market(market)}")
            return route.client

    def ensure_routes_ready(self, routes: list[tuple[str, str] | tuple[str, str, str | None]]) -> None:
        payload = self.status_payload()
        status_by_key = {_route_key(str(row.get("exchange") or ""), str(row.get("market") or ""), row.get("account_mode")): row for row in payload["routes"]}
        for route in routes:
            exchange, market, mode = _route_parts(route)
            key = _route_key(exchange, market, mode)
            row = status_by_key.get(key)
            if not row:
                raise ValueError(f"daemon route unavailable: {exchange} {display_market(market)}")
            if row["status"] != "ready":
                reason = row.get("last_error") or row["status"]
                raise ValueError(f"daemon route not ready: {exchange} {display_market(market)}: {reason}")

    def status_payload(self) -> dict[str, Any]:
        with self._lock:
            bootstrapping = any(route.last_private_refresh_at is None for route in self._routes.values())
            rows = [self._route_payload(route, bootstrapping=bootstrapping) for route in self._routes.values()]
            daemon_status = "ready" if all(row["status"] == "ready" for row in rows) else "degraded"
            if all(row["status"] in {"starting", "warming"} for row in rows):
                daemon_status = "warming"
            elif bootstrapping and all(row["status"] in {"starting", "warming", "ready"} for row in rows):
                daemon_status = "warming"
            return {
                "mode": "trading_gateway_daemon",
                "status": daemon_status,
                "pid": os.getpid(),
                "host": self._config.daemon.host,
                "port": self._config.daemon.port,
                "started_at": _iso(self._started_at),
                "uptime_sec": max(0.0, time.time() - self._started_at),
                "config_file": str(self._config.path),
                "runtime_dir": str(self._config.daemon.runtime_dir),
                "active_live_job": self._active_job,
                "routes": rows,
            }

    def set_active_job(self, value: dict[str, Any] | None) -> None:
        with self._lock:
            self._active_job = value

    def route_account_state(self, exchange: str, market: str, account_mode: str | None = None) -> dict[str, Any]:
        key = _route_key(exchange, market, account_mode)
        with self._lock:
            route = self._routes[key]
            if route.cached_balance is None:
                raise ValueError("cache_missing")
            if not self._account_state.cache_fresh(route.last_private_refresh_at):
                raise ValueError("cache_stale")
            return {
                "balance": dict(route.cached_balance),
                "positions": list(route.cached_positions or []),
                "refreshed_at": route.last_private_refresh_at,
            }

    def refresh_route_account_state(self, exchange: str, market: str, account_mode: str | None = None) -> None:
        key = _route_key(exchange, market, account_mode)
        with self._lock:
            route = self._routes[key]
        self._refresh_route(route, force=True)

    def _refresh_loop(self) -> None:
        while not self._stop_event.is_set():
            started_at = time.time()
            with self._lock:
                routes = list(self._routes.values())
            self._refresh_routes_once(routes)
            self._stop_event.wait(self._refresh_wait_sec(started_at=started_at))

    def _refresh_routes_once(self, routes: list[RouteState]) -> None:
        workers = max(1, len(routes))
        with ThreadPoolExecutor(max_workers=workers, thread_name_prefix="tg-daemon-route") as pool:
            futures = [pool.submit(self._refresh_route, route) for route in routes]
            for future in as_completed(futures):
                future.result()

    def _refresh_wait_sec(self, *, started_at: float) -> float:
        elapsed = max(0.0, time.time() - started_at)
        return max(0.0, self._config.daemon.private_refresh_interval_sec - elapsed)

    def _refresh_route(self, route: RouteState, *, force: bool = False) -> None:
        ttl = self._config.daemon.readiness_ttl_sec
        now = time.time()
        with self._lock:
            if not force and route.last_private_refresh_at and now - route.last_private_refresh_at < min(ttl / 2, self._config.daemon.private_refresh_interval_sec):
                return
            client = route.client
            exchange = route.exchange
            market = route.market
            route.refreshing = True
            if client is None:
                route.status = "warming"
        try:
            if client is None:
                client = build_ccxt_client(exchange, market, require_private=True, account_mode=route.account_mode)
                with self._lock:
                    if route.client is None:
                        route.client = client
                        route.last_warmup_at = now
                    else:
                        close_client(client)
                        client = route.client
            balance, positions = self._fetch_private_state(client, market)
            with self._lock:
                route.cached_balance = balance
                route.cached_positions = positions
                route.last_private_refresh_at = now
                route.status = "ready"
                route.last_error = None
                route.refreshing = False
        except Exception as exc:  # noqa: BLE001
            with self._lock:
                route.last_error = redact_text(exc)
                route.status = "error" if route.last_private_refresh_at is None else "stale"
                route.refreshing = False

    def _fetch_private_state(self, client: Any, market: str) -> tuple[dict[str, Any], list[dict[str, Any]]]:
        balance = client.fetch_balance() if hasattr(client, "fetch_balance") else {}
        positions = client.fetch_positions() if market == "swap" and hasattr(client, "fetch_positions") else []
        return balance or {}, positions or []

    def _route_payload(self, route: RouteState, *, bootstrapping: bool) -> dict[str, Any]:
        now = time.time()
        ttl = self._config.daemon.readiness_ttl_sec
        effective_status = route.status
        if (
            not bootstrapping
            and not route.refreshing
            and route.status == "ready"
            and route.last_private_refresh_at is not None
            and now - route.last_private_refresh_at > ttl
        ):
            effective_status = "stale"
        return {
            "route": f"{route.exchange}:{display_market(route.market)}:{route.account_mode}",
            "exchange": route.exchange,
            "market": display_market(route.market),
            "account_mode": route.account_mode,
            "status": effective_status,
            "last_warmup_at": _iso(route.last_warmup_at),
            "last_private_refresh_at": _iso(route.last_private_refresh_at),
            "last_private_refresh_age_sec": None if route.last_private_refresh_at is None else max(0.0, now - route.last_private_refresh_at),
            "last_orderbook_refresh_at": _iso(route.last_orderbook_refresh_at),
            "last_orderbook_refresh_age_sec": None if route.last_orderbook_refresh_at is None else max(0.0, now - route.last_orderbook_refresh_at),
            "last_error": route.last_error,
        }


_RUNTIME: DaemonRuntime | None = None


def get_daemon_runtime() -> DaemonRuntime:
    global _RUNTIME
    if _RUNTIME is None:
        _RUNTIME = DaemonRuntime()
        _RUNTIME.start()
    return _RUNTIME


def stop_daemon_runtime() -> None:
    global _RUNTIME
    if _RUNTIME is not None:
        _RUNTIME.stop()
    _RUNTIME = None


def reset_daemon_runtime_for_tests() -> None:
    stop_daemon_runtime()


def _route_key(exchange: str, market: str, account_mode: object | None = None) -> str:
    normalized_market = "swap" if market == "perp" else market
    normalized_exchange = exchange.lower()
    mode = str(account_mode or "live").lower()
    return f"{normalized_exchange}:{normalized_market.lower()}:{mode}"


def _route_enabled(exchange: str, account_mode: str) -> bool:
    profile = exchange_account_profile(exchange, account_mode)
    if not profile.private_enabled:
        return False
    creds = read_exchange_creds(profile.exchange, profile.env_spec, profile.fallback_env_spec)
    return bool(creds.api_key and creds.api_secret and (creds.password or not profile.env_spec.password_env))


def _route_parts(route: tuple[str, str] | tuple[str, str, str | None]) -> tuple[str, str, str | None]:
    if len(route) == 2:
        return route[0], route[1], None
    return route[0], route[1], route[2]


def _iso(value: float | None) -> str | None:
    if value is None:
        return None
    return datetime.fromtimestamp(value, UTC).isoformat()
