from __future__ import annotations

from decimal import Decimal
from typing import Any

from ..common import _decimal_num, _num


def _position_side(parent_side: str, position_mode: str | None) -> str | None:
    mode = str(position_mode or "").strip().lower()
    if mode in {"long_short_mode", "hedge", "hedge_mode"}:
        return "long" if parent_side == "buy" else "short"
    return None


def _validate_specs(contract: Decimal, lot: Decimal, minimum: Decimal) -> None:
    if contract <= 0:
        raise ValueError("contract value must be positive")
    if lot <= 0:
        raise ValueError("lot size must be positive")
    if minimum <= 0:
        raise ValueError("minimum size must be positive")


def _validate_exit_geometry(side: str, entry_px: Decimal, stop_loss: Decimal, take_profits: list[tuple[float, float]]) -> None:
    tp_prices = [Decimal(str(price)) for price, _ in take_profits]
    if side == "buy":
        if stop_loss >= entry_px:
            raise ValueError("long stop_loss must be below entry price")
        if any(price <= entry_px for price in tp_prices):
            raise ValueError("long take-profit prices must be above entry price")
    else:
        if stop_loss <= entry_px:
            raise ValueError("short stop_loss must be above entry price")
        if any(price >= entry_px for price in tp_prices):
            raise ValueError("short take-profit prices must be below entry price")


def _validate_split_sizes(split_take_profits: list[tuple[Decimal, Decimal, Decimal]], minimum: Decimal) -> None:
    for _, _, tp_size in split_take_profits:
        if tp_size < minimum:
            raise ValueError("computed take-profit size is below minimum order size")


def _specs_payload(contract: Decimal, lot: Decimal, minimum: Decimal, tick_size: float | str | None, position_mode: str | None) -> dict[str, Any]:
    payload = {
        "contract_value": _decimal_num(contract),
        "lot_size": _decimal_num(lot),
        "min_size": _decimal_num(minimum),
    }
    if tick_size is not None:
        payload["tick_size"] = str(tick_size)
    if position_mode:
        payload["position_mode"] = str(position_mode)
    return payload


def _trigger_oco_confirm_phrase(exchange: str, symbol: str, leverage: int, orders: list[dict[str, Any]]) -> str:
    parts = []
    for order in orders:
        tp_text = ",".join(
            f"{_num(tp['trigger_px'])}@{_num(tp['allocation_pct'])}" for tp in order["exit_plan"]["take_profits"]
        )
        parts.append(
            f"{order['label']}:{order['side']}:TRIG_{_num(order['trigger_price'])}:PX_{_num(order['entry_price'])}:"
            f"NOTIONAL_{_num(order['target_notional_usdt'])}:SL_{_num(order['stop_loss'])}:TP_{tp_text}"
        )
    return f"LIVE_TRIGGER_OCO:{exchange}:{symbol}:LEV_{int(leverage)}:LEGS_{';'.join(parts)}"
