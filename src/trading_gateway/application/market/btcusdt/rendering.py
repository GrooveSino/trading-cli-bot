from __future__ import annotations

from typing import Any

from .features import compact_feature_json, feature_matrix_rows
from .shared import fmt_contracts, fmt_money, fmt_number, fmt_price, pct


def render_markdown(snapshot: dict[str, Any]) -> str:
    okx = snapshot.get("okx_market") or {}
    oi = snapshot.get("derivatives_oi") or snapshot.get("binance_oi") or {}
    cvd = snapshot.get("derivatives_cvd") or snapshot.get("binance_cvd") or {}
    ofi = snapshot.get("derivatives_ofi_3m") or snapshot.get("binance_ofi_3m") or {}
    ratios = snapshot.get("derivatives_long_short_ratios") or snapshot.get("binance_ratios") or {}
    momentum = snapshot.get("derivatives_momentum") or snapshot.get("binance_momentum") or {}
    liq = snapshot.get("derivatives_liquidations_30m") or snapshot.get("binance_liquidations") or {}
    liq24 = snapshot.get("liquidation_density_24h") or {}
    funding = snapshot.get("funding_basis") or {}
    top_delta = snapshot.get("top_trader_position_delta") or {}
    remote = snapshot.get("remote_snapshot") or {}
    section_cache = snapshot.get("section_cache") or {}
    market_label = snapshot.get("display_market_label") or "OKX"
    account_label = snapshot.get("display_account_label") or "OKX BTC"
    instrument_label = snapshot.get("account_instrument_label") or "BTC-USDT-SWAP"
    rows = [
        ("快照", "时间", snapshot.get("snapshot_time_cst"), "Asia/Shanghai"),
        ("采集", "耗时/策略", _timing_text(snapshot), _strategy_text(snapshot)),
        ("价格", f"{market_label} 最新价", fmt_price(okx.get("last")), f"{okx.get('source', '-')}; {okx.get('timestamp_cst', '-')}"),
        ("价格", "Best bid / ask", f"{fmt_price(okx.get('best_bid'))} / {fmt_price(okx.get('best_ask'))}", okx.get("timestamp_cst", "-")),
    ]
    for band, data in (okx.get("depth_bands") or {}).items():
        coverage = _depth_coverage_text(data)
        rows.append(("盘口深度", f"{band} Ask visible", fmt_money(data.get("ask_notional_usd")), coverage))
        rows.append(("盘口深度", f"{band} Bid visible", fmt_money(data.get("bid_notional_usd")), coverage))
        rows.append(("盘口深度", f"{band} Bid/Ask visible", fmt_number(data.get("bid_ask_ratio"), 3), coverage))
    for idx, wall in enumerate((okx.get("super_ask_walls") or [])[:10], start=1):
        rows.append(("超级卖墙", f"Ask wall #{idx}", f"{fmt_price(wall.get('price'))} / {fmt_contracts(wall.get('contracts'))} 张 / {fmt_money(wall.get('notional_usd'))}", okx.get("timestamp_cst", "-")))
    for level, wall in (okx.get("key_super_ask_levels") or {}).items():
        value = "未发现 +/-30 美元内 >$1M 单档卖墙"
        if wall:
            value = f"{fmt_price(wall.get('price'))} / {fmt_money(wall.get('notional_usd'))}"
        rows.append(("关键价位", f"{level} 附近超级卖墙", value, okx.get("timestamp_cst", "-")))
    current_oi = (oi.get("current") or {})
    rows.append(("OI", "当前 OI", f"{fmt_number(current_oi.get('btc'), 3)} BTC", current_oi.get("timestamp_cst", "-")))
    for label, data in (oi.get("delta") or {}).items():
        oi_source = f"{data.get('from_cst', '-')} -> {data.get('to_cst', '-')}; exchange_value_delta={fmt_money(data.get('exchange_value_delta_usd'))}"
        rows.append(("OI", f"{label} Delta", f"{fmt_number(data.get('btc_delta'), 3)} BTC / est {fmt_money(data.get('estimated_notional_delta_usd'))}", oi_source))
    rows.extend(
        [
            ("CVD", "15m Taker Buy", fmt_money(cvd.get("taker_buy_usd")), f"{cvd.get('from_cst', '-')} -> {cvd.get('to_cst', '-')}"),
            ("CVD", "15m Taker Sell", fmt_money(cvd.get("taker_sell_usd")), f"{cvd.get('from_cst', '-')} -> {cvd.get('to_cst', '-')}"),
            ("CVD", "15m Delta", f"{fmt_money(cvd.get('delta_usd'))} / {fmt_number(cvd.get('delta_btc'), 3)} BTC", f"aggTrades={cvd.get('agg_trade_count', '-')}"),
        ]
    )
    whale = cvd.get("whale_over_100k") or {}
    rows.append(("巨鲸流", ">$100K Net Flow", f"{fmt_money(whale.get('delta_usd'))} (buy {fmt_money(whale.get('buy_usd'))}, sell {fmt_money(whale.get('sell_usd'))})", f"buy_count={whale.get('buy_count', '-')} sell_count={whale.get('sell_count', '-')}"))
    if ofi:
        rows.extend(
            [
                ("OFI", "3m Taker Buy/Sell", f"{fmt_money(ofi.get('taker_buy_usd'))} / {fmt_money(ofi.get('taker_sell_usd'))}", f"{ofi.get('from_cst', '-')} -> {ofi.get('to_cst', '-')}"),
                ("OFI", "3m Delta", f"{fmt_money(ofi.get('delta_usd'))} / {fmt_number(ofi.get('delta_btc'), 3)} BTC", f"aggTrades={ofi.get('agg_trade_count', '-')} tier={ofi.get('tier', '-')}"),
                ("OFI", "3m Buy/Sell share", f"{pct(ofi.get('buy_share'))} / {pct(ofi.get('sell_share'))}", f"price {fmt_money(ofi.get('price_change_usd'))} / {fmt_number(ofi.get('price_change_pct'), 4)}%"),
            ]
        )
    for name, label in [("top_position", "大户持仓多空比"), ("top_account", "大户账户多空比"), ("global_account", "全市场账户多空比")]:
        row = ratios.get(name) or {}
        rows.append(("多空比", label, f"Long {pct(row.get('longAccount'))} / Short {pct(row.get('shortAccount'))} / Ratio {row.get('longShortRatio', '-')}", row.get("timestamp_cst", "-")))
    _append_remote_marketdata_rows(rows, remote, section_cache, liq24, funding, top_delta)
    for interval, data in ((momentum.get("rsi") or {}).items()):
        rows.append(("RSI", f"{interval} RSI14", f"live {fmt_number(data.get('rsi14_live'), 2)} / closed {fmt_number(data.get('rsi14_latest_closed'), 2)}", data.get("current_bar_open_cst", "-")))
    kline = momentum.get("latest_closed_15m") or {}
    rows.extend(
        [
            ("15m K线", "最新已收盘 O/H/L/C", f"{fmt_price(kline.get('open'))} / {fmt_price(kline.get('high'))} / {fmt_price(kline.get('low'))} / {fmt_price(kline.get('close'))}", kline.get("open_cst", "-")),
            ("15m K线", "成交额 / 涨跌幅 / 振幅", f"{fmt_money(kline.get('quote_volume_usd'))} / {fmt_number(kline.get('pct_change'), 4)}% / {fmt_number(kline.get('range_pct'), 4)}%", kline.get("close_cst", "-")),
            ("VPP", "VPP_by_close", fmt_money(kline.get("vpp_by_close")), "成交额 / abs(收盘涨跌幅%)"),
            ("VPP", "VPP_by_range", fmt_money(kline.get("vpp_by_range")), "成交额 / 振幅%"),
            ("影线", "上影线 / 下影线", f"{fmt_number(kline.get('upper_wick_pct'), 4)}% / {fmt_number(kline.get('lower_wick_pct'), 4)}%", kline.get("close_cst", "-")),
        ]
    )
    liq_value = f"Long {fmt_money(liq.get('long_liq_usd'))} / Short {fmt_money(liq.get('short_liq_usd'))}" if liq.get("available") else f"无法公开精确获取：{liq.get('note') or liq.get('reason')}"
    rows.append(("清算", "30m Long/Short Liq", liq_value, liq.get("source", "-")))
    account = snapshot.get("okx_account") or snapshot.get("account_overlay") or {}
    if account:
        counts = account.get("counts") or {}
        rows.append((f"{account_label}账户", "账户采集状态", account.get("status", "-"), f"{account.get('source', '-')}; {account.get('timestamp_cst', '-')}"))
        rows.append((f"{account_label}账户", "持仓/普通挂单/Algo单数量", f"{counts.get('positions', 0)} / {counts.get('open_orders', 0)} / {counts.get('algo_orders', 0)}", f"仅 {instrument_label}"))
        for index, position in enumerate(account.get("positions") or [], start=1):
            rows.append((f"{account_label}持仓", f"Position #{index}", _position_text(position), position.get("updated_at_cst") or account.get("timestamp_cst", "-")))
        for index, order in enumerate(account.get("open_orders") or [], start=1):
            rows.append((f"{account_label}普通订单", f"Order #{index}", _open_order_text(order), order.get("updated_at_cst") or order.get("created_at_cst") or account.get("timestamp_cst", "-")))
        for kind, orders in (account.get("algo_orders") or {}).items():
            for index, order in enumerate(orders or [], start=1):
                rows.append((f"{account_label} Algo", f"{kind} #{index}", _algo_order_text(order), order.get("updated_at_cst") or order.get("created_at_cst") or account.get("timestamp_cst", "-")))
        if account.get("status") == "error":
            rows.append((f"{account_label}账户", "私有接口错误", account.get("error", "-"), account.get("source", "-")))
    lines = [f"数据快照时间：{snapshot.get('snapshot_time_cst')} Asia/Shanghai", "", "| 维度 | 指标 | 数值 | 数据源/时间戳 |", "|---|---:|---:|---|"]
    lines.extend(f"| {dim} | {metric} | {value} | {source} |" for dim, metric, value, source in rows)
    _append_llm_feature_section(lines, snapshot.get("llm_feature_vectors") or {})
    lines.extend(["", "交易读数："])
    lines.extend(f"- {item}" for item in snapshot.get("readings") or [])
    if snapshot.get("errors"):
        lines.extend(["", "采集错误："])
        lines.extend(f"- `{key}`: {value}" for key, value in snapshot["errors"].items())
    return "\n".join(lines) + "\n"


