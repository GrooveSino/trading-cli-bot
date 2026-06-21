from __future__ import annotations

import json
from decimal import Decimal
from pathlib import Path
from typing import Any

from trading_gateway.domain.models import normalize_exchange
from trading_gateway.support.redaction import redact_mapping

from ..common import _decimal_num, _okx_swap_inst_id
from .compile_order import _compile_order, _confirm_phrase, _reject_unknown

TOP_KEYS = {"version", "exchange", "instrument", "margin_mode", "leverage", "execution", "orders"}
EXECUTION_KEYS = {"mode", "replace_existing"}
ORDER_KEYS = {"id", "side", "entry", "notional_usdt", "stop_loss", "take_profits"}
ENTRY_KEYS = {"type", "trigger_price", "order_price", "price", "trigger_price_type", "post_only"}
STOP_KEYS = {"trigger_price", "order_price", "trigger_price_type"}
TP_KEYS = {"price", "size_pct"}


def load_okx_json_plan(source: str) -> dict[str, Any]:
    text = source
    if source != "-" and not str(source).lstrip().startswith(("{", "[")):
        text = Path(source).read_text(encoding="utf-8")
    try:
        payload = json.loads(text)
    except json.JSONDecodeError as exc:
        raise ValueError(f"invalid JSON plan: {exc}") from exc
    if not isinstance(payload, dict):
        raise ValueError("JSON plan must be an object")
    return payload


def build_okx_json_plan(
    raw_plan: dict[str, Any],
    *,
    contract_btc: str | float = "0.01",
    lot_size: str | float = "0.01",
    min_size: str | float | None = None,
    tick_size: str | float | None = None,
    position_mode: str | None = None,
) -> dict[str, Any]:
    _reject_unknown(raw_plan, TOP_KEYS, "plan")
    if int(raw_plan.get("version", 0)) != 1:
        raise ValueError("plan.version must be 1")
    exchange = normalize_exchange(str(raw_plan.get("exchange") or ""))
    if exchange != "okx":
        raise ValueError("JSON order plan v1 supports only okx")
    inst_id = _okx_swap_inst_id(str(raw_plan.get("instrument") or ""))
    margin_mode = str(raw_plan.get("margin_mode") or "isolated").strip().lower()
    if margin_mode not in {"isolated", "cross"}:
        raise ValueError("margin_mode must be isolated or cross")
    leverage = int(raw_plan.get("leverage") or 20)
    if leverage <= 0:
        raise ValueError("leverage must be positive")
    execution = raw_plan.get("execution") or {}
    if not isinstance(execution, dict):
        raise ValueError("execution must be an object")
    _reject_unknown(execution, EXECUTION_KEYS, "execution")
    execution_mode = str(execution.get("mode") or "one_shot").strip().lower()
    if execution_mode != "one_shot":
        raise ValueError("execution.mode must be one_shot")

    contract = Decimal(str(contract_btc))
    lot = Decimal(str(lot_size))
    minimum = Decimal(str(min_size if min_size is not None else lot_size))
    if contract <= 0 or lot <= 0 or minimum <= 0:
        raise ValueError("instrument specs must be positive")
    pos_mode = str(position_mode or "").strip()

    compiled_orders: list[dict[str, Any]] = []
    target_total = Decimal("0")
    actual_total = Decimal("0")
    source_orders = raw_plan.get("orders")
    if not isinstance(source_orders, list) or not source_orders:
        raise ValueError("orders must be a non-empty array")
    for index, order in enumerate(source_orders, start=1):
        for compiled in _compile_order(
            order,
            index=index,
            inst_id=inst_id,
            margin_mode=margin_mode,
            leverage=leverage,
            contract=contract,
            lot=lot,
            minimum=minimum,
            position_mode=pos_mode,
        ):
            compiled_orders.append(compiled)
            target_total += Decimal(str(compiled["target_notional_usdt"]))
            actual_total += Decimal(str(compiled["actual_notional_usdt"]))

    confirm = _confirm_phrase(exchange, inst_id, leverage, margin_mode, compiled_orders)
    return redact_mapping(
        {
            "mode": "okx_json_order_plan",
            "version": 1,
            "exchange": exchange,
            "symbol": inst_id,
            "margin_mode": margin_mode,
            "leverage": str(leverage),
            "execution": {"mode": "one_shot", "replace_existing": bool(execution.get("replace_existing", False))},
            "mutual_exclusion_enforced": False,
            "instrument_specs": {
                "contract_value": _decimal_num(contract),
                "lot_size": _decimal_num(lot),
                "min_size": _decimal_num(minimum),
                "tick_size": "" if tick_size is None else str(tick_size),
                "position_mode": pos_mode,
            },
            "target_notional_usdt": _decimal_num(target_total),
            "actual_notional_usdt": _decimal_num(actual_total),
            "notional_shortfall_usdt": _decimal_num(target_total - actual_total),
            "orders": compiled_orders,
            "confirm_phrase": confirm,
        }
    )

