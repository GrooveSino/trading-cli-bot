from __future__ import annotations

from datetime import datetime
from pathlib import Path
from typing import Any

from .models import CST


def _safe_data(payload: dict[str, Any]) -> list[dict[str, Any]]:
    data = payload.get("data") if isinstance(payload, dict) else None
    return data if isinstance(data, list) else []


def _normalize_inst_id(value: Any) -> str:
    text = str(value or "").strip().upper()
    if "/" in text:
        base = text.split("/")[0]
        rest = text.split("/")[1].split(":")[0]
        return f"{base}-{rest}-SWAP"
    return text


def _symbol_group(inst_id: str) -> str:
    base = inst_id.split("-")[0]
    metals = {"XAG", "XAU"}
    energy = {"NG", "CL", "BZ"}
    crypto_beta = {"BTC", "ETH", "SOL", "COIN", "MSTR", "SUI", "XRP", "DOGE", "BNB"}
    ai_stock = {"PLTR", "ANTHROPIC", "OPENAI", "NVDA", "AMD", "SOXL", "MRVL", "MU", "ARM", "TSM", "QCOM"}
    if base in metals:
        return "metals"
    if base in energy:
        return "energy"
    if base in crypto_beta:
        return "crypto_beta"
    if base in ai_stock:
        return "ai_stock"
    return base.lower()


def _spread_bps(bid: float, ask: float) -> float | None:
    if bid <= 0 or ask <= 0:
        return None
    mid = (bid + ask) / 2
    return (ask - bid) / mid * 10000 if mid else None


def _read_text(path: Path) -> str:
    return path.read_text(encoding="utf-8") if path.exists() else ""


def _last_int(text: str) -> int:
    import re

    matches = re.findall(r"\d+", text)
    return int(matches[-1]) if matches else 0


def _maybe_float(value: Any) -> float | None:
    try:
        if value in (None, ""):
            return None
        return float(value)
    except (TypeError, ValueError):
        return None


def _float(value: Any) -> float:
    maybe = _maybe_float(value)
    return 0.0 if maybe is None else maybe


def _fmt(value: float | None) -> str:
    if value is None:
        return "-"
    return f"{value:g}"


def _float_changed(left: float | None, right: float | None, *, tolerance: float = 1e-9) -> bool:
    if left is None and right is None:
        return False
    if left is None or right is None:
        return True
    return abs(left - right) > tolerance


def _parse_cst_datetime(text: str) -> datetime | None:
    import re

    match = re.search(r"(20\d\d-\d\d-\d\d \d\d:\d\d(?::\d\d)?)", text)
    if not match:
        return None
    raw = match.group(1)
    fmt = "%Y-%m-%d %H:%M:%S" if raw.count(":") == 2 else "%Y-%m-%d %H:%M"
    return datetime.strptime(raw, fmt).replace(tzinfo=CST)