def _append_llm_feature_section(lines: list[str], vector: dict[str, Any]) -> None:
    if not vector:
        return
    lines.extend(["", "LLM 决策特征工程矩阵：", "", "| 维度 | 指标 | 数值 | 证据/等级 |", "|---|---:|---:|---|"])
    lines.extend(f"| {dim} | {metric} | {value} | {source} |" for dim, metric, value, source in feature_matrix_rows(vector))
    lines.extend(["", "```json", compact_feature_json(vector), "```"])


def _depth_coverage_text(data: dict[str, Any]) -> str:
    status = "complete" if data.get("coverage_complete") else "visible_only"
    return f"{status}; coverage ask={fmt_number(data.get('ask_coverage_pct'), 3)}% bid={fmt_number(data.get('bid_coverage_pct'), 3)}%"


def _append_remote_marketdata_rows(rows: list[tuple[str, str, object, object]], remote: dict[str, Any], section_cache: dict[str, Any], liq24: dict[str, Any], funding: dict[str, Any], top_delta: dict[str, Any]) -> None:
    if remote:
        rows.append(("远端快照", "Tokyo appliance", f"{remote.get('status', '-')} / age={fmt_number(remote.get('age_sec'), 1)}s", remote.get("host", "-")))
    for section in ("public_snapshot", "funding_basis", "top_trader_position_delta", "okx_account"):
        status = section_cache.get(section) or {}
        if status:
            rows.append(("远端缓存", section, f"{status.get('status', '-')} / age={fmt_number(status.get('age_sec'), 1)}s", status.get("refresh_error", "-")))
    if liq24:
        rows.append(("24h强平密度", "口径", liq24.get("note", "已发生强平密度"), liq24.get("source", "-")))
        rows.append(("24h强平密度", "Long/Short Liq", f"Long {fmt_money(liq24.get('long_liq_usd'))} / Short {fmt_money(liq24.get('short_liq_usd'))}", liq24.get("generated_at_cst", "-")))
        rows.append(("24h强平密度", "事件数/最近BTC事件", f"{liq24.get('event_count', 0)} / {liq24.get('last_event_cst') or '-'}", _liquidation_stream_text(liq24)))
        if not liq24.get("buckets"):
            rows.append(("24h强平密度", "价格档位", "暂无 BTCUSDT 强平档位", "WebSocket 连接正常时也可能长时间没有 BTC 强平事件"))
        for bucket in _top_liquidation_buckets(liq24):
            rows.append(("24h强平密度", f"{fmt_price(bucket.get('price_bucket'))} 档", f"Long {fmt_money(bucket.get('long_liq_usd'))} / Short {fmt_money(bucket.get('short_liq_usd'))}", f"count {bucket.get('long_count', 0)}/{bucket.get('short_count', 0)}"))
    derivatives = (funding.get("derivatives") or funding.get("binance") or {})
    okx = (funding.get("okx") or {})
    if derivatives:
        rows.append(("Funding/Basis", "衍生品 Mark-Index", f"{fmt_money(derivatives.get('basis_usd'))} / {fmt_number(derivatives.get('basis_bps'), 2)} bps", derivatives.get("timestamp_cst", "-")))
        rows.append(("Funding/Basis", "衍生品 funding", f"last {fmt_number(derivatives.get('last_funding_rate'), 6)}", derivatives.get("next_funding_time_cst", "-")))
    if okx:
        rows.append(("Funding/Basis", "OKX funding", f"current {fmt_number(okx.get('funding_rate'), 6)} / next {fmt_number(okx.get('next_funding_rate'), 6)}", okx.get("next_funding_time_cst", "-")))
    delta = top_delta.get("delta") or {}
    current = top_delta.get("current") or {}
    if current:
        rows.append(("Top Trader Delta", "当前大户持仓比", f"Long {pct(current.get('long_account'))} / Short {pct(current.get('short_account'))} / Ratio {fmt_number(current.get('long_short_ratio'), 4)}", current.get("timestamp_cst", "-")))
    for label in ("15m", "1h"):
        row = delta.get(label) or {}
        if row:
            rows.append(("Top Trader Delta", f"{label} Ratio Delta", fmt_number(row.get("long_short_ratio_delta"), 4), f"{row.get('from_cst', '-')} -> {row.get('to_cst', '-')}"))


