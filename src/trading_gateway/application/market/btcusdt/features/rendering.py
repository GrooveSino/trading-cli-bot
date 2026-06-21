from __future__ import annotations

import json
from typing import Any

from trading_gateway.application.market.btcusdt.shared import fmt_money, fmt_number


FEATURE_LABELS = {
    "oi_acceleration": "OI 加速度",
    "cvd_log_skew": "CVD log skew",
    "order_flow_imbalance_3m": "3m OFI",
    "whale_flow": "巨鲸流偏斜",
    "weighted_orderbook_gravity": "盘口引力",
    "liquidity_vacuum_down": "下方成交密集度缺口",
    "basis_zscore": "Basis Z-score",
    "vpp_anomaly_zscore": "VPP anomaly",
    "squeeze_coefficient": "挤压系数",
    "realized_liquidation_24h": "24h 已发生强平",
}


def feature_matrix_rows(vector: dict[str, Any]) -> list[tuple[str, str, str, str]]:
    rows = [("LLM状态", "market_state", str(vector.get("market_state", "-")), f"confidence={fmt_number(vector.get('confidence'), 2)}")]
    features = vector.get("features") or {}
    for key in FEATURE_LABELS:
        payload = features.get(key) or {}
        rows.append(("LLM特征", FEATURE_LABELS[key], _feature_value(key, payload.get("value")), f"{payload.get('tier') or payload.get('status')}; {payload.get('evidence', '-')}"))
    flags = vector.get("semantic_flags") or []
    if not flags:
        rows.append(("LLM语义", "semantic_flags", "无高置信异常", "当前阈值下未触发"))
    for flag in flags[:8]:
        rows.append(("LLM语义", flag.get("code", "-"), flag.get("severity", "-"), f"{flag.get('evidence', '-')}; quality={flag.get('data_quality', '-')}"))
    return rows


def compact_feature_json(vector: dict[str, Any]) -> str:
    compact = {
        "schema_version": vector.get("schema_version"),
        "market_state": vector.get("market_state"),
        "confidence": vector.get("confidence"),
        "features": {key: {"value": value.get("value"), "tier": value.get("tier"), "status": value.get("status")} for key, value in (vector.get("features") or {}).items()},
        "semantic_flags": vector.get("semantic_flags") or [],
        "data_quality": vector.get("data_quality") or {},
    }
    return json.dumps({"llm_feature_vectors": compact}, ensure_ascii=False, sort_keys=True, indent=2)


def _feature_value(key: str, value: Any) -> str:
    if value is None:
        return "N/A"
    if key in {"whale_flow", "realized_liquidation_24h"}:
        return fmt_money(value)
    if key in {"cvd_log_skew", "order_flow_imbalance_3m", "weighted_orderbook_gravity", "liquidity_vacuum_down", "basis_zscore", "vpp_anomaly_zscore", "squeeze_coefficient", "oi_acceleration"}:
        return fmt_number(value, 4)
    return str(value)
