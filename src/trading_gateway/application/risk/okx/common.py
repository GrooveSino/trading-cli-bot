from __future__ import annotations

from decimal import Decimal, ROUND_FLOOR, ROUND_HALF_UP
from typing import Any

from trading_gateway.domain.models import format_decimal


def _maybe_num(value: float | None) -> str | None:
    return None if value is None else _num(value)


def _num(value: float) -> str:
    return format_decimal(float(value))


def _okx_swap_inst_id(symbol: str) -> str:
    raw = str(symbol or "").strip().upper()
    if not raw:
        raise ValueError("symbol is required")
    if raw.endswith("-SWAP"):
        return raw
    if raw in {"BTCUSDT", "BTC/USDT", "BTC/USDT:USDT"}:
        return "BTC-USDT-SWAP"
    if "/" in raw:
        base, quote = raw.split("/", 1)
        quote = quote.split(":", 1)[0]
        return f"{base}-{quote}-SWAP"
    if raw.endswith("USDT"):
        return f"{raw[:-4]}-USDT-SWAP"
    return raw


def _floor_to_lot(value: Decimal, lot: Decimal) -> Decimal:
    return (value / lot).to_integral_value(rounding=ROUND_FLOOR) * lot


def _split_size(size: Decimal, take_profits: list[tuple[float, float]], lot: Decimal) -> list[tuple[Decimal, Decimal, Decimal]]:
    parts: list[tuple[Decimal, Decimal, Decimal]] = []
    remaining = size
    for index, (price, ratio_pct) in enumerate(take_profits):
        px = Decimal(str(price))
        ratio = Decimal(str(ratio_pct))
        if px <= 0 or ratio <= 0:
            raise ValueError("take-profit price and allocation must be positive")
        if index == len(take_profits) - 1:
            part_size = remaining
        else:
            part_size = (size * ratio / Decimal("100") / lot).to_integral_value(rounding=ROUND_HALF_UP) * lot
            remaining -= part_size
        if part_size <= 0:
            raise ValueError("computed take-profit size is below lot size")
        parts.append((px, ratio, part_size))
    if remaining != parts[-1][2]:
        raise AssertionError("internal split accounting error")
    return parts


def _decimal_num(value: Decimal) -> str:
    return format(value.normalize(), "f")


def _maybe_decimal_num(value: Decimal | None) -> str | None:
    return None if value is None else _decimal_num(value)


def _validate_trigger_px_type(value: str, name: str) -> str:
    trigger_px_type = str(value or "").strip().lower()
    if trigger_px_type not in {"last", "index", "mark"}:
        raise ValueError(f"{name} must be last, index, or mark")
    return trigger_px_type


def attached_algo_orders(prefix: str, size: Decimal, take_profits: list[tuple[float, float]], stop_loss: Decimal | float, *, lot: Decimal, tp_trigger_px_type: str, sl_trigger_px_type: str) -> list[dict[str, str]]:
    rows = []
    for index, (tp_px, _, tp_size) in enumerate(_split_size(size, take_profits, lot), start=1):
        rows.append(
            {
                "attachAlgoClOrdId": f"{prefix}tp{index}",
                "tpTriggerPx": _decimal_num(tp_px),
                "tpOrdPx": "-1",
                "tpTriggerPxType": tp_trigger_px_type,
                "sz": _decimal_num(tp_size),
            }
        )
    rows.append(
        {
            "attachAlgoClOrdId": f"{prefix}sl",
            "slTriggerPx": _decimal_num(Decimal(str(stop_loss))),
            "slOrdPx": "-1",
            "slTriggerPxType": sl_trigger_px_type,
            "sz": _decimal_num(size),
        }
    )
    return rows
