from __future__ import annotations

from typing import Any

from trading_gateway.app.config import get_gateway_config

from .common import effectively_done, first_step, fmt_float, is_effectively_zero, text


def plan_preview_text(plan: dict[str, Any]) -> str:
    preview = plan.get("execution_preview") or {}
    asset = preview_asset(plan)
    current = text(preview.get("current_quantity"))
    target = text(preview.get("target_quantity"))
    remaining = text(preview.get("remaining_quantity"))
    if str(preview.get("planned_delta_quantity")).upper() == "ALL":
        return f"{asset} {current} -> {target} (all available / all remaining)"
    return f"{asset} {current} -> {target} (remaining {remaining})"


def order_text(plan: dict[str, Any]) -> str:
    order = plan.get("order") or {}
    params = order.get("params") or {}
    bits = [f"{order.get('side', '-')}", f"{order.get('type', '-')}"]
    bits.append(f"amount={display_order_amount(plan)}")
    if order.get("price") is not None:
        bits.append(f"price={text(order.get('price'))}")
    if params.get("priceMatch"):
        bits.append(f"priceMatch={params['priceMatch']}")
    if params.get("tdMode"):
        bits.append(f"tdMode={params['tdMode']}")
    if plan.get("take_profit") is not None:
        bits.append(f"TP={text(plan.get('take_profit'))}")
    if plan.get("stop_loss") is not None:
        bits.append(f"SL={text(plan.get('stop_loss'))}")
    if params.get("reduceOnly"):
        bits.append("reduceOnly=true")
    return " ".join(bits)


def reason_text(payload: dict[str, Any]) -> str | None:
    status = str(payload.get("final_status") or "")
    if status == "asset_target_not_reached":
        target = payload.get("target") or {}
        rescue_reason = target.get("rescue_reason")
        if rescue_reason:
            return f"{text(rescue_reason)}; remaining={text(target.get('remaining_quantity'))}"
        runtime_reason = target.get("runtime_reason")
        if runtime_reason:
            return f"{text(runtime_reason)}; remaining={text(target.get('remaining_quantity'))}"
        rescue_guard = first_step(payload, "spot_rescue_guard") or {}
        if rescue_guard.get("status") == "blocked" and rescue_guard.get("reason"):
            return f"{text(rescue_guard.get('reason'))}; remaining={text(target.get('remaining_quantity'))}"
        return f"order attempts finished but balance stayed above tolerance; remaining={text(target.get('remaining_quantity'))}"
    if status == "target_not_reached":
        target = payload.get("target") or {}
        remaining_quote = target.get("remaining_quote_usdt")
        if remaining_quote is not None:
            return f"order attempts finished but position stayed above tolerance; remaining ~= {text(remaining_quote)} USDT"
        return "order attempts finished but target position was not reached"
    if status == "close_all_pending":
        target = payload.get("target") or {}
        return f"position still open and {target.get('owned_open_order_count', 0)} reduce-only close orders are still live"
    if status == "force_close_failed":
        error = (payload.get("target") or {}).get("last_force_close_error")
        return text(error) if error else "maker cleanup exhausted and force close failed"
    if status == "blocked":
        target = payload.get("target") or {}
        runtime_reason = target.get("runtime_reason")
        if runtime_reason:
            return text(runtime_reason)
        blocked = (payload.get("plan") or {}).get("blocked_reason")
        return text(blocked) if blocked else None
    return None


def fee_rate_text(plan: dict[str, Any]) -> str:
    market = str(plan.get("market") or "-")
    liquidity = "taker" if str((plan.get("order") or {}).get("type") or "").lower() == "market" else "maker"
    try:
        return f"{get_gateway_config().fee_bps(market, liquidity, plan.get('exchange')):.8g} bps {liquidity}"
    except Exception:  # noqa: BLE001
        return "-"


def intent_text(plan: dict[str, Any]) -> str:
    symbol = plan.get("canonical_symbol") or plan.get("symbol")
    if plan.get("requested_quote_usdt") is not None:
        return f"{plan.get('action')} {symbol} with ~{plan.get('requested_quote_usdt')} USDT"
    if str(plan.get("quantity")).upper() == "ALL":
        return f"{plan.get('action')} all available {plan.get('base_asset')}"
    return f"{plan.get('action')} {symbol} quantity={text(plan.get('quantity'))}"


def minimum_text(plan: dict[str, Any]) -> str:
    return f"min qty {text(plan.get('min_executable_quantity'))} | min quote {text(plan.get('min_executable_quote_usdt'))} USDT"


