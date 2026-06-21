from __future__ import annotations

from typing import Any

from rich.console import Console
from rich.panel import Panel

from .common import base_table, fee_text, first_step, first_step_value, progress_prefix, text
from .single_leg_helpers import fee_rate_text, intent_text, leverage_text, minimum_text, next_command, order_text, plan_preview_text, position_mode_text, reason_text, spot_rescue_policy_text, target_text, verified_text


def print_plan_brief(plan: dict[str, Any], query_ms: int) -> None:
    status = "CAN EXECUTE" if plan.get("can_execute") else "BLOCKED"
    style = "bold green" if plan.get("can_execute") else "bold red"
    exchange = str(plan.get("exchange") or "exchange").title()
    title = f"{exchange} Trading Plan: {status}  query_ms={query_ms}  {plan.get('market')} {plan.get('action')} {plan.get('canonical_symbol') or plan.get('symbol')}"
    table = base_table()
    table.add_row("intent", intent_text(plan))
    if plan.get("native_symbol"):
        table.add_row("symbol", f"{plan.get('canonical_symbol')} (native {plan.get('native_symbol')})")
    table.add_row("target", plan_preview_text(plan))
    if plan.get("quantity_unit") == "contracts":
        table.add_row("contracts", f"{text(plan.get('order_amount'))} contracts  contract_size={text(plan.get('contract_size'))}")
    table.add_row("last_price", text(plan.get("last_price")))
    table.add_row("order", order_text(plan))
    table.add_row("fee_rate", fee_rate_text(plan))
    table.add_row("account_state", planning_source_text(plan.get("planning_data_sources") or {}))
    rescue_policy = spot_rescue_policy_text(plan)
    if rescue_policy:
        table.add_row("rescue", rescue_policy)
    if plan.get("requested_quote_usdt") is not None:
        table.add_row("requested_quote_usdt", text(plan.get("requested_quote_usdt")))
    table.add_row("minimum", minimum_text(plan))
    if plan.get("market") == "perp":
        table.add_row("target_leverage", text(plan.get("target_leverage")))
    if plan.get("warnings"):
        table.add_row("reason", "\n".join(f"{row.get('code', 'warning')}: {row.get('message', row)}" for row in plan.get("warnings") or []))
    elif plan.get("can_execute"):
        table.add_row("confirm", text(plan.get("confirm_phrase")))
        table.add_row("next", next_command(plan))
    Console(stderr=True, width=120).print(Panel(table, title=title, style=style))


def print_run_brief(payload: dict[str, Any]) -> None:
    plan = payload.get("plan") or {}
    status = str(payload.get("final_status") or "unknown")
    style = "bold green" if status in {"target_reached", "asset_target_reached", "resting_limit_submitted"} else "bold red"
    exchange = str(plan.get("exchange") or "exchange").title()
    title = f"{exchange} Trading Run: {status.upper()}  query_ms={payload.get('query_ms')}  {plan.get('market')} {plan.get('action')} {plan.get('canonical_symbol') or plan.get('symbol')}"
    table = base_table()
    table.add_row("intent", intent_text(plan))
    if plan.get("native_symbol"):
        table.add_row("symbol", f"{plan.get('canonical_symbol')} (native {plan.get('native_symbol')})")
    if plan.get("quantity_unit") == "contracts":
        table.add_row("contracts", f"{text(plan.get('order_amount'))} contracts  contract_size={text(plan.get('contract_size'))}")
    table.add_row("last_price", text(plan.get("last_price")))
    table.add_row("order", order_text(plan))
    rescue_policy = spot_rescue_policy_text(plan)
    if rescue_policy:
        table.add_row("rescue", rescue_policy)
    if plan.get("market") == "perp":
        table.add_row("position_mode", position_mode_text(payload))
        table.add_row("target_leverage", leverage_text(payload))
    table.add_row("target", target_text(payload))
    reason = reason_text(payload)
    if reason:
        table.add_row("reason", reason)
    if status == "close_all_pending":
        table.add_row("open_close_orders", text((payload.get("target") or {}).get("owned_open_order_count")))
    if (payload.get("target") or {}).get("next_action_hint"):
        table.add_row("next_action", text((payload.get("target") or {}).get("next_action_hint")))
    table.add_row("order_id", first_step_value(payload, "submit", "order_id") if first_step(payload, "submit") else first_step_value(payload, "close_all_wave_submit", "order_id"))
    table.add_row("fees", fee_text(payload))
    table.add_row("verified", verified_text(payload))
    table.add_row("timeline", " -> ".join(f"{row.get('name')}:{row.get('status')}" for row in payload.get("steps") or []))
    Console(stderr=True, width=120).print(Panel(table, title=title, style=style))


def print_execution_progress(step: dict[str, Any]) -> None:
    if step.get("name") in {"plan", "position_mode"}:
        return
    phase = str(step.get("phase") or "after")
    prefix = progress_prefix(step)
    keys = ("leg", "attempt", "order_id", "amount", "price", "asset", "target_leverage", "current_quantity", "free_quantity", "target_quantity", "remaining_quantity")
    detail = " ".join(f"{key}={step[key]}" for key in keys if key in step)
    if phase == "before":
        Console(stderr=True).print(f"[cyan]step[/cyan] {prefix}{step.get('name')}:start {detail}".rstrip())
        return
    Console(stderr=True).print(f"[cyan]step[/cyan] {prefix}{step.get('name')}:{step.get('status')} {detail}".rstrip())


def planning_source_text(payload: dict[str, Any]) -> str:
    source = str(payload.get("account_state_source") or "-")
    age = payload.get("account_state_age_sec")
    fallback = payload.get("fallback_reason")
    text_value = source
    if age is not None:
        text_value = f"{text_value} age={age:.2f}s"
    if fallback:
        text_value = f"{text_value} fallback={fallback}"
    return text_value
