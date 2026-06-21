from __future__ import annotations

from .models import MaintenanceConfig, MaintenanceReport
from .utils import _fmt


def render_journal_entry(report: MaintenanceReport) -> str:
    pos_rows = "\n".join(
        "| {inst} | {owner} | {side} | {size:g} | {entry:g} | {mark:g} | {upl:+.4f} | {tp} | {sl} | `{algo}` | {prot} |".format(
            inst=p.inst_id,
            owner=p.owner,
            side=p.side,
            size=p.size,
            entry=p.entry,
            mark=p.mark,
            upl=p.upl,
            tp=_fmt(p.tp),
            sl=_fmt(p.sl),
            algo=p.oco_algo_id or "-",
            prot="live OCO" if p.protected else p.protection_note or "missing",
        )
        for p in report.positions
    )
    candidate_lines = "\n".join(
        f"- {c.inst_id} {c.side}: score {c.score:.1f}, {c.tp_layer}, gross TP {c.gross_tp_usdt:.2f}U, "
        f"gross SL {c.gross_sl_usdt:.2f}U, EV {c.expected_value_usdt:.2f}U; {c.reason}"
        for c in report.candidates
    ) or "- Not scanned or no accepted candidate."
    reject_lines = "\n".join(
        f"- {c.inst_id} {c.side}: {c.status}; {c.reason}"
        for c in report.rejected_candidates[:8]
    ) or "- None recorded."
    messages = "\n".join(f"- {message}" for message in report.messages) or "- No special action required."
    protection_change_lines = "\n".join(
        "- {inst}: TP {old_tp} -> {new_tp}, SL {old_sl} -> {new_sl}, OCO `{old_algo}` -> `{new_algo}`.".format(
            inst=change["instId"],
            old_tp=_fmt(change.get("oldTp")),
            new_tp=_fmt(change.get("newTp")),
            old_sl=_fmt(change.get("oldSl")),
            new_sl=_fmt(change.get("newSl")),
            old_algo=change.get("oldAlgoId") or "-",
            new_algo=change.get("newAlgoId") or "-",
        )
        for change in report.protection_changes
    ) or "- No live OCO TP/SL changes detected versus previous handoff."
    return f"""
## Adaptive OKX Maintenance Audit - {report.now_cst:%Y-%m-%d %H:%M} CST

- Time: {report.now_cst:%Y-%m-%d %H:%M:%S} CST (+0800).
- Action Type: adaptive OKX maintenance audit, OCO verification, fill/bill/order-history processing, position-target audit, and optional candidate scan.
- Decision: {report.decision}.
- Notification: {"NOTIFY" if report.notify else "DONT_NOTIFY"}.

### Account Snapshot

- Total equity: {report.account.get("totalEq")} USDT.
- USDT equity / cash / available: {report.account.get("usdtEq")} / {report.account.get("cashBal")} / {report.account.get("availBal")} USDT.
- Frozen / isolated equity: {report.account.get("frozenBal")} / {report.account.get("isoEq")} USDT.
- Unrealized PnL: {report.account.get("upl")} USDT.
- Open positions: {len(report.positions)}.
- Normal open SWAP orders: {report.normal_order_count}.
- Live OCO orders checked separately with `ordType=oco`: {len(report.oco_orders)}.
- Conditional algo orders: {report.conditional_count}.
- Trigger orders: {report.trigger_count}.

| Symbol | Owner | Side | Size | Entry | Mark | UPL | TP | SL | OCO Algo ID | Protection |
|---|---|---:|---:|---:|---:|---:|---:|---:|---|---|
{pos_rows}

### Events Since Previous Handoff

- Fills: {len(report.fills_since)}.
- Bills/funding: {len(report.bills_since)}.
- Order-history rows: {len(report.orders_since)}.
- OCO effective/canceled/order-failed rows: {len(report.oco_history_since)}.
- Live OCO TP/SL changes versus previous handoff: {len(report.protection_changes)}.

### 4-Position Target Audit

- Protected live positions: {sum(1 for p in report.positions if p.protected)}.
- Target positions: 4.
- Gap: {report.current_gap}.
- Daily audit entry: #{report.audit_entry}.
- Daily hard-refusal count: {report.refusal_count}.
- Refused to fill this run: {"yes" if report.refused_to_fill else "no"}.

### Accepted Candidates

{candidate_lines}

### Rejections / Watch

{reject_lines}

### OCO TP/SL Change Audit

{protection_change_lines}

### Notes

{messages}
""".strip() + "\n"


