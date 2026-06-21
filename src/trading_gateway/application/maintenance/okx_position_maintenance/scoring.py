from __future__ import annotations

from datetime import datetime
from statistics import mean
from typing import Any

from .models import CST, CandidateView, MaintenanceConfig, PositionView
from .utils import _float, _normalize_inst_id, _spread_bps, _symbol_group


def scan_candidates(
    client: Any,
    positions: list[PositionView],
    config: MaintenanceConfig,
) -> tuple[list[CandidateView], list[CandidateView]]:
    markets = client.load_markets()
    tickers = client.fetch_tickers()
    live = {position.inst_id for position in positions}
    live_groups = {_symbol_group(symbol) for symbol in live}
    rows: list[tuple[str, dict[str, Any]]] = []
    for inst_id, ticker in tickers.items():
        normalized = _normalize_inst_id(inst_id)
        market = markets.get(inst_id) or markets.get(normalized) or {}
        if normalized in live or not normalized.endswith("-USDT-SWAP"):
            continue
        if market and not market.get("active", True):
            continue
        last = _float(ticker.get("last") or ticker.get("close"))
        bid = _float(ticker.get("bid"))
        ask = _float(ticker.get("ask"))
        if last <= 0:
            continue
        spread = _spread_bps(bid, ask)
        if spread is None:
            continue
        rows.append((normalized, {"last": last, "bid": bid, "ask": ask, "spread_bps": spread, "ticker": ticker}))
    rows.sort(key=lambda item: abs(_float(item[1]["ticker"].get("percentage"))), reverse=True)
    accepted: list[CandidateView] = []
    rejected: list[CandidateView] = []
    for inst_id, row in rows[: config.scan_limit]:
        candidate = _score_symbol(client, inst_id, row, live_groups, config)
        if candidate.status == "accepted":
            accepted.append(candidate)
        else:
            rejected.append(candidate)
    accepted.sort(key=lambda item: item.score, reverse=True)
    rejected.sort(key=lambda item: item.score, reverse=True)
    return accepted[: config.max_candidates], rejected


def _score_symbol(
    client: Any,
    inst_id: str,
    row: dict[str, Any],
    live_groups: set[str],
    config: MaintenanceConfig,
) -> CandidateView:
    last = _float(row["last"])
    spread = _float(row["spread_bps"])
    group = _symbol_group(inst_id)
    if inst_id in config.cooldown_symbols:
        return _candidate(inst_id, "watch", 0, "cooldown", "fresh cooldown/no immediate re-entry", last, spread)
    if group in live_groups:
        return _candidate(inst_id, "watch", 0, "correlated", f"same thesis group already live: {group}", last, spread)
    if spread > config.max_spread_bps:
        return _candidate(inst_id, "watch", 0, "illiquid", f"spread {spread:.1f} bps exceeds limit", last, spread)
    if inst_id in config.headline_symbols:
        return _candidate(inst_id, "watch", 0, "event_risk", "headline/Pre-IPO risk is not bounded", last, spread)
    bars15 = _fetch_bars(client, inst_id, "15m", 40)
    bars1h = _fetch_bars(client, inst_id, "1h", 40)
    if len(bars15) < 20 or len(bars1h) < 20:
        return _candidate(inst_id, "watch", 0, "insufficient_data", "not enough 15m/1h data", last, spread)
    metrics15 = _bar_metrics(bars15)
    metrics1h = _bar_metrics(bars1h)
    momentum = metrics1h["change_last10_pct"]
    side = "long" if momentum > 0 else "short"
    pullback_ok = _pullback_ok(side, last, metrics15, metrics1h)
    if not pullback_ok:
        return _candidate(inst_id, side, 20, "chase", "price is away from tactical structure; would be chase", last, spread)
    atr = metrics15["atr14"]
    if atr <= 0:
        return _candidate(inst_id, side, 0, "no_atr", "ATR unavailable", last, spread)
    sl_distance = max(atr * 1.05, last * 0.001)
    tp_distance = max(atr * 1.35, sl_distance * 1.15)
    nominal_size = _adaptive_size(last, tp_distance, target_tp_usdt=2.0)
    gross_tp = nominal_size * tp_distance
    gross_sl = nominal_size * sl_distance
    if gross_tp < config.min_tactical_tp_usdt:
        return _candidate(inst_id, side, 35, "small_target", f"gross TP {gross_tp:.2f}U below tactical minimum", last, spread)
    if gross_sl > gross_tp * 1.25:
        return _candidate(inst_id, side, 30, "bad_rr", "gross SL is too large versus TP", last, spread, gross_tp, gross_sl)
    estimated_fees = (gross_tp + gross_sl) * 0.04
    net_tp = gross_tp - estimated_fees
    net_sl = gross_sl + estimated_fees
    assumed_win_rate = 0.54 if abs(momentum) < 8 else 0.50
    ev = assumed_win_rate * net_tp - (1 - assumed_win_rate) * net_sl
    if ev < config.min_expected_value_usdt:
        return _candidate(
            inst_id,
            side,
            42,
            "thin_ev",
            f"estimated EV {ev:.2f}U is below clear-edge minimum {config.min_expected_value_usdt:.2f}U after costs",
            last,
            spread,
            gross_tp,
            gross_sl,
            ev,
        )
    score = 55 + min(20, abs(momentum) * 1.5) + min(12, max(0, config.max_spread_bps - spread) / 3)
    layer = "1h tactical" if gross_tp <= config.max_tactical_tp_usdt else "extension"
    return CandidateView(
        inst_id=inst_id,
        side=side,
        score=score,
        status="accepted",
        reason="B+ tactical structure: near 15m/1h structure, acceptable spread, positive estimated EV",
        last=last,
        spread_bps=spread,
        tp_layer=layer,
        gross_tp_usdt=gross_tp,
        gross_sl_usdt=gross_sl,
        expected_value_usdt=ev,
    )


