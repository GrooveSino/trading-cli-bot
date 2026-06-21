from __future__ import annotations

from typing import Any


def render_sim_lifecycle(payload: dict[str, Any], *, executed: bool) -> str:
    title = "OKX Sim lifecycle test" if executed else "OKX Sim lifecycle dry-run"
    lines = [title, ""]
    lines.append(f"status: {payload.get('status', '-')}")
    lines.append(f"symbol: {str(payload.get('symbol', '-')).upper()}")
    lines.append(f"market: {payload.get('market', '-')}")
    if payload.get("quote_usdt") is not None:
        lines.append(f"usd: {payload.get('quote_usdt')}")
    if payload.get("side") is not None:
        lines.append(f"side: {payload.get('side')}")
    lines.extend(["", "steps:"])
    for index, step in enumerate(payload.get("steps") or [], start=1):
        lines.append(f"{index}. {_step_line(step)}")
    if not executed:
        lines.extend(["", "dry-run only. Run again with --yes to execute on OKX Sim."])
    if payload.get("error"):
        lines.extend(["", f"error: {payload['error']}"])
    if _looks_like_account_mode_error(payload):
        lines.append(f"hint: {payload.get('hint') or 'OKX Sim is likely in Spot mode. Switch account mode before using --swap.'}")
    if payload.get("base_balance_after"):
        lines.extend(["", f"base_balance_after: {payload['base_balance_after']}"])
    return "\n".join(lines) + "\n"


def _step_line(step: Any) -> str:
    if isinstance(step, str):
        return step
    if not isinstance(step, dict):
        return str(step)
    order = step.get("order") or {}
    order_id = order.get("id") or order.get("ordId") or (order.get("info") or {}).get("ordId")
    suffix = f" order_id={order_id}" if order_id else ""
    amount = f" amount={step.get('amount')}" if step.get("amount") is not None else ""
    return f"{step.get('name', 'step')} status={step.get('status', '-')}{suffix}{amount}"


def _looks_like_account_mode_error(payload: dict[str, Any]) -> bool:
    text = str(payload.get("error") or "")
    return "51010" in text or "account mode" in text.lower()
