from __future__ import annotations

from dataclasses import asdict, dataclass, field
from decimal import Decimal
from typing import Any, Literal

ExchangeId = Literal["binance", "okx", "gate", "mexc"]
MarketType = Literal["spot", "swap"]
OrderSide = Literal["buy", "sell"]

SUPPORTED_EXCHANGES: tuple[str, ...] = ("binance", "okx", "gate", "mexc")
MARKET_TYPES: tuple[str, ...] = ("spot", "swap")
PUBLIC_MARKET_TYPES: tuple[str, ...] = ("spot", "perp")


def _clean(value: str) -> str:
    return str(value or "").strip()


def normalize_exchange(value: str) -> str:
    exchange = _clean(value).lower()
    if exchange not in SUPPORTED_EXCHANGES:
        raise ValueError(f"unsupported exchange: {value}")
    return exchange


def normalize_market(value: str) -> str:
    market = _clean(value).lower()
    if market == "perp":
        return "swap"
    if market not in MARKET_TYPES:
        raise ValueError(f"unsupported market: {value}")
    return market


def display_market(value: str) -> str:
    market = _clean(value).lower()
    if market == "swap":
        return "perp"
    return market


def public_market_choices(*, include_both: bool = False) -> str:
    values = list(PUBLIC_MARKET_TYPES)
    if include_both:
        values.append("both")
    return "/".join(values)


def normalize_transfer_account(value: str) -> str:
    account = _clean(value).lower()
    return "swap" if account == "perp" else account


def display_transfer_account(value: str) -> str:
    return "perp" if normalize_transfer_account(value) == "swap" else _clean(value).lower()


def normalize_side(value: str) -> str:
    side = _clean(value).lower()
    if side not in ("buy", "sell"):
        raise ValueError(f"unsupported side: {value}")
    return side


def format_decimal(value: float) -> str:
    dec = Decimal(str(value)).normalize()
    text = format(dec, "f")
    return text.rstrip("0").rstrip(".") if "." in text else text


@dataclass(frozen=True)
class ExchangeCreds:
    api_key: str = field(default="", repr=False)
    api_secret: str = field(default="", repr=False)
    password: str | None = field(default=None, repr=False)

    def __repr__(self) -> str:
        has_password = self.password is not None
        return (
            "ExchangeCreds("
            "api_key=<redacted>, api_secret=<redacted>, "
            f"password={'<redacted>' if has_password else None})"
        )


@dataclass(frozen=True)
class OrderIntent:
    exchange: str
    market: str
    symbol: str
    side: str
    quote_usdt: float | None = None
    base_amount: float | None = None
    leverage: int = 1
    margin_mode: str = "cross"
    position_mode: str = "oneway"
    account_mode: str | None = None

    def __post_init__(self) -> None:
        object.__setattr__(self, "exchange", normalize_exchange(self.exchange))
        object.__setattr__(self, "market", normalize_market(self.market))
        object.__setattr__(self, "side", normalize_side(self.side))
        object.__setattr__(self, "symbol", _clean(self.symbol))
        if not self.symbol:
            raise ValueError("symbol is required")
        if self.quote_usdt is None and self.base_amount is None:
            raise ValueError("quote_usdt or base_amount is required")
        if self.quote_usdt is not None and float(self.quote_usdt) <= 0:
            raise ValueError("quote_usdt must be positive")
        if self.base_amount is not None and float(self.base_amount) <= 0:
            raise ValueError("base_amount must be positive")
        if int(self.leverage) < 1:
            raise ValueError("leverage must be >= 1")


@dataclass(frozen=True)
class OrderPlan:
    exchange: str
    market: str
    symbol: str
    side: str
    last_price: float
    base_amount: float
    contract_amount: float | None
    cost_amount: float | None
    amount: float
    quote_usdt: float
    params: dict[str, Any]
    order_method: str
    live_confirm_phrase: str
    account_mode: str | None = None

    def to_dict(self) -> dict[str, Any]:
        payload = asdict(self)
        payload["market"] = display_market(self.market)
        return payload


@dataclass(frozen=True)
class CapabilityReport:
    exchange: str
    market: str
    public_ok: bool
    private_read_ok: bool
    trade_supported: bool
    transfer_supported: bool
    adapter_trade_implemented: bool
    adapter_transfer_implemented: bool
    private_verified: bool
    notes: list[str]

    def to_dict(self) -> dict[str, Any]:
        payload = asdict(self)
        payload["market"] = display_market(self.market)
        return payload


@dataclass(frozen=True)
class TransferIntent:
    exchange: str
    code: str
    amount: float
    from_account: str
    to_account: str

    def __post_init__(self) -> None:
        object.__setattr__(self, "exchange", normalize_exchange(self.exchange))
        object.__setattr__(self, "code", _clean(self.code).upper())
        object.__setattr__(self, "from_account", normalize_transfer_account(self.from_account))
        object.__setattr__(self, "to_account", normalize_transfer_account(self.to_account))
        if not self.code:
            raise ValueError("code is required")
        if float(self.amount) <= 0:
            raise ValueError("amount must be positive")
        if not self.from_account or not self.to_account:
            raise ValueError("from_account and to_account are required")


@dataclass(frozen=True)
class WalletSnapshot:
    exchange: str
    balances: dict[str, Any]
    positions: list[dict[str, Any]]
    open_orders: list[dict[str, Any]]

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)
