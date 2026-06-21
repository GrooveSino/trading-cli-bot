from __future__ import annotations

from typing import Any

from trading_gateway.application.market.btcusdt.rendering import render_markdown as render_table_markdown
from trading_gateway.application.market.btcusdt.shared import fmt_number


def render_llm_context(snapshot: dict[str, Any]) -> str:
    table_text = render_table_markdown(snapshot)
    parsed = _parse_table_markdown(table_text)
    lines = [
        f"# 市场快照：{_title(snapshot)}",
        "",
        "## 输出口径",
        "- 本输出面向 LLM 直接读取，与 `--table` 使用同一份指标行；不是缩减摘要。",
        "- `--json` 仍是机器可读完整 payload；本输出保留完整指标、完整 LLM 特征 JSON、账户 overlay、采集错误与读数提醒。",
        "- 不生成交易执行点位、仓位参数或自动下单指令。",
        "",
        "## 快照元信息",
        *_meta_lines(snapshot),
        "",
        "## 核心读数",
        *_bullet_rows(parsed["main_rows"]),
        "",
        "## LLM 特征",
        *_bullet_rows(parsed["feature_rows"]),
        *_json_lines(parsed["json_blocks"]),
        "",
        "## 账户 Overlay",
        *_account_lines(parsed["main_rows"]),
        "",
        "## 数据质量",
        *_quality_lines(snapshot, parsed),
        "",
        "## 读数提醒",
        *_plain_lines(parsed["readings"]),
    ]
    return "\n".join(lines) + "\n"


def _parse_table_markdown(text: str) -> dict[str, Any]:
    main_rows: list[tuple[str, str, str, str]] = []
    feature_rows: list[tuple[str, str, str, str]] = []
    readings: list[str] = []
    errors: list[str] = []
    json_blocks: list[str] = []
    section = "main"
    in_json = False
    json_lines: list[str] = []
    for line in text.splitlines():
        stripped = line.strip()
        if stripped == "LLM 决策特征工程矩阵：":
            section = "features"
            continue
        if stripped == "交易读数：":
            section = "readings"
            continue
        if stripped == "采集错误：":
            section = "errors"
            continue
        if stripped == "```json":
            in_json = True
            json_lines = []
            continue
        if stripped == "```" and in_json:
            json_blocks.append("\n".join(json_lines))
            in_json = False
            continue
        if in_json:
            json_lines.append(line)
            continue
        row = _parse_pipe_row(stripped)
        if row and section == "features":
            feature_rows.append(row)
        elif row:
            main_rows.append(row)
        elif stripped.startswith("- ") and section == "readings":
            readings.append(stripped[2:])
        elif stripped.startswith("- ") and section == "errors":
            errors.append(stripped[2:])
    return {"main_rows": main_rows, "feature_rows": feature_rows, "readings": readings, "errors": errors, "json_blocks": json_blocks}


def _parse_pipe_row(line: str) -> tuple[str, str, str, str] | None:
    if not line.startswith("|") or "---" in line or "维度 | 指标" in line:
        return None
    cells = [cell.strip() for cell in line.strip("|").split("|")]
    if len(cells) != 4:
        return None
    return cells[0], cells[1], cells[2], cells[3]


def _bullet_rows(rows: list[tuple[str, str, str, str]]) -> list[str]:
    if not rows:
        return ["- 无"]
    bullets = [f"- {dim} / {metric}：{value} ｜ {source}" for dim, metric, value, source in rows]
    for _, metric, value, source in rows:
        if metric == "market_state":
            bullets.insert(0, f"- market_state={value}; {source}")
            break
    return bullets


def _account_lines(rows: list[tuple[str, str, str, str]]) -> list[str]:
    account_rows = [row for row in rows if _is_account_dim(row[0])]
    if not account_rows:
        return ["- 未包含账户 overlay，或账户 overlay 已跳过。"]
    return _bullet_rows(account_rows)


def _is_account_dim(dim: str) -> bool:
    return any(token in dim for token in ("账户", "持仓", "普通订单", "Algo", "订单"))


def _quality_lines(snapshot: dict[str, Any], parsed: dict[str, Any]) -> list[str]:
    rows = [row for row in parsed["main_rows"] if _is_quality_dim(row[0]) or _is_quality_metric(row[1])]
    vector = snapshot.get("llm_feature_vectors") or {}
    quality = vector.get("data_quality") or {}
    result = _bullet_rows(rows)
    result.append(f"- missing_critical：{', '.join(quality.get('missing_critical') or []) or 'none'}")
    result.append(f"- insufficient_history：{', '.join(quality.get('insufficient_history') or []) or 'none'}")
    if parsed["errors"]:
        result.extend(f"- 采集错误：{item}" for item in parsed["errors"])
    return result


def _is_quality_dim(dim: str) -> bool:
    return dim in {"采集", "远端快照", "远端缓存", "清算", "24h强平密度"}


def _is_quality_metric(metric: str) -> bool:
    return any(token in metric for token in ("口径", "事件数", "价格档位", "清算"))


def _json_lines(blocks: list[str]) -> list[str]:
    if not blocks:
        return []
    lines = ["", "### 完整 LLM Feature JSON"]
    for block in blocks:
        lines.extend(["```json", block, "```"])
    return lines


def _plain_lines(items: list[str]) -> list[str]:
    return [f"- {item}" for item in items] if items else ["- 无额外读数提醒。"]


def _meta_lines(snapshot: dict[str, Any]) -> list[str]:
    profile = snapshot.get("venue_profile") or {}
    remote = snapshot.get("remote_snapshot") or {}
    account = snapshot.get("account_overlay") or snapshot.get("okx_account") or {}
    sources = snapshot.get("data_sources") or []
    return [
        f"- venue={profile.get('id', '-')}; exchange={profile.get('exchange', '-')}; account_mode={profile.get('account_mode', '-')}; symbol={snapshot.get('symbol', '-')}",
        f"- snapshot_time={snapshot.get('snapshot_time_cst', '-')} Asia/Shanghai; cli_elapsed={snapshot.get('cli_elapsed_ms', '-')}ms",
        f"- remote={remote.get('status', 'local')}; transport={remote.get('transport', '-')}; age={fmt_number(remote.get('age_sec'), 1)}s; fetch={remote.get('fetch_ms', '-')}ms",
        f"- account_overlay={account.get('status', 'skipped')}; source={account.get('source', snapshot.get('display_account_label', '-'))}",
        f"- data_sources={'; '.join(str(item) for item in sources) if sources else '-'}",
    ]


def _title(snapshot: dict[str, Any]) -> str:
    profile = snapshot.get("venue_profile") or {}
    display = profile.get("display_name") or snapshot.get("display_market_label") or profile.get("id") or "Market"
    symbol = str(snapshot.get("symbol") or snapshot.get("base_asset") or "BTC").upper()
    return f"{display} {symbol}"