def _top_liquidation_buckets(liq24: dict[str, Any]) -> list[dict[str, Any]]:
    rows = list(liq24.get("buckets") or [])
    rows.sort(key=lambda row: float(row.get("long_liq_usd") or 0) + float(row.get("short_liq_usd") or 0), reverse=True)
    return rows[:8]


def _liquidation_stream_text(liq24: dict[str, Any]) -> str:
    status = liq24.get("stream_status") or {}
    parts = [
        f"stream={status.get('status', '-')}",
        f"uptime={fmt_number(status.get('uptime_sec'), 1)}s",
        f"all={status.get('total_force_order_messages', 0)}",
        f"btc={status.get('btc_force_order_messages', 0)}",
    ]
    if status.get("last_message_cst"):
        parts.append(f"last_msg={status.get('last_message_cst')}")
    if status.get("last_error"):
        parts.append(f"err={status.get('last_error')}")
    return "; ".join(parts)


def build_readings(snapshot: dict[str, Any]) -> list[str]:
    okx = snapshot.get("okx_market") or {}
    cvd = snapshot.get("derivatives_cvd") or snapshot.get("binance_cvd") or {}
    momentum = snapshot.get("derivatives_momentum") or snapshot.get("binance_momentum") or {}
    readings: list[str] = []
    super_walls = okx.get("super_ask_walls") or []
    if super_walls:
        wall = super_walls[0]
        readings.append(f"上方最大可见超级卖墙在 {fmt_price(wall.get('price'))}，约 {fmt_money(wall.get('notional_usd'))}，冲到该区域前后需要观察是否放量滞涨。")
    delta = cvd.get("delta_usd")
    if delta is not None:
        direction = "主动买入占优" if delta > 0 else "主动卖出占优"
        readings.append(f"15m CVD 为 {fmt_money(delta)}，当前衍生品短窗口表现为{direction}。")
    kline = (momentum.get("latest_closed_15m") or {})
    if kline.get("vpp_by_close") is not None:
        readings.append(f"最新已收 15m K 的 VPP_by_close 为 {fmt_money(kline.get('vpp_by_close'))}/1%，用于衡量价格推进是否吃力。")
    rsi15 = (((momentum.get("rsi") or {}).get("15m") or {}).get("rsi14_live"))
    if rsi15 is not None:
        readings.append(f"15m RSI14 当前约 {fmt_number(rsi15, 2)}，高于 70 时按短线过热处理，但不能单独作为做空信号。")
    liq = snapshot.get("derivatives_liquidations_30m") or snapshot.get("binance_liquidations") or {}
    liq24 = snapshot.get("liquidation_density_24h") or {}
    if liq24:
        readings.append(f"24h 已发生强平密度：Long {fmt_money(liq24.get('long_liq_usd'))} / Short {fmt_money(liq24.get('short_liq_usd'))}，注意这不是未来爆仓热力图。")
    if not liq.get("available"):
        readings.append("全市场清算密度无法通过免费公开 REST 回溯，相关判断不能伪装成精确清算地图。")
    account = snapshot.get("okx_account") or {}
    counts = account.get("counts") or {}
    if account.get("status") == "ok" and (counts.get("positions") or counts.get("open_orders") or counts.get("algo_orders")):
        readings.append(
            f"OKX BTC 当前账户侧：持仓 {counts.get('positions', 0)} 个、普通挂单 {counts.get('open_orders', 0)} 个、条件/TP/SL 单 {counts.get('algo_orders', 0)} 个。"
        )
    return readings[:5]