def next_command(plan: dict[str, Any]) -> str:
    parts = ["run", str(plan.get("exchange") or ""), str(plan.get("market") or ""), str(plan.get("action") or ""), str(plan.get("canonical_symbol") or plan.get("symbol") or "")]
    if plan.get("requested_quote_usdt") is not None:
        parts.append(str(plan.get("requested_quote_usdt")))
    if plan.get("bbo"):
        parts.append("--bbo")
    if plan.get("limit_price") is not None:
        parts.extend(["--limit-price", str(plan.get("limit_price"))])
    if plan.get("take_profit") is not None:
        parts.extend(["--take-profit", str(plan.get("take_profit"))])
    if plan.get("stop_loss") is not None:
        parts.extend(["--stop-loss", str(plan.get("stop_loss"))])
    if plan.get("margin_mode") is not None:
        parts.extend(["--margin-mode", str(plan.get("margin_mode"))])
    if plan.get("requested_leverage") is not None:
        parts.extend(["--leverage", str(plan.get("requested_leverage"))])
    if plan.get("account_mode") == "sim":
        parts.extend(["--account-mode", "sim"])
    parts.append(f'--confirm "{plan.get("confirm_phrase")}"')
    return " ".join(bit for bit in parts if bit)


def position_mode_text(payload: dict[str, Any]) -> str:
    step = first_step(payload, "position_mode")
    return "-" if not step else f"{step.get('mode', '-')} hedge={step.get('hedge', '-')}"


def leverage_text(payload: dict[str, Any]) -> str:
    step = first_step(payload, "perp_leverage")
    if step:
        return f"{step.get('target_leverage', '-')}x status={step.get('status', '-')}"
    value = (payload.get("plan") or {}).get("target_leverage") or (payload.get("plan") or {}).get("perp_target_leverage")
    return "-" if value is None else f"{value}x"


def target_text(payload: dict[str, Any]) -> str:
    target = payload.get("target") or first_step(payload, "close_all_verify") or first_step(payload, "position_verify") or first_step(payload, "position_before") or {}
    if not target:
        return "-"
    asset = str(target.get("asset") or (payload.get("plan") or {}).get("base_asset") or "").strip()
    current = text(target.get("current_quantity"))
    desired = text(target.get("target_quantity"))
    remaining = text(target.get("remaining_quantity"))
    remaining_quote = target.get("remaining_quote_usdt")
    prefix = f"{asset} " if asset else ""
    if effectively_done(target):
        if remaining_quote is not None:
            return f"{prefix}{current} -> {desired} (done; dust <= {text(remaining_quote)} USDT)"
        return f"{prefix}{current} -> {desired} (done)"
    if remaining_quote is not None:
        return f"{prefix}{current} -> {desired} (remaining {remaining} ~= {text(remaining_quote)} USDT)"
    return f"{prefix}{current} -> {desired} (remaining {remaining})"


def verified_text(payload: dict[str, Any]) -> str:
    if (payload.get("plan") or {}).get("market") == "spot":
        return verified_balance_text(payload)
    return verified_position_text(payload)


def verified_position_text(payload: dict[str, Any]) -> str:
    positions = ((first_step(payload, "position_verify") or {}).get("positions") or []) or ((first_step(payload, "close_all_verify") or {}).get("positions") or [])
    if not positions:
        if payload.get("final_status") == "target_reached":
            return "position is flat"
        return "-"
    row = positions[0]
    return f"{row.get('side', '-')} contracts={text(row.get('contracts'))} entry={text(row.get('entryPrice'))} mark={text(row.get('markPrice'))} upl={text(row.get('unrealizedPnl'))}"


def verified_balance_text(payload: dict[str, Any]) -> str:
    step = first_step(payload, "balance_verify") or first_step(payload, "spot_rescue_verify") or {}
    balance = step.get("balance") or {}
    if not balance:
        return "-"
    total = balance.get("total")
    asset = balance.get("asset", "-")
    tolerance = step.get("tolerance_quantity")
    if is_effectively_zero(total, tolerance):
        return f"{asset} balance is now 0"
    return f"{balance.get('asset', '-')} free={text(balance.get('free'))} used={text(balance.get('used'))} total={text(balance.get('total'))}"


def preview_asset(plan: dict[str, Any]) -> str:
    preview = plan.get("execution_preview") or {}
    if preview.get("kind") == "spot_target_preview":
        return str(preview.get("asset") or plan.get("base_asset") or "asset")
    return f"{preview.get('position_side') or 'position'}"


def display_order_amount(plan: dict[str, Any]) -> str:
    amount = (plan.get("order") or {}).get("amount")
    if amount is not None:
        return text(amount)
    if str(plan.get("quantity")).upper() == "ALL":
        return "ALL (resolved at run time)"
    return "-"


def spot_rescue_policy_text(plan: dict[str, Any]) -> str | None:
    if plan.get("market") != "spot" or plan.get("action") != "sell" or str(plan.get("quantity")).upper() != "ALL":
        return None
    config = get_gateway_config().spot_execution
    mode = str(config.sell_all_rescue_mode or "disabled").strip().lower()
    if mode == "disabled":
        return None
    if mode == "bbo_counterparty_1":
        return (
            "maker exhaustion -> bbo_counterparty_1 "
            f"if remaining <= {fmt_float(config.sell_all_rescue_max_quote_usdt)} USDT "
            f"and slippage <= {fmt_float(config.sell_all_rescue_max_slippage_bps)} bps"
        )
    return f"maker exhaustion -> {mode}"