def render_state(report: MaintenanceReport, config: MaintenanceConfig) -> str:
    live_rows = "\n".join(
        "| {inst} | {owner} | {side} | {size:g} | {entry:g} | {mark:g} | {upl:+.4f} | {tp} | {sl} | `{algo}` | {prot} |".format(
            inst=p.inst_id,
            owner=p.owner,
            side=p.side,
            size=p.size,
            entry=p.entry,
            mark=p.mark,
            upl=p.upl,
            tp=_fmt(p.tp),
            sl=_fmt(p.sl),
            algo=p.oco_algo_id or "-",
            prot="Live reduce-only OCO" if p.protected else p.protection_note or "missing",
        )
        for p in report.positions
    )
    watch = "\n".join(f"- {message}" for message in report.messages) or "- No special warnings."
    candidates = "\n".join(
        f"- {c.inst_id} {c.side}: score {c.score:.1f}; {c.reason}"
        for c in report.candidates
    ) or "- No accepted replacement candidates recorded."
    protection_changes = "\n".join(
        "- {inst}: TP {old_tp} -> {new_tp}, SL {old_sl} -> {new_sl}, OCO `{old_algo}` -> `{new_algo}`.".format(
            inst=change["instId"],
            old_tp=_fmt(change.get("oldTp")),
            new_tp=_fmt(change.get("newTp")),
            old_sl=_fmt(change.get("oldSl")),
            new_sl=_fmt(change.get("newSl")),
            old_algo=change.get("oldAlgoId") or "-",
            new_algo=change.get("newAlgoId") or "-",
        )
        for change in report.protection_changes
    ) or "- No live OCO TP/SL changes detected versus previous handoff."
    return f"""# Automation Session State

This file is the overwrite-only handoff state for the 30-minute OKX position maintenance automation.

Do not append entries here. Each automation run must replace the entire file with the latest state snapshot, decision summary, and next-cycle instructions. Use the dated trade journal for append-only trade/action records.

## Operating Rules

- Primary objective: avoid negative-expectancy trades, protect open risk, reduce correlation, and avoid fee/slippage churn.
- Opening is allowed only when structure, probability, gross/net R:R, fees, correlation, event/news risk, and execution risk all support positive expectancy.
- Position target: aim for {config.target_positions} protected, quality-qualified, low-correlation small positions. Treat the target as a mandatory workflow and audit target, not permission to override hard risk gates.
- OCO verification rule: always query OKX `ordType=oco` separately; never rely on conditional-only output.
- User/external positions must not be automatically closed, resized, added to, or have OCO replaced unless the user explicitly asks or protection disappears and emergency no-naked-position rules require action.
- 1h tactical TP layer: gross TP {config.min_tactical_tp_usdt:g}-{config.max_tactical_tp_usdt:g} USDT. High-quality extension TP layer: gross TP {config.min_extension_tp_usdt:g}-{config.max_extension_tp_usdt:g} USDT.
- Do not open 0.5-1U small-target trades. Do not widen SL to avoid losses. Do not restore stale brackets from old handoff text.

## Last Known Handoff

- Timestamp basis: Asia/Shanghai time, {report.now_cst:%Y-%m-%d %H:%M:%S} CST (+0800).
- Last run type: adaptive OKX maintenance audit.
- Latest journal entry: `{report.journal_path}`, section `Adaptive OKX Maintenance Audit - {report.now_cst:%Y-%m-%d %H:%M} CST`.
- Decision: {report.decision}.
- Notification: {"NOTIFY" if report.notify else "DONT_NOTIFY"}.
- Daily audit entry: #{report.audit_entry}.
- Current daily hard-refusal count: {report.refusal_count}.
- Refused to fill this run: {"yes" if report.refused_to_fill else "no"}.
- Total equity: {report.account.get("totalEq")} USDT.
- USDT equity / cash / available: {report.account.get("usdtEq")} / {report.account.get("cashBal")} / {report.account.get("availBal")} USDT.
- Frozen / isolated equity: {report.account.get("frozenBal")} / {report.account.get("isoEq")} USDT.
- Unrealized PnL: {report.account.get("upl")} USDT.
- Open positions: {len(report.positions)}.
- Open normal SWAP orders: {report.normal_order_count}.
- Live OCO orders checked separately with `ordType=oco`: {len(report.oco_orders)}.
- Conditional algo orders: {report.conditional_count}.
- Trigger orders: {report.trigger_count}.
- Protection status: {sum(1 for p in report.positions if p.protected)} of {len(report.positions)} positions have verified live reduce-only OCO protection.
- Recent fills since previous handoff: {len(report.fills_since)}.
- Recent bills/funding since previous handoff: {len(report.bills_since)}.
- OCO effective/canceled/order-failed history since previous handoff: {len(report.oco_history_since)}.
- Live OCO TP/SL changes versus previous handoff: {len(report.protection_changes)}.

## Current Live Positions

| Symbol | Owner | Side | Size | Entry | Mark | UPL | TP | SL | OCO Algo ID | Protection |
|---|---|---:|---:|---:|---:|---:|---:|---:|---|---|
{live_rows}

## Candidate Watch

{candidates}

## OCO TP/SL Change Audit

{protection_changes}

## Warnings And Next Focus

{watch}

## Next Run Protocol

1. Read this file first.
2. Confirm Asia/Shanghai absolute time and choose the correct dated journal file.
3. Query OKX live account state before acting: equity, available balance, frozen/isolated margin, unrealized PnL, current positions, OCO orders, conditional orders, trigger orders, open orders, recent fills, order history, bills, and OCO history with required parameters.
4. Check `oco` separately from `conditional`; never rely on conditional-only output.
5. If any live position lacks protection, repair immediately or close according to emergency rules.
6. Treat external/user positions as portfolio exposure for risk review, but do not automate-manage them unless protection disappears or the user asks.
7. If fewer than {config.target_positions} protected positions remain, run broad USDT-SWAP scan and document candidates/rejections before any entry.
"""


def append_journal(report: MaintenanceReport) -> None:
    report.journal_path.parent.mkdir(parents=True, exist_ok=True)
    if not report.journal_path.exists():
        report.journal_path.write_text(
            f"# OKX Trading Journal - {report.now_cst:%Y-%m-%d}\n\n"
            "All timestamps in this file use Asia/Shanghai time (CST, UTC+08:00).\n\n",
            encoding="utf-8",
        )
    with report.journal_path.open("a", encoding="utf-8") as handle:
        handle.write("\n")
        handle.write(render_journal_entry(report))
        handle.write("\n")


def write_state(report: MaintenanceReport, config: MaintenanceConfig) -> None:
    report.state_path.write_text(render_state(report, config), encoding="utf-8")
