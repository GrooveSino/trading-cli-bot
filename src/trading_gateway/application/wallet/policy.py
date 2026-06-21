from __future__ import annotations

from trading_gateway.domain.models import normalize_exchange
from trading_gateway.infrastructure.exchange.account_modes import account_mode_choices, default_account_mode, require_private_profile

PRIVATE_ACCOUNT_EXCHANGES: tuple[str, ...] = ("okx",)
PRIVATE_ACCOUNT_DISABLED_MESSAGE = "Binance private account features are disabled; use OKX live or OKX sim."


class PrivateAccountExchangeDisabled(ValueError):
    pass


def default_private_account_exchanges() -> list[str]:
    return list(PRIVATE_ACCOUNT_EXCHANGES)


def private_account_choices() -> str:
    return "okx(live/sim)"


def private_account_mode_choices() -> str:
    return account_mode_choices()


def validate_private_account_exchange(exchange: str, account_mode: str | None = None) -> str:
    name = normalize_exchange(exchange)
    mode = account_mode or default_account_mode(name)
    try:
        require_private_profile(name, mode)
        return name
    except ValueError as exc:
        if name != "binance":
            raise PrivateAccountExchangeDisabled(str(exc)) from exc
    if name == "binance":
        raise PrivateAccountExchangeDisabled(PRIVATE_ACCOUNT_DISABLED_MESSAGE)
    raise PrivateAccountExchangeDisabled(f"{name} private account features are disabled; use OKX live or OKX sim.")


def validate_private_account_exchanges(exchanges: list[str] | None, account_mode: str | None = None) -> list[str]:
    if not exchanges:
        return default_private_account_exchanges()
    return [validate_private_account_exchange(exchange, account_mode) for exchange in exchanges]