def _position_text(position: dict[str, Any]) -> str:
    parts = [
        str(position.get("side") or "-"),
        f"size={position.get('size', '-')}",
        f"entry={position.get('entry_price', '-')}",
        f"mark={position.get('mark_price', '-')}",
        f"upl={position.get('unrealized_pnl', '-')}",
        f"liq={position.get('liq_price', '-')}",
        f"lev={position.get('leverage', '-')}",
        str(position.get("margin_mode") or "-"),
    ]
    return " / ".join(parts)


def _open_order_text(order: dict[str, Any]) -> str:
    parts = [
        f"id={order.get('order_id', '-')}",
        str(order.get("side") or "-"),
        str(order.get("order_type") or "-"),
        f"px={order.get('price', '-')}",
        f"sz={order.get('size', '-')}",
        f"filled={order.get('filled', '-')}",
        f"state={order.get('state', '-')}",
        f"reduceOnly={order.get('reduce_only', '-')}",
    ]
    return " / ".join(parts)


def _algo_order_text(order: dict[str, Any]) -> str:
    triggers = []
    if order.get("tp_trigger"):
        triggers.append(f"TP={order.get('tp_trigger')}")
    if order.get("sl_trigger"):
        triggers.append(f"SL={order.get('sl_trigger')}")
    if order.get("trigger"):
        triggers.append(f"trigger={order.get('trigger')}")
    trigger_text = ", ".join(triggers) if triggers else "trigger=-"
    parts = [
        f"id={order.get('algo_id', '-')}",
        str(order.get("side") or "-"),
        str(order.get("ord_type") or "-"),
        f"sz={order.get('size', '-')}",
        trigger_text,
        f"state={order.get('state', '-')}",
        f"reduceOnly={order.get('reduce_only', '-')}",
    ]
    return " / ".join(parts)


