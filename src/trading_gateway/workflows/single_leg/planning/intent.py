from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class SingleLegIntent:
    exchange: str
    market: str
    action: str
    symbol: str
    quote_usdt: float | None = None
    bbo: bool = False
    limit_price: float | None = None
    take_profit: float | None = None
    stop_loss: float | None = None
    margin_mode: str | None = None
    leverage: int | None = None
    account_mode: str | None = None

    def __post_init__(self) -> None:
        market = clean_value(self.market)
        action = clean_value(self.action)
        exchange = clean_value(self.exchange)
        margin_mode = clean_optional_value(self.margin_mode)
        account_mode = clean_optional_value(self.account_mode)
        object.__setattr__(self, "exchange", exchange)
        object.__setattr__(self, "market", market)
        object.__setattr__(self, "action", action)
        object.__setattr__(self, "margin_mode", margin_mode)
        object.__setattr__(self, "account_mode", account_mode)
        if market not in {"spot", "perp"}:
            raise ValueError("market must be spot or perp")
        actions = {"buy", "sell"} if market == "spot" else {"open-long", "open-short", "close-long", "close-short"}
        if action == "close-shot":
            raise ValueError("unsupported perp action: close-shot; did you mean close-short?")
        if action not in actions:
            raise ValueError(f"unsupported {market} action: {action}")
        if market == "spot" and action == "buy" and self.quote_usdt is None:
            raise ValueError("quote_usdt is required for spot buy")
        if self.quote_usdt is not None and float(self.quote_usdt) <= 0:
            raise ValueError("quote_usdt must be positive")
        if self.limit_price is not None and float(self.limit_price) <= 0:
            raise ValueError("limit_price must be positive")
        if self.take_profit is not None and float(self.take_profit) <= 0:
            raise ValueError("take_profit must be positive")
        if self.stop_loss is not None and float(self.stop_loss) <= 0:
            raise ValueError("stop_loss must be positive")
        if margin_mode is not None and margin_mode not in {"cross", "isolated"}:
            raise ValueError("margin_mode must be cross or isolated")
        if self.leverage is not None and int(self.leverage) < 1:
            raise ValueError("leverage must be >= 1")
        if account_mode is not None and account_mode not in {"live", "sim", "sandbox", "testnet", "paper"}:
            raise ValueError("account_mode must be live or sim")
        if (self.take_profit is not None or self.stop_loss is not None) and (market != "perp" or exchange != "okx"):
            raise ValueError("take_profit/stop_loss are currently supported only for okx perp orders")
        if margin_mode is not None and (market != "perp" or exchange != "okx"):
            raise ValueError("margin_mode is currently supported only for okx perp orders")


def clean_value(value: str) -> str:
    return str(value or "").strip().lower()


def clean_optional_value(value: str | None) -> str | None:
    text = str(value or "").strip().lower()
    return text or None
