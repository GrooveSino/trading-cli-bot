from __future__ import annotations

import json
import time
from concurrent.futures import ThreadPoolExecutor
from typing import Any, Callable

from trading_gateway.application.market.btcusdt.shared import fmt_money, fmt_number, fmt_price
from trading_gateway.application.market.specs import get_market_spec, get_venue_profile, normalize_market_symbol

from .llm_context import render_llm_context
from .snapshot import render_market_markdown

SnapshotLoader = Callable[[str, str], dict[str, Any]]


def normalize_bundle_symbols(values: list[str] | tuple[str, ...]) -> list[str]:
    tokens = [str(item).strip().lower() for item in values if str(item).strip()]
    if not tokens:
        return ["btc"]
    if len(tokens) == 1 and tokens[0] == "all":
        return ["btc", "eth"]
    symbols = [normalize_market_symbol(token) for token in tokens]
    return list(dict.fromkeys(symbols))


def build_market_bundle(venue: str, symbols: list[str], load: SnapshotLoader, *, started_at: float | None = None) -> dict[str, Any]:
    profile = get_venue_profile(venue)
    started = started_at or time.perf_counter()
    with ThreadPoolExecutor(max_workers=max(1, len(symbols)), thread_name_prefix="market-bundle") as pool:
        results = dict(zip(symbols, pool.map(lambda item: load(profile.id, item), symbols)))
    payload = {
        "mode": "multi_symbol_market_snapshot",
        "venue_profile": {"id": profile.id, "exchange": profile.exchange, "account_mode": profile.account_mode, "display_name": profile.display_name},
        "symbols": symbols,
        "snapshots": results,
        "cross_symbol_features": cross_symbol_features(results),
        "cli_elapsed_ms": int((time.perf_counter() - started) * 1000),
    }
    return payload


def cross_symbol_features(snapshots: dict[str, dict[str, Any]]) -> dict[str, Any]:
    metrics = {symbol: _symbol_metrics(snapshot) for symbol, snapshot in snapshots.items()}
    return {
        "leader": _leader(metrics),
        "metrics": metrics,
        "rankings": {
            "oi_5m_btc_delta": _rank(metrics, "oi_5m_btc_delta"),
            "cvd_delta_usd": _rank(metrics, "cvd_delta_usd"),
            "ofi_3m_delta_usd": _rank(metrics, "ofi_3m_delta_usd"),
            "weighted_orderbook_gravity": _rank(metrics, "weighted_orderbook_gravity"),
        },
        "risk_flags": _risk_flags(metrics),
    }


def render_bundle_llm_context(bundle: dict[str, Any]) -> str:
    lines = [_bundle_header(bundle), "", "## 组合元信息", *_bundle_meta(bundle), "", "## 跨币种对照", *_cross_lines(bundle)]
    for symbol, snapshot in bundle.get("snapshots", {}).items():
        lines.extend(["", f"## {symbol.upper()} 完整快照", render_llm_context(snapshot).rstrip()])
    lines.extend(["", "## 组合 Feature JSON", "```json", json.dumps(_compact_bundle_json(bundle), ensure_ascii=False, indent=2, sort_keys=True), "```"])
    return "\n".join(lines) + "\n"


def render_bundle_table_markdown(bundle: dict[str, Any]) -> str:
    lines = [_bundle_header(bundle), "", "跨币种对照：", *_cross_lines(bundle)]
    for symbol, snapshot in bundle.get("snapshots", {}).items():
        lines.extend(["", f"## {symbol.upper()} 表格", render_market_markdown(snapshot).rstrip()])
    lines.extend(["", "组合 Feature JSON：", "```json", json.dumps(_compact_bundle_json(bundle), ensure_ascii=False, indent=2, sort_keys=True), "```"])
    return "\n".join(lines) + "\n"


def _symbol_metrics(snapshot: dict[str, Any]) -> dict[str, Any]:
    source = snapshot.get("market_source") or {}
    deriv = snapshot.get("global_derivatives") or {}
    oi_delta = ((deriv.get("oi") or {}).get("delta") or {})
    cvd = deriv.get("cvd") or {}
    ofi = deriv.get("ofi_3m") or {}
    rsi = (((deriv.get("momentum") or {}).get("rsi") or {}).get("15m") or {})
    basis = ((deriv.get("funding_basis") or {}).get("derivatives") or (deriv.get("funding_basis") or {}).get("binance") or {})
    account = snapshot.get("account_overlay") or {}
    gravity = ((snapshot.get("llm_feature_vectors") or {}).get("features") or {}).get("weighted_orderbook_gravity") or {}
    return {
        "last": source.get("last"),
        "best_bid": source.get("best_bid"),
        "best_ask": source.get("best_ask"),
        "oi_5m_btc_delta": (oi_delta.get("5m") or {}).get("btc_delta"),
        "oi_15m_btc_delta": (oi_delta.get("15m") or {}).get("btc_delta"),
        "oi_1h_btc_delta": (oi_delta.get("1h") or {}).get("btc_delta"),
        "cvd_delta_usd": cvd.get("delta_usd"),
        "ofi_3m_delta_usd": ofi.get("delta_usd"),
        "ofi_3m_tier": ofi.get("tier"),
        "rsi_15m_live": rsi.get("rsi14_live"),
        "basis_bps": basis.get("basis_bps"),
        "weighted_orderbook_gravity": gravity.get("value"),
        "market_state": (snapshot.get("llm_feature_vectors") or {}).get("market_state"),
        "account_status": account.get("status"),
        "remote": snapshot.get("remote_snapshot") or {},
        "errors": snapshot.get("errors") or {},
    }


