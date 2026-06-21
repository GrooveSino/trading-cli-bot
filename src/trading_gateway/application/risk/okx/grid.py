from __future__ import annotations

from decimal import Decimal
from typing import Any

from trading_gateway.domain.models import normalize_exchange
from trading_gateway.support.redaction import redact_mapping

from .common import attached_algo_orders, _decimal_num, _floor_to_lot, _num, _okx_swap_inst_id, _split_size, _validate_trigger_px_type


def build_okx_grid_short_plan(
    exchange: str,
    symbol: str,
    assigned_usdt: float,
    *,
    entries: list[tuple[float, float]],
    take_profits: list[tuple[float, float]],
    stop_loss: float,
    leverage: int = 15,
    margin_mode: str = "isolated",
    contract_btc: float = 0.01,
    lot_size: float = 0.01,
) -> dict[str, Any]:
    exchange = normalize_exchange(exchange)
    if exchange != "okx":
        raise ValueError("grid-short currently supports only okx")
    inst_id = _okx_swap_inst_id(symbol)
    assigned = Decimal(str(assigned_usdt))
    if assigned <= 0:
        raise ValueError("assigned_usdt must be positive")
    if int(leverage) <= 0:
        raise ValueError("leverage must be positive")
    margin_mode = str(margin_mode or "").strip().lower()
    if margin_mode not in {"isolated", "cross"}:
        raise ValueError("margin_mode must be isolated or cross")
    if not entries:
        raise ValueError("at least one entry is required")
    if not take_profits:
        raise ValueError("at least one take-profit is required")
    entry_alloc_total = sum(Decimal(str(alloc)) for _, alloc in entries)
    tp_alloc_total = sum(Decimal(str(alloc)) for _, alloc in take_profits)
    if entry_alloc_total <= 0 or entry_alloc_total > 100:
        raise ValueError("entry allocation percent total must be > 0 and <= 100")
    if tp_alloc_total != Decimal("100"):
        raise ValueError("take-profit allocation percent total must equal 100")

    contract = Decimal(str(contract_btc))
    lot = Decimal(str(lot_size))
    orders: list[dict[str, Any]] = []
    for index, (entry_price, allocation_pct) in enumerate(entries, start=1):
        px = Decimal(str(entry_price))
        allocation = Decimal(str(allocation_pct))
        if px <= 0 or allocation <= 0:
            raise ValueError("entry price and allocation must be positive")
        raw_size = assigned * allocation / Decimal("100") * Decimal(str(leverage)) / (px * contract)
        size = _floor_to_lot(raw_size, lot)
        if size <= 0:
            raise ValueError("computed order size is below lot size")
        tp_parts = _split_size(size, take_profits, lot)
        attach_algo_orders = []
        for tp_index, (tp_px, _, tp_size) in enumerate(tp_parts, start=1):
            attach_algo_orders.append(
                {
                    "attachAlgoClOrdId": f"grid{index}tp{tp_index}",
                    "tpTriggerPx": _num(tp_px),
                    "tpOrdPx": "-1",
                    "tpTriggerPxType": "last",
                    "sz": _decimal_num(tp_size),
                }
            )
        attach_algo_orders.append(
            {
                "attachAlgoClOrdId": f"grid{index}sl",
                "slTriggerPx": _num(stop_loss),
                "slOrdPx": "-1",
                "slTriggerPxType": "mark",
                "sz": _decimal_num(size),
            }
        )
        orders.append(
            {
                "label": f"ORDER_{index}",
                "entry_price": _decimal_num(px),
                "allocation_pct": _decimal_num(allocation),
                "size": _decimal_num(size),
                "margin_usdt_est": _decimal_num(size * contract * px / Decimal(str(leverage))),
                "notional_usdt_est": _decimal_num(size * contract * px),
                "payload": {
                    "instId": inst_id,
                    "tdMode": margin_mode,
                    "side": "sell",
                    "ordType": "limit",
                    "px": _decimal_num(px),
                    "sz": _decimal_num(size),
                    "attachAlgoOrds": attach_algo_orders,
                },
            }
        )

    confirm = _grid_short_confirm_phrase(exchange, inst_id, assigned, leverage, entries, take_profits, stop_loss)
    return redact_mapping(
        {
            "mode": "grid_short_plan",
            "exchange": exchange,
            "symbol": inst_id,
            "assigned_usdt": _decimal_num(assigned),
            "leverage": str(int(leverage)),
            "margin_mode": margin_mode,
            "stop_loss": _num(stop_loss),
            "entry_allocation_total_pct": _decimal_num(entry_alloc_total),
            "take_profit_allocation_total_pct": _decimal_num(tp_alloc_total),
            "orders": orders,
            "confirm_phrase": confirm,
        }
    )


