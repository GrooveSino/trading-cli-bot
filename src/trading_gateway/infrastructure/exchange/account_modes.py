from __future__ import annotations

from dataclasses import dataclass
from typing import Literal

from trading_gateway.app.config.schema import ExchangeEnvSpec
from trading_gateway.domain.models import normalize_exchange

AccountMode = Literal["live", "sim"]


@dataclass(frozen=True)
class ExchangeAccountProfile:
    exchange: str
    account_mode: AccountMode
    env_spec: ExchangeEnvSpec
    sandbox: bool
    private_enabled: bool
    reason: str = ""
    fallback_env_spec: ExchangeEnvSpec | None = None

    @property
    def label(self) -> str:
        return f"{self.exchange}:{self.account_mode}"


def normalize_account_mode(value: str | None, *, exchange: str) -> AccountMode:
    text = str(value or "").strip().lower()
    if not text:
        return default_account_mode(exchange)
    aliases = {"paper": "sim", "sandbox": "sim", "testnet": "sim"}
    text = aliases.get(text, text)
    if text not in {"live", "sim"}:
        raise ValueError("account_mode must be live or sim")
    return text  # type: ignore[return-value]


def default_account_mode(exchange: str) -> AccountMode:
    normalize_exchange(exchange)
    return "live"


def account_mode_choices() -> str:
    return "live/sim"


def exchange_account_profile(exchange: str, account_mode: str | None = None) -> ExchangeAccountProfile:
    name = normalize_exchange(exchange)
    mode = normalize_account_mode(account_mode, exchange=name)
    if name == "okx" and mode == "live":
        return ExchangeAccountProfile(name, mode, ExchangeEnvSpec("OKX_API_KEY", "OKX_API_SECRET", "OKX_PASSWORD"), sandbox=False, private_enabled=True)
    if name == "okx" and mode == "sim":
        return ExchangeAccountProfile(
            name,
            mode,
            ExchangeEnvSpec("OKX_SIM_API_KEY", "OKX_SIM_API_SECRET", "OKX_SIM_PASSWORD"),
            sandbox=True,
            private_enabled=True,
            fallback_env_spec=ExchangeEnvSpec("OKX_API_KEY", "OKX_API_SECRET", "OKX_PASSWORD"),
        )
    if name == "gate":
        return ExchangeAccountProfile(name, mode, ExchangeEnvSpec("GATE_API_KEY", "GATE_API_SECRET", "GATE_PASSWORD"), sandbox=False, private_enabled=False, reason="Gate private account features are disabled; use OKX sim.")
    if name == "binance":
        return ExchangeAccountProfile(name, mode, ExchangeEnvSpec("BINANCE_API_KEY", "BINANCE_API_SECRET"), sandbox=False, private_enabled=False, reason="Binance private account features are disabled; use OKX live or OKX sim.")
    return ExchangeAccountProfile(name, mode, ExchangeEnvSpec(f"{name.upper()}_API_KEY", f"{name.upper()}_API_SECRET", f"{name.upper()}_PASSWORD"), sandbox=False, private_enabled=False, reason=f"{name} private account features are disabled; use OKX live or OKX sim.")


def require_private_profile(exchange: str, account_mode: str | None = None) -> ExchangeAccountProfile:
    profile = exchange_account_profile(exchange, account_mode)
    if not profile.private_enabled:
        raise ValueError(profile.reason)
    return profile
