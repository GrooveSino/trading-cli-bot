from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal
from typing import Any

from trading_gateway.domain.models import normalize_exchange
from trading_gateway.support.redaction import redact_mapping

from .common import _maybe_num, _num


@dataclass(frozen=True)
class OkxBracketIntent:
    exchange: str
    symbol: str
    side: str
    size: float
    take_profit: float | None = None
    stop_loss: float | None = None
    margin_mode: str = "cross"
    trigger_px_type: str = "last"
    order_px: str = "-1"

    def __post_init__(self) -> None:
        object.__setattr__(self, "exchange", normalize_exchange(self.exchange))
        if self.exchange != "okx":
            raise ValueError("risk bracket orders currently support only okx")
        symbol = str(self.symbol or "").strip().upper()
        object.__setattr__(self, "symbol", symbol)
        if not symbol:
            raise ValueError("symbol is required")
        side = str(self.side or "").strip().lower()
        if side not in {"long", "short"}:
            raise ValueError("side must be long or short")
        object.__setattr__(self, "side", side)
        if Decimal(str(self.size)) <= 0:
            raise ValueError("size must be positive")
        if self.take_profit is None and self.stop_loss is None:
            raise ValueError("take_profit or stop_loss is required")
        trigger_px_type = str(self.trigger_px_type or "").strip().lower()
        if trigger_px_type not in {"last", "index", "mark"}:
            raise ValueError("trigger_px_type must be last, index, or mark")
        object.__setattr__(self, "trigger_px_type", trigger_px_type)
        margin_mode = str(self.margin_mode or "").strip().lower()
        if margin_mode not in {"cross", "isolated"}:
            raise ValueError("margin_mode must be cross or isolated")
        object.__setattr__(self, "margin_mode", margin_mode)


def build_okx_bracket_plan(
    exchange: str,
    symbol: str,
    side: str,
    size: float,
    *,
    take_profit: float | None = None,
    stop_loss: float | None = None,
    margin_mode: str = "cross",
    trigger_px_type: str = "last",
    order_px: str = "-1",
) -> dict[str, Any]:
    intent = OkxBracketIntent(
        exchange=exchange,
        symbol=symbol,
        side=side,
        size=size,
        take_profit=take_profit,
        stop_loss=stop_loss,
        margin_mode=margin_mode,
        trigger_px_type=trigger_px_type,
        order_px=order_px,
    )
    orders = []
    if intent.take_profit is not None:
        orders.append({"kind": "take_profit", "payload": _algo_payload(intent, "take_profit", intent.take_profit)})
    if intent.stop_loss is not None:
        orders.append({"kind": "stop_loss", "payload": _algo_payload(intent, "stop_loss", intent.stop_loss)})
    return redact_mapping(
        {
            "mode": "plan",
            "exchange": intent.exchange,
            "symbol": intent.symbol,
            "position_side": intent.side,
            "size": _num(intent.size),
            "take_profit": _maybe_num(intent.take_profit),
            "stop_loss": _maybe_num(intent.stop_loss),
            "margin_mode": intent.margin_mode,
            "trigger_px_type": intent.trigger_px_type,
            "algo_orders": orders,
            "confirm_phrase": okx_bracket_confirm_phrase(intent),
        }
    )


def okx_bracket_confirm_phrase(intent: OkxBracketIntent) -> str:
    tp = _maybe_num(intent.take_profit) or "NONE"
    sl = _maybe_num(intent.stop_loss) or "NONE"
    return f"LIVE_BRACKET:{intent.exchange}:{intent.symbol}:{intent.side}:{_num(intent.size)}:TP_{tp}:SL_{sl}"


def _algo_payload(intent: OkxBracketIntent, kind: str, trigger_price: float) -> dict[str, str]:
    prefix = "tp" if kind == "take_profit" else "sl"
    return {
        "instId": intent.symbol,
        "tdMode": intent.margin_mode,
        "side": "sell" if intent.side == "long" else "buy",
        "ordType": "conditional",
        "sz": _num(intent.size),
        "reduceOnly": "true",
        f"{prefix}TriggerPx": _num(trigger_price),
        f"{prefix}OrdPx": str(intent.order_px),
        f"{prefix}TriggerPxType": intent.trigger_px_type,
    }