def build_okx_static_grid_plan(
    exchange: str,
    symbol: str,
    assigned_usdt: float,
    *,
    side: str,
    entries: list[dict[str, Any]],
    leverage: int = 15,
    margin_mode: str = "isolated",
    order_type: str = "post_only",
    tp_trigger_px_type: str = "last",
    sl_trigger_px_type: str = "mark",
    contract_btc: float = 0.01,
    lot_size: float = 0.01,
) -> dict[str, Any]:
    exchange = normalize_exchange(exchange)
    if exchange != "okx":
        raise ValueError("static-grid currently supports only okx")
    inst_id = _okx_swap_inst_id(symbol)
    assigned = Decimal(str(assigned_usdt))
    if assigned <= 0:
        raise ValueError("assigned_usdt must be positive")
    if int(leverage) <= 0:
        raise ValueError("leverage must be positive")
    margin_mode = str(margin_mode or "").strip().lower()
    if margin_mode not in {"isolated", "cross"}:
        raise ValueError("margin_mode must be isolated or cross")
    parent_side = str(side or "").strip().lower()
    if parent_side not in {"buy", "sell"}:
        raise ValueError("side must be buy or sell")
    order_type = str(order_type or "").strip().lower().replace("-", "_")
    if order_type not in {"limit", "post_only"}:
        raise ValueError("order_type must be limit or post_only")
    tp_trigger_px_type = _validate_trigger_px_type(tp_trigger_px_type, "tp_trigger_px_type")
    sl_trigger_px_type = _validate_trigger_px_type(sl_trigger_px_type, "sl_trigger_px_type")
    if not entries:
        raise ValueError("at least one entry is required")

    entry_alloc_total = sum(Decimal(str(entry["allocation_pct"])) for entry in entries)
    if entry_alloc_total <= 0 or entry_alloc_total > 100:
        raise ValueError("entry allocation percent total must be > 0 and <= 100")

    contract = Decimal(str(contract_btc))
    lot = Decimal(str(lot_size))
    orders: list[dict[str, Any]] = []
    for index, entry in enumerate(entries, start=1):
        px = Decimal(str(entry["price"]))
        allocation = Decimal(str(entry["allocation_pct"]))
        stop_loss = Decimal(str(entry["stop_loss"]))
        take_profits = [(float(tp["price"]), float(tp["allocation_pct"])) for tp in entry["take_profits"]]
        if px <= 0 or allocation <= 0:
            raise ValueError("entry price and allocation must be positive")
        if stop_loss <= 0:
            raise ValueError("entry stop_loss must be positive")
        tp_alloc_total = sum(Decimal(str(alloc)) for _, alloc in take_profits)
        if tp_alloc_total != Decimal("100"):
            raise ValueError("each entry take-profit allocation percent total must equal 100")

        raw_size = assigned * allocation / Decimal("100") * Decimal(str(leverage)) / (px * contract)
        size = _floor_to_lot(raw_size, lot)
        if size <= 0:
            raise ValueError("computed order size is below lot size")

        tp_parts = _split_size(size, take_profits, lot)
        attach_algo_orders = []
        for tp_index, (tp_px, _, tp_size) in enumerate(tp_parts, start=1):
            attach_algo_orders.append(
                {
                    "attachAlgoClOrdId": f"static{index}tp{tp_index}",
                    "tpTriggerPx": _decimal_num(tp_px),
                    "tpOrdPx": "-1",
                    "tpTriggerPxType": tp_trigger_px_type,
                    "sz": _decimal_num(tp_size),
                }
            )
        attach_algo_orders.append(
            {
                "attachAlgoClOrdId": f"static{index}sl",
                "slTriggerPx": _decimal_num(stop_loss),
                "slOrdPx": "-1",
                "slTriggerPxType": sl_trigger_px_type,
                "sz": _decimal_num(size),
            }
        )
        orders.append(
            {
                "label": f"ORDER_{index}",
                "side": parent_side,
                "order_type": order_type,
                "entry_price": _decimal_num(px),
                "allocation_pct": _decimal_num(allocation),
                "stop_loss": _decimal_num(stop_loss),
                "take_profit_allocation_total_pct": _decimal_num(tp_alloc_total),
                "size": _decimal_num(size),
                "margin_usdt_est": _decimal_num(size * contract * px / Decimal(str(leverage))),
                "notional_usdt_est": _decimal_num(size * contract * px),
                "payload": {
                    "instId": inst_id,
                    "tdMode": margin_mode,
                    "side": parent_side,
                    "ordType": order_type,
                    "px": _decimal_num(px),
                    "sz": _decimal_num(size),
                    "attachAlgoOrds": attach_algo_orders,
                },
            }
        )

    confirm = _static_grid_confirm_phrase(exchange, inst_id, assigned, leverage, parent_side, order_type, entries)
    return redact_mapping(
        {
            "mode": "static_grid_plan",
            "exchange": exchange,
            "symbol": inst_id,
            "assigned_usdt": _decimal_num(assigned),
            "leverage": str(int(leverage)),
            "margin_mode": margin_mode,
            "side": parent_side,
            "order_type": order_type,
            "entry_allocation_total_pct": _decimal_num(entry_alloc_total),
            "tp_trigger_px_type": tp_trigger_px_type,
            "sl_trigger_px_type": sl_trigger_px_type,
            "orders": orders,
            "confirm_phrase": confirm,
        }
    )