def _candidate(
    inst_id: str,
    side: str,
    score: float,
    status: str,
    reason: str,
    last: float,
    spread: float,
    gross_tp: float = 0.0,
    gross_sl: float = 0.0,
    ev: float = 0.0,
) -> CandidateView:
    return CandidateView(
        inst_id=inst_id,
        side=side,
        score=score,
        status=status,
        reason=reason,
        last=last,
        spread_bps=spread,
        tp_layer="none",
        gross_tp_usdt=gross_tp,
        gross_sl_usdt=gross_sl,
        expected_value_usdt=ev,
    )


def _market_structure(client: Any, symbols: list[str]) -> dict[str, Any]:
    out: dict[str, Any] = {}
    for symbol in symbols:
        item: dict[str, Any] = {}
        try:
            ob = client.fetch_order_book(symbol, limit=5)
            bid = _float((ob.get("bids") or [[0]])[0][0])
            ask = _float((ob.get("asks") or [[0]])[0][0])
            item["orderbook"] = {"bid": bid, "ask": ask, "spread_bps": _spread_bps(bid, ask)}
        except Exception as exc:  # noqa: BLE001 - report boundary.
            item["orderbook_error"] = str(exc)
        frames: dict[str, Any] = {}
        for timeframe in ("15m", "1h", "4h"):
            bars = _fetch_bars(client, symbol, timeframe, 30)
            frames[timeframe] = _bar_metrics(bars) if bars else {"error": "no data"}
        item["timeframes"] = frames
        out[symbol] = item
    return out


def _fetch_bars(client: Any, symbol: str, timeframe: str, limit: int) -> list[list[float]]:
    try:
        return client.fetch_ohlcv(symbol, timeframe=timeframe, limit=limit) or []
    except Exception:
        return []


def _bar_metrics(bars: list[list[float]]) -> dict[str, Any]:
    if not bars:
        return {"error": "no bars"}
    highs = [float(row[2]) for row in bars]
    lows = [float(row[3]) for row in bars]
    closes = [float(row[4]) for row in bars]
    trs = []
    for idx in range(max(1, len(bars) - 14), len(bars)):
        prev = float(bars[idx - 1][4])
        high = float(bars[idx][2])
        low = float(bars[idx][3])
        trs.append(max(high - low, abs(high - prev), abs(low - prev)))
    change = ((closes[-1] / closes[-10]) - 1) * 100 if len(closes) >= 10 and closes[-10] else 0.0
    return {
        "lastBarCST": datetime.fromtimestamp(float(bars[-1][0]) / 1000, CST).strftime("%Y-%m-%d %H:%M"),
        "lastClose": closes[-1],
        "lastHigh": highs[-1],
        "lastLow": lows[-1],
        "recentHigh_10": max(highs[-10:]),
        "recentLow_10": min(lows[-10:]),
        "atr14": mean(trs) if trs else 0.0,
        "change_last10_pct": change,
    }


def _pullback_ok(side: str, last: float, metrics15: dict[str, Any], metrics1h: dict[str, Any]) -> bool:
    atr15 = _float(metrics15.get("atr14"))
    atr1h = _float(metrics1h.get("atr14"))
    if side == "long":
        return last <= _float(metrics15.get("recentHigh_10")) - atr15 * 0.35 or last <= _float(metrics1h.get("recentHigh_10")) - atr1h * 0.2
    return last >= _float(metrics15.get("recentLow_10")) + atr15 * 0.35 or last >= _float(metrics1h.get("recentLow_10")) + atr1h * 0.2


def _adaptive_size(last: float, tp_distance: float, *, target_tp_usdt: float) -> float:
    if last <= 0 or tp_distance <= 0:
        return 0.0
    return max(0.001, target_tp_usdt / tp_distance)
