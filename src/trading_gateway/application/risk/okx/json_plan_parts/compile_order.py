from __future__ import annotations

from decimal import Decimal
from typing import Any

from ..common import _decimal_num, _floor_to_lot, _validate_trigger_px_type

ORDER_KEYS = {"id", "side", "entry", "notional_usdt", "stop_loss", "take_profits"}
ENTRY_KEYS = {"type", "trigger_price", "order_price", "price", "trigger_price_type", "post_only"}
STOP_KEYS = {"trigger_price", "order_price", "trigger_price_type"}
TP_KEYS = {"price", "size_pct"}


def _compile_order(
    order: Any,
    *,
    index: int,
    inst_id: str,
    margin_mode: str,
    leverage: int,
    contract: Decimal,
    lot: Decimal,
    minimum: Decimal,
    position_mode: str,
) -> list[dict[str, Any]]:
    if not isinstance(order, dict):
        raise ValueError("each order must be an object")
    _reject_unknown(order, ORDER_KEYS, f"orders[{index}]")
    order_id = str(order.get("id") or f"order_{index}").strip()
    side = str(order.get("side") or "").strip().lower()
    if side not in {"buy", "sell"}:
        raise ValueError(f"{order_id}: side must be buy or sell")
    entry = order.get("entry")
    if not isinstance(entry, dict):
        raise ValueError(f"{order_id}: entry must be an object")
    _reject_unknown(entry, ENTRY_KEYS, f"{order_id}.entry")
    entry_type = str(entry.get("type") or "").strip().lower().replace("-", "_")
    if entry_type not in {"stop_limit", "limit", "post_only"}:
        raise ValueError(f"{order_id}: entry.type must be stop_limit, limit, or post_only")
    entry_px = Decimal(str(entry.get("order_price") if entry_type == "stop_limit" else entry.get("price")))
    if entry_px <= 0:
        raise ValueError(f"{order_id}: entry price must be positive")
    trigger_px = Decimal(str(entry.get("trigger_price"))) if entry_type == "stop_limit" else None
    if entry_type == "stop_limit" and (trigger_px is None or trigger_px <= 0):
        raise ValueError(f"{order_id}: stop_limit requires positive trigger_price")
    trigger_type = _validate_trigger_px_type(str(entry.get("trigger_price_type") or "last"), "entry.trigger_price_type")

    stop_loss = order.get("stop_loss")
    if not isinstance(stop_loss, dict):
        raise ValueError(f"{order_id}: stop_loss must be an object")
    _reject_unknown(stop_loss, STOP_KEYS, f"{order_id}.stop_loss")
    sl_px = Decimal(str(stop_loss.get("trigger_price")))
    sl_order_px = str(stop_loss.get("order_price", "-1"))
    sl_trigger_type = _validate_trigger_px_type(str(stop_loss.get("trigger_price_type") or "mark"), "stop_loss.trigger_price_type")

    take_profits = order.get("take_profits")
    if not isinstance(take_profits, list) or not take_profits:
        raise ValueError(f"{order_id}: take_profits must be a non-empty array")
    parsed_tps = []
    total_pct = Decimal("0")
    for tp_index, tp in enumerate(take_profits, start=1):
        if not isinstance(tp, dict):
            raise ValueError(f"{order_id}.take_profits[{tp_index}] must be an object")
        _reject_unknown(tp, TP_KEYS, f"{order_id}.take_profits[{tp_index}]")
        pct = Decimal(str(tp.get("size_pct")))
        price = Decimal(str(tp.get("price")))
        if pct <= 0 or price <= 0:
            raise ValueError(f"{order_id}: TP price and size_pct must be positive")
        parsed_tps.append({"price": price, "size_pct": pct})
        total_pct += pct
    if total_pct != Decimal("100"):
        raise ValueError(f"{order_id}: take_profit size_pct total must equal 100")
    _validate_geometry(order_id, side, entry_px, sl_px, [tp["price"] for tp in parsed_tps])

    notional = Decimal(str(order.get("notional_usdt")))
    if notional <= 0:
        raise ValueError(f"{order_id}: notional_usdt must be positive")
    parent_pos_side = _pos_side(side, position_mode)
    close_side = "buy" if side == "sell" else "sell"
    close_pos_side = parent_pos_side

    if entry_type == "stop_limit" and len(parsed_tps) > 1:
        pieces = []
        remaining_notional = notional
        for tp_index, tp in enumerate(parsed_tps, start=1):
            if tp_index == len(parsed_tps):
                child_notional = remaining_notional
            else:
                child_notional = (notional * tp["size_pct"] / Decimal("100")).quantize(Decimal("0.00000001"))
                remaining_notional -= child_notional
            pieces.append((tp_index, child_notional, tp))
    else:
        pieces = [(1, notional, parsed_tps[0])]

    compiled = []
    for tp_index, child_notional, tp in pieces:
        size = _floor_to_lot(child_notional / (entry_px * contract), lot)
        if size < minimum:
            raise ValueError(f"{order_id}: compiled size is below minimum order size")
        actual_notional = size * entry_px * contract
        payload = {
            "instId": inst_id,
            "tdMode": margin_mode,
            "side": side,
            "sz": _decimal_num(size),
        }
        if parent_pos_side:
            payload["posSide"] = parent_pos_side
        attach = [
            {
                "tpTriggerPx": _decimal_num(tp["price"]),
                "tpOrdPx": "-1",
                "tpTriggerPxType": "last",
                "sz": _decimal_num(size),
            },
            {
                "slTriggerPx": _decimal_num(sl_px),
                "slOrdPx": sl_order_px,
                "slTriggerPxType": sl_trigger_type,
                "sz": _decimal_num(size),
            },
        ]
        if close_pos_side:
            for row in attach:
                row["posSide"] = close_pos_side
        if entry_type == "stop_limit":
            payload.update(
                {
                    "ordType": "trigger",
                    "triggerPx": _decimal_num(trigger_px or Decimal("0")),
                    "triggerPxType": trigger_type,
                    "orderPx": _decimal_num(entry_px),
                    "attachAlgoOrds": attach,
                }
            )
            endpoint = "order_algo"
        else:
            payload.update({"ordType": entry_type, "px": _decimal_num(entry_px), "attachAlgoOrds": attach})
            endpoint = "order"
        compiled.append(
            {
                "id": f"{order_id}#{tp_index}" if len(pieces) > 1 else order_id,
                "source_id": order_id,
                "side": side,
                "entry_type": entry_type,
                "entry_price": _decimal_num(entry_px),
                "trigger_price": _decimal_num(trigger_px) if trigger_px is not None else None,
                "target_notional_usdt": _decimal_num(child_notional),
                "actual_notional_usdt": _decimal_num(actual_notional),
                "notional_shortfall_usdt": _decimal_num(child_notional - actual_notional),
                "size": _decimal_num(size),
                "margin_usdt_est": _decimal_num(actual_notional / Decimal(str(leverage))),
                "endpoint": endpoint,
                "payload": payload,
            }
        )
    return compiled