def _grid_short_confirm_phrase(
    exchange: str,
    symbol: str,
    assigned: Decimal,
    leverage: int,
    entries: list[tuple[float, float]],
    take_profits: list[tuple[float, float]],
    stop_loss: float,
) -> str:
    entry_text = ",".join(f"{_num(px)}@{_num(alloc)}" for px, alloc in entries)
    tp_text = ",".join(f"{_num(px)}@{_num(alloc)}" for px, alloc in take_profits)
    return (
        f"LIVE_GRID_SHORT:{exchange}:{symbol}:USDT_{_decimal_num(assigned)}:LEV_{int(leverage)}:"
        f"ENTRIES_{entry_text}:SL_{_num(stop_loss)}:TP_{tp_text}"
    )


def _static_grid_confirm_phrase(
    exchange: str,
    symbol: str,
    assigned: Decimal,
    leverage: int,
    side: str,
    order_type: str,
    entries: list[dict[str, Any]],
) -> str:
    entry_texts = []
    for entry in entries:
        tp_text = ",".join(f"{_num(tp['price'])}@{_num(tp['allocation_pct'])}" for tp in entry["take_profits"])
        entry_texts.append(
            f"{_num(entry['price'])}@{_num(entry['allocation_pct'])}:SL_{_num(entry['stop_loss'])}:TP_{tp_text}"
        )
    return (
        f"LIVE_STATIC_GRID:{exchange}:{symbol}:{side}:USDT_{_decimal_num(assigned)}:LEV_{int(leverage)}:"
        f"TYPE_{order_type}:ENTRIES_{';'.join(entry_texts)}"
    )
