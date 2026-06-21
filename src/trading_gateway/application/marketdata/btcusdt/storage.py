from __future__ import annotations

import json
import os
import sqlite3
import threading
import time
from pathlib import Path
from typing import Any

from .liquidations import liquidation_density, utc_ms

SECTION_TABLES = {
    "public_snapshot": "agg_trades_summary",
    "funding_basis": "funding_basis",
    "top_trader_position_delta": "top_trader_ratios",
    "okx_account": "okx_account_snapshots",
}


class BtcusdtMarketDataStore:
    def __init__(self, path: Path) -> None:
        self.path = path
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self._conn = sqlite3.connect(self.path, timeout=30)
        self._conn.row_factory = sqlite3.Row
        self._conn.execute("pragma busy_timeout = 30000")
        self._conn.execute("pragma journal_mode = wal")
        self._conn.execute("pragma synchronous = normal")
        self._init_schema()

    def close(self) -> None:
        self._conn.close()

    def record_liquidation(self, event: dict[str, Any]) -> None:
        def write() -> None:
            self._conn.execute(
                "insert or ignore into liquidations(event_ms, symbol, side, liquidated_side, price, qty_btc, notional_usd, raw_json) values (?, ?, ?, ?, ?, ?, ?, ?)",
                (
                    int(event["event_ms"]),
                    event["symbol"],
                    event["side"],
                    event["liquidated_side"],
                    float(event["price"]),
                    float(event["qty_btc"]),
                    float(event["notional_usd"]),
                    json.dumps(event, ensure_ascii=False, sort_keys=True),
                ),
            )

        self._write_with_retry(write)

    def record_snapshot_section(self, section: str, payload: dict[str, Any], now_ms: int) -> None:
        payload_json = json.dumps(payload, ensure_ascii=False, sort_keys=True)

        def write() -> None:
            self._conn.execute("insert into metric_snapshots(section, ts_ms, payload_json) values (?, ?, ?)", (section, now_ms, payload_json))
            table = SECTION_TABLES.get(section)
            if table:
                self._conn.execute(f"insert into {table}(ts_ms, payload_json) values (?, ?)", (now_ms, payload_json))

        self._write_with_retry(write)

    def latest_section(self, section: str) -> dict[str, Any] | None:
        latest = self.latest_section_with_ts(section)
        return latest[0] if latest else None

    def latest_section_with_ts(self, section: str) -> tuple[dict[str, Any], int] | None:
        row = self._conn.execute(
            "select payload_json, ts_ms from metric_snapshots where section = ? order by ts_ms desc limit 1",
            (section,),
        ).fetchone()
        return (json.loads(row["payload_json"]), int(row["ts_ms"])) if row else None

    def liquidation_events_since(self, since_ms: int, *, symbol: str | None = None) -> list[dict[str, Any]]:
        if symbol:
            rows = self._conn.execute(
                "select raw_json from liquidations where event_ms >= ? and symbol = ? order by event_ms",
                (since_ms, symbol),
            ).fetchall()
        else:
            rows = self._conn.execute("select raw_json from liquidations where event_ms >= ? order by event_ms", (since_ms,)).fetchall()
        return [json.loads(row["raw_json"]) for row in rows]

    def liquidation_density_24h(
        self,
        *,
        bucket_usd: float,
        now_ms: int | None = None,
        stream_status: dict[str, Any] | None = None,
        symbol: str = "BTCUSDT",
    ) -> dict[str, Any]:
        current_ms = now_ms or utc_ms()
        events = self.liquidation_events_since(current_ms - 24 * 60 * 60 * 1000, symbol=symbol)
        return liquidation_density(events, bucket_usd=bucket_usd, now_ms=current_ms, stream_status=stream_status, symbol=symbol)

    def basis_history_bps(self, *, since_ms: int, section: str = "funding_basis") -> list[float]:
        rows = self._conn.execute("select payload_json from metric_snapshots where section = ? and ts_ms >= ? order by ts_ms", (section, since_ms)).fetchall()
        values: list[float] = []
        for row in rows:
            try:
                value = json.loads(row["payload_json"])["binance"]["basis_bps"]
            except (KeyError, TypeError, json.JSONDecodeError):
                continue
            if value is not None:
                values.append(float(value))
        return values

    def prune(self, *, now_ms: int, liquidation_retention_ms: int, summary_retention_ms: int, max_bytes: int, managed_dirs: list[Path]) -> dict[str, Any]:
        self._conn.execute("delete from liquidations where event_ms < ?", (now_ms - liquidation_retention_ms,))
        self._conn.execute("delete from metric_snapshots where ts_ms < ?", (now_ms - summary_retention_ms,))
        for table in SECTION_TABLES.values():
            self._conn.execute(f"delete from {table} where ts_ms < ?", (now_ms - summary_retention_ms,))
        self._conn.commit()
        deleted_rows = self._trim_for_size(max_bytes, managed_dirs)
        self._conn.execute("vacuum")
        return {"deleted_oldest_liquidation_rows": deleted_rows, "storage_bytes": storage_bytes(managed_dirs)}

    def _trim_for_size(self, max_bytes: int, managed_dirs: list[Path]) -> int:
        deleted = 0
        while storage_bytes(managed_dirs) > max_bytes:
            rows = self._conn.execute("select id from liquidations order by event_ms asc limit 1000").fetchall()
            if not rows:
                break
            self._conn.executemany("delete from liquidations where id = ?", [(row["id"],) for row in rows])
            self._conn.commit()
            deleted += len(rows)
        return deleted

    def _init_schema(self) -> None:
        self._conn.executescript(
            """
            create table if not exists liquidations (
                id integer primary key autoincrement,
                event_ms integer not null,
                symbol text not null,
                side text not null,
                liquidated_side text not null,
                price real not null,
                qty_btc real not null,
                notional_usd real not null,
                raw_json text not null,
                unique(event_ms, side, price, qty_btc)
            );
            create index if not exists idx_liquidations_event_ms on liquidations(event_ms);
            create table if not exists metric_snapshots (
                id integer primary key autoincrement,
                section text not null,
                ts_ms integer not null,
                payload_json text not null
            );
            create index if not exists idx_metric_snapshots_section_ts on metric_snapshots(section, ts_ms);
            create table if not exists agg_trades_summary (id integer primary key autoincrement, ts_ms integer not null, payload_json text not null);
            create index if not exists idx_agg_trades_summary_ts on agg_trades_summary(ts_ms);
            create table if not exists funding_basis (id integer primary key autoincrement, ts_ms integer not null, payload_json text not null);
            create index if not exists idx_funding_basis_ts on funding_basis(ts_ms);
            create table if not exists top_trader_ratios (id integer primary key autoincrement, ts_ms integer not null, payload_json text not null);
            create index if not exists idx_top_trader_ratios_ts on top_trader_ratios(ts_ms);
            create table if not exists okx_account_snapshots (id integer primary key autoincrement, ts_ms integer not null, payload_json text not null);
            create index if not exists idx_okx_account_snapshots_ts on okx_account_snapshots(ts_ms);
            """
        )
        self._conn.commit()

    def _write_with_retry(self, write: Any) -> None:
        for attempt in range(5):
            try:
                write()
                self._conn.commit()
                return
            except sqlite3.OperationalError as exc:
                self._conn.rollback()
                if "locked" not in str(exc).lower() or attempt == 4:
                    raise
                # The hosted collector and one-off diagnostics can briefly overlap.
                time.sleep(0.25 * (attempt + 1))


def write_snapshot(path: Path, payload: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temp = path.with_suffix(f"{path.suffix}.{os.getpid()}.{threading.get_ident()}.tmp")
    temp.write_text(json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True), encoding="utf-8")
    temp.replace(path)


def read_snapshot(path: Path) -> dict[str, Any]:
    return json.loads(path.read_text(encoding="utf-8"))


def storage_bytes(paths: list[Path]) -> int:
    total = 0
    for root in paths:
        if root.is_file():
            total += root.stat().st_size
            continue
        if root.exists():
            total += sum(path.stat().st_size for path in root.rglob("*") if path.is_file())
    return total
