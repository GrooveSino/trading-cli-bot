from __future__ import annotations

from decimal import Decimal
from typing import Any

from trading_gateway.domain.models import normalize_exchange
from trading_gateway.support.redaction import redact_mapping

from ..common import _decimal_num, _floor_to_lot, _maybe_decimal_num, _num, _okx_swap_inst_id, _split_size, _validate_trigger_px_type
from .helpers import _position_side, _specs_payload, _validate_exit_geometry, _validate_specs, _validate_split_sizes


def build_okx_static_notional_plan(
    exchange: str,
    symbol: str,
    *,
    side: str,
    entries: list[dict[str, Any]],
    leverage: int = 15,
    margin_mode: str = "isolated",
    tp_trigger_px_type: str = "last",
    sl_trigger_px_type: str = "mark",
    trigger_px_type: str = "last",
    stop_limit_mode: str = "auto",
    contract_btc: float = 0.01,
    lot_size: float = 0.01,
    min_size: float | None = None,
    tick_size: float | str | None = None,
    position_mode: str | None = None,
) -> dict[str, Any]:
    exchange = normalize_exchange(exchange)
    if exchange != "okx":
        raise ValueError("static-notional currently supports only okx")
    inst_id = _okx_swap_inst_id(symbol)
    if int(leverage) <= 0:
        raise ValueError("leverage must be positive")
    margin_mode = str(margin_mode or "").strip().lower()
    if margin_mode not in {"isolated", "cross"}:
        raise ValueError("margin_mode must be isolated or cross")
    parent_side = str(side or "").strip().lower()
    if parent_side not in {"buy", "sell"}:
        raise ValueError("side must be buy or sell")
    tp_trigger_px_type = _validate_trigger_px_type(tp_trigger_px_type, "tp_trigger_px_type")
    sl_trigger_px_type = _validate_trigger_px_type(sl_trigger_px_type, "sl_trigger_px_type")
    trigger_px_type = _validate_trigger_px_type(trigger_px_type, "trigger_px_type")
    stop_limit_mode = str(stop_limit_mode or "auto").strip().lower()
    if stop_limit_mode not in {"auto", "managed"}:
        raise ValueError("stop_limit_mode must be auto or managed")
    if not entries:
        raise ValueError("at least one entry is required")

    contract = Decimal(str(contract_btc))
    lot = Decimal(str(lot_size))
    minimum = Decimal(str(min_size if min_size is not None else lot_size))
    _validate_specs(contract, lot, minimum)
    pos_side = _position_side(parent_side, position_mode)
    orders: list[dict[str, Any]] = []
    total_target_notional = Decimal("0")
    total_actual_notional = Decimal("0")
    for index, entry in enumerate(entries, start=1):
        kind = str(entry["kind"]).strip().lower().replace("-", "_")
        if kind not in {"limit", "post_only", "stop_limit"}:
            raise ValueError("entry kind must be limit, post_only, or stop_limit")
        order_px = Decimal(str(entry["price"]))
        target_notional = Decimal(str(entry["notional_usdt"]))
        stop_loss = Decimal(str(entry["stop_loss"]))
        take_profits = [(float(tp["price"]), float(tp["allocation_pct"])) for tp in entry["take_profits"]]
        if order_px <= 0 or target_notional <= 0:
            raise ValueError("entry price and notional_usdt must be positive")
        if stop_loss <= 0:
            raise ValueError("entry stop_loss must be positive")
        if kind == "stop_limit" and Decimal(str(entry.get("trigger_price") or "0")) <= 0:
            raise ValueError("stop_limit entries require positive trigger_price")
        tp_alloc_total = sum(Decimal(str(alloc)) for _, alloc in take_profits)
        if tp_alloc_total != Decimal("100"):
            raise ValueError("each entry take-profit allocation percent total must equal 100")
        _validate_exit_geometry(parent_side, order_px, stop_loss, take_profits)

        raw_size = target_notional / (order_px * contract)
        size = _floor_to_lot(raw_size, lot)
        if size < minimum:
            raise ValueError("computed order size is below lot size")
        actual_notional = size * contract * order_px
        total_target_notional += target_notional
        total_actual_notional += actual_notional

        clord_prefix = f"notional{parent_side}{_decimal_num(order_px)}{index}".replace(".", "")
        exit_plan = None
        attach_algo_orders = []
        split_take_profits = _split_size(size, take_profits, lot)
        _validate_split_sizes(split_take_profits, minimum)
        for tp_index, (tp_px, tp_alloc, tp_size) in enumerate(split_take_profits, start=1):
            attach_algo_orders.append(
                {
                    "attachAlgoClOrdId": f"{clord_prefix}tp{tp_index}",
                    "tpTriggerPx": _decimal_num(tp_px),
                    "tpOrdPx": "-1",
                    "tpTriggerPxType": tp_trigger_px_type,
                    "sz": _decimal_num(tp_size),
                }
            )
        attach_algo_orders.append(
            {
                "attachAlgoClOrdId": f"{clord_prefix}sl",
                "slTriggerPx": _decimal_num(stop_loss),
                "slOrdPx": "-1",
                "slTriggerPxType": sl_trigger_px_type,
                "sz": _decimal_num(size),
            }
        )

        payload: dict[str, Any] = {
            "instId": inst_id,
            "tdMode": margin_mode,
            "side": parent_side,
            "sz": _decimal_num(size),
        }
        if pos_side:
            payload["posSide"] = pos_side
        if kind == "stop_limit":
            close_side = "buy" if parent_side == "sell" else "sell"
            exit_plan = {
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
            }
            payload.update(
                {
                    "ordType": "trigger",
                    "triggerPx": _decimal_num(Decimal(str(entry["trigger_price"]))),
                    "triggerPxType": trigger_px_type,
                    "orderPx": _decimal_num(order_px),
                }
            )
            endpoint = "managed_order_algo"
        else:
            payload.update({"ordType": kind, "px": _decimal_num(order_px), "attachAlgoOrds": attach_algo_orders})
            endpoint = "order"

        order = {
            "label": f"ORDER_{index}",
            "kind": kind,
            "endpoint": endpoint,
            "side": parent_side,
            "entry_price": _decimal_num(order_px),
            "trigger_price": _maybe_decimal_num(Decimal(str(entry["trigger_price"]))) if kind == "stop_limit" else None,
            "target_notional_usdt": _decimal_num(target_notional),
            "actual_notional_usdt": _decimal_num(actual_notional),
            "notional_shortfall_usdt": _decimal_num(target_notional - actual_notional),
            "stop_loss": _decimal_num(stop_loss),
            "take_profit_allocation_total_pct": _decimal_num(tp_alloc_total),
            "size": _decimal_num(size),
            "margin_usdt_est": _decimal_num(actual_notional / Decimal(str(leverage))),
            "payload": payload,
        }
        if exit_plan:
            order["exit_plan"] = exit_plan
        orders.append(order)

    confirm = _static_notional_confirm_phrase(exchange, inst_id, leverage, parent_side, entries)
    return redact_mapping(
        {
            "mode": "static_notional_plan",
            "exchange": exchange,
            "symbol": inst_id,
            "leverage": str(int(leverage)),
            "margin_mode": margin_mode,
            "side": parent_side,
            "target_notional_usdt": _decimal_num(total_target_notional),
            "actual_notional_usdt": _decimal_num(total_actual_notional),
            "notional_shortfall_usdt": _decimal_num(total_target_notional - total_actual_notional),
            "tp_trigger_px_type": tp_trigger_px_type,
            "sl_trigger_px_type": sl_trigger_px_type,
            "trigger_px_type": trigger_px_type,
            "stop_limit_mode": stop_limit_mode,
            "instrument_specs": _specs_payload(contract, lot, minimum, tick_size, position_mode),
            "source_entries": entries,
            "orders": orders,
            "confirm_phrase": confirm,
        }
    )


def _static_notional_confirm_phrase(
    exchange: str,
    symbol: str,
    leverage: int,
    side: str,
    entries: list[dict[str, Any]],
) -> str:
    entry_texts = []
    for entry in entries:
        tp_text = ",".join(f"{_num(tp['price'])}@{_num(tp['allocation_pct'])}" for tp in entry["take_profits"])
        trigger = f":TRIG_{_num(entry['trigger_price'])}" if str(entry["kind"]).strip().lower().replace("-", "_") == "stop_limit" else ""
        entry_texts.append(
            f"{entry['kind']}:{_num(entry['price'])}:NOTIONAL_{_num(entry['notional_usdt'])}{trigger}:"
            f"SL_{_num(entry['stop_loss'])}:TP_{tp_text}"
        )
    return f"LIVE_STATIC_NOTIONAL:{exchange}:{symbol}:{side}:LEV_{int(leverage)}:ENTRIES_{';'.join(entry_texts)}"

