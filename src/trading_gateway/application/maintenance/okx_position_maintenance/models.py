from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Any
from zoneinfo import ZoneInfo

from trading_gateway.support.redaction import redact_mapping

CST = ZoneInfo("Asia/Shanghai")
REPO_ROOT = Path(__file__).resolve().parents[6]
DEFAULT_STATE_PATH = REPO_ROOT / "docs" / "trade-journal" / "automation-session-state.md"
DEFAULT_JOURNAL_DIR = REPO_ROOT / "docs" / "trade-journal"


@dataclass(frozen=True)
class MaintenanceConfig:
    target_positions: int = 4
    refusal_limit_per_day: int = 6
    min_tactical_tp_usdt: float = 1.5
    max_tactical_tp_usdt: float = 3.0
    min_extension_tp_usdt: float = 3.0
    max_extension_tp_usdt: float = 5.0
    min_expected_value_usdt: float = 0.2
    max_spread_bps: float = 30.0
    scan_limit: int = 180
    max_candidates: int = 4
    default_since_minutes: int = 45
    automation_symbols: frozenset[str] = frozenset({"NG-USDT-SWAP", "PLTR-USDT-SWAP", "XAG-USDT-SWAP"})
    user_symbols: frozenset[str] = frozenset({"BTC-USDT-SWAP"})
    cooldown_symbols: frozenset[str] = frozenset(
        {
            "OPN-USDT-SWAP",
            "BTC-USDT-SWAP",
            "BEAT-USDT-SWAP",
            "LAB-USDT-SWAP",
            "SOL-USDT-SWAP",
            "BZ-USDT-SWAP",
            "CL-USDT-SWAP",
            "STRK-USDT-SWAP",
            "EGLD-USDT-SWAP",
            "TON-USDT-SWAP",
            "XLM-USDT-SWAP",
            "COMP-USDT-SWAP",
            "AXS-USDT-SWAP",
            "XTZ-USDT-SWAP",
            "PEPE-USDT-SWAP",
        }
    )
    headline_symbols: frozenset[str] = frozenset(
        {
            "ANTHROPIC-USDT-SWAP",
            "OPENAI-USDT-SWAP",
            "SPACE-USDT-SWAP",
            "SPCX-USDT-SWAP",
            "TRUMP-USDT-SWAP",
            "TRUTH-USDT-SWAP",
            "WLFI-USDT-SWAP",
        }
    )


@dataclass(frozen=True)
class PositionView:
    inst_id: str
    owner: str
    side: str
    size: float
    entry: float
    mark: float
    upl: float
    realized_pnl: float
    fee: float
    funding_fee: float
    margin_mode: str
    leverage: str
    liq_px: str
    margin: float
    tp: float | None
    sl: float | None
    oco_algo_id: str | None
    protected: bool = False
    protection_note: str = ""

    @property
    def abs_size(self) -> float:
        return abs(self.size)


@dataclass(frozen=True)
class CandidateView:
    inst_id: str
    side: str
    score: float
    status: str
    reason: str
    last: float
    spread_bps: float
    tp_layer: str
    gross_tp_usdt: float
    gross_sl_usdt: float
    expected_value_usdt: float


@dataclass(frozen=True)
class MaintenanceReport:
    now_cst: datetime
    journal_path: Path
    state_path: Path
    account: dict[str, Any]
    positions: list[PositionView]
    oco_orders: list[dict[str, Any]]
    conditional_count: int
    trigger_count: int
    normal_order_count: int
    fills_since: list[dict[str, Any]]
    bills_since: list[dict[str, Any]]
    orders_since: list[dict[str, Any]]
    oco_history_since: list[dict[str, Any]]
    protection_changes: list[dict[str, Any]]
    market_structure: dict[str, Any]
    candidates: list[CandidateView]
    rejected_candidates: list[CandidateView]
    current_gap: int
    audit_entry: int
    refusal_count: int
    refused_to_fill: bool
    decision: str
    notify: bool
    messages: list[str] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        return redact_mapping(
            {
                "now_cst": self.now_cst.strftime("%Y-%m-%d %H:%M:%S CST (+0800)"),
                "journal_path": str(self.journal_path),
                "state_path": str(self.state_path),
                "account": self.account,
                "positions": [p.__dict__ for p in self.positions],
                "oco_orders": self.oco_orders,
                "conditional_count": self.conditional_count,
                "trigger_count": self.trigger_count,
                "normal_order_count": self.normal_order_count,
                "fills_since": self.fills_since,
                "bills_since": self.bills_since,
                "orders_since": self.orders_since,
                "oco_history_since": self.oco_history_since,
                "protection_changes": self.protection_changes,
                "market_structure": self.market_structure,
                "candidates": [c.__dict__ for c in self.candidates],
                "rejected_candidates": [c.__dict__ for c in self.rejected_candidates],
                "current_gap": self.current_gap,
                "audit_entry": self.audit_entry,
                "refusal_count": self.refusal_count,
                "refused_to_fill": self.refused_to_fill,
                "decision": self.decision,
                "notify": self.notify,
                "messages": self.messages,
            }
        )