def _leader(metrics: dict[str, dict[str, Any]]) -> str:
    cvd = _rank(metrics, "cvd_delta_usd")
    ofi = _rank(metrics, "ofi_3m_delta_usd")
    if len(cvd) < 2 or len(ofi) < 2:
        return "mixed"
    return cvd[0]["symbol"] if cvd[0]["symbol"] == ofi[0]["symbol"] else "mixed"


def _rank(metrics: dict[str, dict[str, Any]], key: str) -> list[dict[str, Any]]:
    rows = [{"symbol": symbol, "value": row.get(key)} for symbol, row in metrics.items() if row.get(key) is not None]
    return sorted(rows, key=lambda item: abs(float(item["value"])), reverse=True)


def _risk_flags(metrics: dict[str, dict[str, Any]]) -> list[dict[str, str]]:
    flags: list[dict[str, str]] = []
    for symbol, row in metrics.items():
        if row.get("errors"):
            flags.append({"symbol": symbol, "code": "SECTION_ERRORS", "detail": ",".join(sorted(row["errors"].keys()))})
        if row.get("account_status") == "error":
            flags.append({"symbol": symbol, "code": "ACCOUNT_OVERLAY_ERROR", "detail": "private overlay degraded"})
        if (row.get("rsi_15m_live") or 0) >= 70:
            flags.append({"symbol": symbol, "code": "RSI_15M_OVERHEATED", "detail": fmt_number(row.get("rsi_15m_live"), 2)})
    return flags


def _bundle_header(bundle: dict[str, Any]) -> str:
    profile = bundle.get("venue_profile") or {}
    symbols = " + ".join(str(item).upper() for item in bundle.get("symbols") or [])
    return f"# 市场快照：{profile.get('display_name', profile.get('id', 'Market'))} {symbols}"


def _bundle_meta(bundle: dict[str, Any]) -> list[str]:
    profile = bundle.get("venue_profile") or {}
    rows = [f"- venue={profile.get('id')}; exchange={profile.get('exchange')}; account_mode={profile.get('account_mode')}", f"- symbols={','.join(bundle.get('symbols') or [])}; cli_elapsed={bundle.get('cli_elapsed_ms')}ms"]
    for symbol, snapshot in (bundle.get("snapshots") or {}).items():
        remote = snapshot.get("remote_snapshot") or {}
        rows.append(f"- {symbol}: snapshot_time={snapshot.get('snapshot_time_cst')}; remote={remote.get('status', 'local')}/{remote.get('transport', '-')}; age={fmt_number(remote.get('age_sec'), 1)}s")
    return rows


def _cross_lines(bundle: dict[str, Any]) -> list[str]:
    features = bundle.get("cross_symbol_features") or {}
    metrics = features.get("metrics") or {}
    lines = [f"- leader={features.get('leader', 'mixed')}"]
    for symbol, row in metrics.items():
        lines.append(
            f"- {symbol.upper()}: px={fmt_price(row.get('last'))}; OI5m={fmt_number(row.get('oi_5m_btc_delta'), 3)}; "
            f"CVD={fmt_money(row.get('cvd_delta_usd'))}; OFI3m={fmt_money(row.get('ofi_3m_delta_usd'))}/{row.get('ofi_3m_tier')}; "
            f"RSI15m={fmt_number(row.get('rsi_15m_live'), 2)}; basis={fmt_number(row.get('basis_bps'), 2)}bps; state={row.get('market_state')}"
        )
    for flag in features.get("risk_flags") or []:
        lines.append(f"- flag {flag.get('symbol')}: {flag.get('code')} ({flag.get('detail')})")
    return lines


def _compact_bundle_json(bundle: dict[str, Any]) -> dict[str, Any]:
    return {
        "mode": bundle.get("mode"),
        "venue_profile": bundle.get("venue_profile"),
        "symbols": bundle.get("symbols"),
        "cross_symbol_features": bundle.get("cross_symbol_features"),
        "llm_feature_vectors": {symbol: (snapshot.get("llm_feature_vectors") or {}) for symbol, snapshot in (bundle.get("snapshots") or {}).items()},
    }