def _timing_text(snapshot: dict[str, Any]) -> str:
    cli_elapsed = snapshot.get("cli_elapsed_ms")
    remote_fetch = (snapshot.get("remote_snapshot") or {}).get("fetch_ms")
    timings = snapshot.get("collector_timings_ms") or {}
    if not timings:
        return _elapsed_text(cli_elapsed, remote_fetch)
    total = sum(int(value or 0) for value in timings.values())
    slow = sorted(timings.items(), key=lambda item: int(item[1] or 0), reverse=True)[:3]
    slow_text = ", ".join(f"{name}={value}ms" for name, value in slow)
    prefix = _elapsed_text(cli_elapsed, remote_fetch)
    return f"{prefix}; collector_sum={total}ms; slowest {slow_text}" if prefix != "-" else f"collector_sum={total}ms; slowest {slow_text}"


def _elapsed_text(cli_elapsed: Any, remote_fetch: Any) -> str:
    parts = []
    if cli_elapsed is not None:
        parts.append(f"cli={cli_elapsed}ms")
    if remote_fetch is not None:
        parts.append(f"remote_fetch={remote_fetch}ms")
    return "; ".join(parts) if parts else "-"


def _strategy_text(snapshot: dict[str, Any]) -> str:
    strategy = snapshot.get("fetch_strategy") or {}
    agg = strategy.get("aggTrades") or {}
    if not agg:
        return str(strategy.get("http") or "-")
    return "aggTrades {mode}, slices={slices}, workers={workers}, pages={pages}, fallback={fallback}".format(
        mode=agg.get("mode", "-"),
        slices=agg.get("slices", "-"),
        workers=agg.get("workers", "-"),
        pages=agg.get("pages", "-"),
        fallback=agg.get("fallback", "-"),
    )
