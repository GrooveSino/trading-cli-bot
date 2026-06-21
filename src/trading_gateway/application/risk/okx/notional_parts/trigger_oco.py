from __future__ import annotations

from decimal import Decimal
from typing import Any

from trading_gateway.domain.models import normalize_exchange
from trading_gateway.support.redaction import redact_mapping

from ..common import _decimal_num, _floor_to_lot, _okx_swap_inst_id, _split_size, _validate_trigger_px_type
from .helpers import _position_side, _specs_payload, _trigger_oco_confirm_phrase, _validate_exit_geometry, _validate_specs, _validate_split_sizes


def build_okx_trigger_oco_plan(
    exchange: str,
    symbol: str,
    *,
    legs: list[dict[str, Any]],
    leverage: int = 15,
    margin_mode: str = "isolated",
    tp_trigger_px_type: str = "last",
    sl_trigger_px_type: str = "mark",
    trigger_px_type: str = "last",
    contract_btc: float = 0.01,
    lot_size: float = 0.01,
    min_size: float | None = None,
    tick_size: float | str | None = None,
    position_mode: str | None = None,
) -> dict[str, Any]:
    exchange = normalize_exchange(exchange)
    if exchange != "okx":
        raise ValueError("trigger-oco currently supports only okx")
    inst_id = _okx_swap_inst_id(symbol)
    if len(legs) != 2:
        raise ValueError("trigger-oco requires exactly two --leg values")
    margin_mode = str(margin_mode or "").strip().lower()
    if margin_mode not in {"isolated", "cross"}:
        raise ValueError("margin_mode must be isolated or cross")
    tp_trigger_px_type = _validate_trigger_px_type(tp_trigger_px_type, "tp_trigger_px_type")
    sl_trigger_px_type = _validate_trigger_px_type(sl_trigger_px_type, "sl_trigger_px_type")
    trigger_px_type = _validate_trigger_px_type(trigger_px_type, "trigger_px_type")

    contract = Decimal(str(contract_btc))
    lot = Decimal(str(lot_size))
    minimum = Decimal(str(min_size if min_size is not None else lot_size))
    _validate_specs(contract, lot, minimum)
    orders: list[dict[str, Any]] = []
    total_target_notional = Decimal("0")
    total_actual_notional = Decimal("0")
    for index, leg in enumerate(legs, start=1):
        label = str(leg.get("label") or f"LEG_{index}").strip().upper()
        side = str(leg["side"]).strip().lower()
        if side not in {"buy", "sell"}:
            raise ValueError("leg side must be buy or sell")
        trigger_px = Decimal(str(leg["trigger_price"]))
        order_px = Decimal(str(leg["order_price"]))
        target_notional = Decimal(str(leg["notional_usdt"]))
        stop_loss = Decimal(str(leg["stop_loss"]))
        take_profits = [(float(tp["price"]), float(tp["allocation_pct"])) for tp in leg["take_profits"]]
        if trigger_px <= 0 or order_px <= 0 or target_notional <= 0 or stop_loss <= 0:
            raise ValueError("leg trigger, order, notional, and stop_loss must be positive")
        tp_alloc_total = sum(Decimal(str(alloc)) for _, alloc in take_profits)
        if tp_alloc_total != Decimal("100"):
            raise ValueError("each leg take-profit allocation percent total must equal 100")
        _validate_exit_geometry(side, order_px, stop_loss, take_profits)

        raw_size = target_notional / (order_px * contract)
        size = _floor_to_lot(raw_size, lot)
        if size < minimum:
            raise ValueError("computed order size is below lot size")
        actual_notional = size * contract * order_px
        total_target_notional += target_notional
        total_actual_notional += actual_notional
        close_side = "buy" if side == "sell" else "sell"
        pos_side = _position_side(side, position_mode)
        split_take_profits = _split_size(size, take_profits, lot)
        _validate_split_sizes(split_take_profits, minimum)
        payload = {
            "instId": inst_id,
            "tdMode": margin_mode,
            "side": side,
            "ordType": "trigger",
            "triggerPx": _decimal_num(trigger_px),
            "triggerPxType": trigger_px_type,
            "orderPx": _decimal_num(order_px),
            "sz": _decimal_num(size),
        }
        if pos_side:
            payload["posSide"] = pos_side
        orders.append(
            {
                "label": label,
                "kind": "stop_limit",
                "endpoint": "managed_order_algo",
                "side": side,
                "entry_price": _decimal_num(order_px),
                "trigger_price": _decimal_num(trigger_px),
                "target_notional_usdt": _decimal_num(target_notional),
                "actual_notional_usdt": _decimal_num(actual_notional),
                "notional_shortfall_usdt": _decimal_num(target_notional - actual_notional),
                "stop_loss": _decimal_num(stop_loss),
                "take_profit_allocation_total_pct": _decimal_num(tp_alloc_total),
                "size": _decimal_num(size),
                "margin_usdt_est": _decimal_num(actual_notional / Decimal(str(leverage))),
                "payload": payload,
                "exit_plan": {
                    "mode": "managed_after_fill",
                    "close_side": close_side,
                    "pos_side": pos_side,
                    "lot_size": _decimal_num(lot),
                    "min_size": _decimal_num(minimum),
                    "stop_loss": {
                        "side": close_side,
                        "trigger_px": _decimal_num(stop_loss),
                        "trigger_px_type": sl_trigger_px_type,
                        "order_px": "-1",
                        "target_size": _decimal_num(size),
                    },
                    "take_profits": [
                        {
                            "side": close_side,
                            "trigger_px": _decimal_num(tp_px),
                            "trigger_px_type": tp_trigger_px_type,
                            "order_px": "-1",
                            "allocation_pct": _decimal_num(Decimal(str(tp_alloc))),
                            "target_size": _decimal_num(tp_size),
                        }
                        for tp_px, tp_alloc, tp_size in split_take_profits
                    ],
                },
            }
        )

    confirm = _trigger_oco_confirm_phrase(exchange, inst_id, leverage, orders)
    return redact_mapping(
        {
            "mode": "trigger_oco_plan",
            "exchange": exchange,
            "symbol": inst_id,
            "leverage": str(int(leverage)),
            "margin_mode": margin_mode,
            "target_notional_usdt": _decimal_num(total_target_notional),
            "actual_notional_usdt": _decimal_num(total_actual_notional),
            "notional_shortfall_usdt": _decimal_num(total_target_notional - total_actual_notional),
            "total_potential_notional_usdt": _decimal_num(total_target_notional),
            "total_potential_actual_notional_usdt": _decimal_num(total_actual_notional),
            "max_active_notional_usdt": _decimal_num(max(Decimal(str(order["target_notional_usdt"])) for order in orders)),
            "max_active_actual_notional_usdt": _decimal_num(max(Decimal(str(order["actual_notional_usdt"])) for order in orders)),
            "tp_trigger_px_type": tp_trigger_px_type,
            "sl_trigger_px_type": sl_trigger_px_type,
            "trigger_px_type": trigger_px_type,
            "instrument_specs": _specs_payload(contract, lot, minimum, tick_size, position_mode),
            "source_legs": legs,
            "orders": orders,
            "confirm_phrase": confirm,
        }
    )