def _validate_geometry(order_id: str, side: str, entry_px: Decimal, sl_px: Decimal, tp_prices: list[Decimal]) -> None:
    if side == "buy":
        if sl_px >= entry_px:
            raise ValueError(f"{order_id}: long stop_loss must be below entry")
        if any(tp <= entry_px for tp in tp_prices):
            raise ValueError(f"{order_id}: long take_profit prices must be above entry")
    else:
        if sl_px <= entry_px:
            raise ValueError(f"{order_id}: short stop_loss must be above entry")
        if any(tp >= entry_px for tp in tp_prices):
            raise ValueError(f"{order_id}: short take_profit prices must be below entry")


def _pos_side(side: str, position_mode: str) -> str | None:
    mode = position_mode.strip().lower()
    if mode in {"long_short_mode", "hedge", "hedge_mode"}:
        return "long" if side == "buy" else "short"
    return None


def _reject_unknown(payload: dict[str, Any], allowed: set[str], label: str) -> None:
    extra = sorted(set(payload) - allowed)
    if extra:
        raise ValueError(f"{label} contains unknown field(s): {', '.join(extra)}")


def _confirm_phrase(exchange: str, symbol: str, leverage: int, margin_mode: str, orders: list[dict[str, Any]]) -> str:
    parts = [
        f"{order['id']}:{order['side']}:{order['entry_type']}:PX_{order['entry_price']}:"
        f"TRIG_{order.get('trigger_price') or 'NONE'}:NOTIONAL_{order['target_notional_usdt']}"
        for order in orders
    ]
    return f"LIVE_JSON_PLAN:{exchange}:{symbol}:LEV_{int(leverage)}:{margin_mode}:ORDERS_{';'.join(parts)}"
