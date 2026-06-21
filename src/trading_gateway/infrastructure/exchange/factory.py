from __future__ import annotations

from typing import Any

from trading_gateway.app.config import get_gateway_config, read_exchange_creds, require_exchange_creds
from trading_gateway.domain.models import ExchangeCreds, normalize_exchange, normalize_market
from trading_gateway.infrastructure.exchange.account_modes import exchange_account_profile, require_private_profile


def _import_ccxt():
    try:
        import ccxt
    except ModuleNotFoundError as exc:
        raise SystemExit("缺少依赖 ccxt，请先安装项目依赖") from exc
    return ccxt


def build_ccxt_client(
    exchange: str,
    market: str,
    *,
    require_private: bool = False,
    account_mode: str | None = None,
    timeout_ms: int | None = None,
    enable_rate_limit: bool | None = None,
) -> Any:
    name = normalize_exchange(exchange)
    market_type = normalize_market(market)
    profile = require_private_profile(name, account_mode) if require_private else exchange_account_profile(name, account_mode or "live")
    creds = require_exchange_creds(name, profile.env_spec, profile.fallback_env_spec) if require_private else read_exchange_creds(name, profile.env_spec, profile.fallback_env_spec)
    ccxt = _import_ccxt()
    cls = getattr(ccxt, name)
    config_values = get_gateway_config()
    config: dict[str, Any] = {
        "enableRateLimit": config_values.enable_rate_limit if enable_rate_limit is None else enable_rate_limit,
        "timeout": config_values.ccxt_timeout_ms if timeout_ms is None else timeout_ms,
        "options": {"defaultType": market_type},
    }
    if creds.api_key and creds.api_secret:
        config.update({"apiKey": creds.api_key, "secret": creds.api_secret})
        if creds.password:
            config["password"] = creds.password
    _apply_exchange_transport_config(name, config)
    return _build_client(cls, config, profile)


def build_ccxt_client_from_creds(
    exchange: str,
    market: str,
    creds: ExchangeCreds,
    *,
    account_mode: str | None = None,
    timeout_ms: int | None = None,
    enable_rate_limit: bool | None = None,
) -> Any:
    name = normalize_exchange(exchange)
    market_type = normalize_market(market)
    ccxt = _import_ccxt()
    cls = getattr(ccxt, name)
    config_values = get_gateway_config()
    config: dict[str, Any] = {
        "enableRateLimit": config_values.enable_rate_limit if enable_rate_limit is None else enable_rate_limit,
        "timeout": config_values.ccxt_timeout_ms if timeout_ms is None else timeout_ms,
        "options": {"defaultType": market_type},
        "apiKey": creds.api_key,
        "secret": creds.api_secret,
    }
    if creds.password:
        config["password"] = creds.password
    profile = exchange_account_profile(name, account_mode or "live")
    _apply_exchange_transport_config(name, config)
    return _build_client(cls, config, profile)


def close_client(client: Any) -> None:
    close = getattr(client, "close", None)
    if callable(close):
        close()


def _apply_exchange_transport_config(exchange: str, config: dict[str, Any]) -> None:
    if exchange == "okx":
        config["requests_trust_env"] = True


def _build_client(cls: Any, config: dict[str, Any], profile: Any) -> Any:
    client = cls(config)
    if profile.sandbox:
        set_sandbox = getattr(client, "set_sandbox_mode", None)
        if not callable(set_sandbox):
            raise ValueError(f"{profile.exchange} {profile.account_mode} requires ccxt sandbox support")
        # OKX demo trading is isolated by ccxt via the x-simulated-trading header.
        set_sandbox(True)
    setattr(client, "trading_gateway_account_mode", profile.account_mode)
    setattr(client, "trading_gateway_sandbox", profile.sandbox)
    return client
