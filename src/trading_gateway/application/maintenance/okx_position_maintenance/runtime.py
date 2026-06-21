from __future__ import annotations

from datetime import datetime
from pathlib import Path
from typing import Any

from .accounting import (
    _build_positions,
    _detect_protection_changes,
    _fetch_account,
    _fetch_oco_history_since,
    _filter_since,
    _infer_since_ms,
    _next_audit,
    _oco_summary,
    _safe_data,
    _timed_summary,
)
from .models import CST, CandidateView, MaintenanceConfig, MaintenanceReport, PositionView
from .scoring import scan_candidates, _market_structure
from .utils import _read_text


def run_okx_maintenance(
    client: Any,
    *,
    state_path: Path,
    journal_dir: Path,
    config: MaintenanceConfig,
    since_ms: int | None = None,
    scan: bool = True,
) -> MaintenanceReport:
    now = datetime.now(CST)
    journal_path = journal_dir / f"{now:%Y-%m-%d}-okx-trading-journal.md"
    prior_state = _read_text(state_path)
    since_ms = since_ms or _infer_since_ms(prior_state, now, config.default_since_minutes)
    account = _fetch_account(client)
    raw_positions = _safe_data(client.privateGetAccountPositions({"instType": "SWAP"}))
    normal_orders = _safe_data(client.privateGetTradeOrdersPending({"instType": "SWAP"}))
    oco_orders = _safe_data(client.privateGetTradeOrdersAlgoPending({"ordType": "oco"}))
    conditional = _safe_data(client.privateGetTradeOrdersAlgoPending({"ordType": "conditional"}))
    trigger = _safe_data(client.privateGetTradeOrdersAlgoPending({"ordType": "trigger"}))
    fills_since = _filter_since(_safe_data(client.privateGetTradeFills({"instType": "SWAP", "limit": "100"})), "fillTime", since_ms)
    bills_since = _filter_since(_safe_data(client.privateGetAccountBills({"instType": "SWAP", "limit": "100"})), "ts", since_ms)
    orders_since = _filter_since(
        _safe_data(client.privateGetTradeOrdersHistory({"instType": "SWAP", "limit": "100"})), "uTime", since_ms
    )
    oco_history_since = _fetch_oco_history_since(client, since_ms)
    positions = _build_positions(raw_positions, oco_orders, config)
    live_symbols = [position.inst_id for position in positions]
    market_structure = _market_structure(client, live_symbols)
    protected_count = sum(1 for position in positions if position.protected)
    gap = max(0, config.target_positions - protected_count)
    protection_changes = _detect_protection_changes(prior_state, positions)
    candidates: list[CandidateView] = []
    rejected: list[CandidateView] = []
    if scan and gap > 0:
        candidates, rejected = scan_candidates(client, positions, config)
    audit_entry, refusal_count = _next_audit(prior_state, now, gap, bool(candidates))
    refused = gap > 0 and len(candidates) < gap
    decision, notify, messages = _decision(
        positions=positions,
        gap=gap,
        candidates=candidates,
        rejected=rejected,
        fills=fills_since,
        bills=bills_since,
        oco_history=oco_history_since,
        protection_changes=protection_changes,
        normal_order_count=len(normal_orders),
        conditional_count=len(conditional),
        trigger_count=len(trigger),
        refusal_count=refusal_count,
        refusal_limit=config.refusal_limit_per_day,
    )
    return MaintenanceReport(
        now_cst=now,
        journal_path=journal_path,
        state_path=state_path,
        account=account,
        positions=positions,
        oco_orders=[_oco_summary(row) for row in oco_orders],
        conditional_count=len(conditional),
        trigger_count=len(trigger),
        normal_order_count=len(normal_orders),
        fills_since=[_timed_summary(row, "fillTime") for row in fills_since],
        bills_since=[_timed_summary(row, "ts") for row in bills_since],
        orders_since=[_timed_summary(row, "uTime") for row in orders_since],
        oco_history_since=oco_history_since,
        protection_changes=protection_changes,
        market_structure=market_structure,
        candidates=candidates[: config.max_candidates],
        rejected_candidates=rejected[: max(config.max_candidates, 12)],
        current_gap=gap,
        audit_entry=audit_entry,
        refusal_count=refusal_count,
        refused_to_fill=refused,
        decision=decision,
        notify=notify,
        messages=messages,
    )


def _decision(
    *,
    positions: list[PositionView],
    gap: int,
    candidates: list[CandidateView],
    rejected: list[CandidateView],
    fills: list[dict[str, Any]],
    bills: list[dict[str, Any]],
    oco_history: list[dict[str, Any]],
    protection_changes: list[dict[str, Any]],
    normal_order_count: int,
    conditional_count: int,
    trigger_count: int,
    refusal_count: int,
    refusal_limit: int,
) -> tuple[str, bool, list[str]]:
    messages: list[str] = []
    notify = False
    unprotected = [position.inst_id for position in positions if not position.protected]
    if unprotected:
        messages.append(f"Protection anomaly: {', '.join(unprotected)} lack verified live reduce-only OCO.")
        notify = True
    if fills:
        messages.append(f"Detected {len(fills)} new fill row(s); process ownership, fees, and cooldown.")
        notify = True
    if oco_history:
        messages.append(f"Detected {len(oco_history)} OCO history row(s); check TP/SL/effective/canceled result.")
        notify = True
    if protection_changes:
        changed = ", ".join(change["instId"] for change in protection_changes)
        messages.append(f"Live OCO TP/SL changed versus previous handoff: {changed}. Verify source and do not restore stale brackets.")
        notify = True
    if normal_order_count or conditional_count or trigger_count:
        messages.append(
            f"Pending non-OCO exposure exists: normal={normal_order_count}, conditional={conditional_count}, trigger={trigger_count}."
        )
        notify = True
    if gap > 0:
        if candidates:
            messages.append(f"Position target gap {gap}; {len(candidates)} candidate(s) passed soft scoring. Review before live entry.")
            notify = True
        else:
            messages.append(f"Position target gap {gap}; no candidate passed hard/soft gates. Rejections recorded: {len(rejected)}.")
            notify = refusal_count > refusal_limit
    else:
        messages.append("Protected position target is met or exceeded; no additive scan/trade required.")
    external = [position.inst_id for position in positions if position.owner != "automation"]
    if external:
        messages.append(f"External/user-managed exposure counted for portfolio risk: {', '.join(external)}.")
    if refusal_count > refusal_limit:
        messages.append(f"Daily refusal audit threshold exceeded: {refusal_count}>{refusal_limit}.")
        notify = True
    if unprotected:
        decision = "PROTECTION_REPAIR_REQUIRED"
    elif gap > 0 and candidates:
        decision = "REVIEW_CANDIDATES_DRY_RUN"
    elif gap > 0:
        decision = "WAIT_HARD_GATES"
    else:
        decision = "HOLD_PROTECTED_POSITIONS"
    return decision, notify, messages
