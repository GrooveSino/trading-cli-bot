from __future__ import annotations

import time
from dataclasses import dataclass
from typing import Any, Callable

from trading_gateway.support.redaction import redact_text

from .storage import BtcusdtMarketDataStore


@dataclass
class CacheResult:
    payload: dict[str, Any]
    status: dict[str, Any]


class SectionCache:
    def __init__(self) -> None:
        self._items: dict[str, tuple[dict[str, Any], int]] = {}

    def get_or_refresh(
        self,
        *,
        section: str,
        store: BtcusdtMarketDataStore,
        now_ms: int,
        refresh_sec: float,
        collect: Callable[[], dict[str, Any]],
        accept: Callable[[dict[str, Any]], bool] | None = None,
        default: dict[str, Any] | None = None,
        force: bool = False,
    ) -> CacheResult:
        cached = self._latest(section, store)
        cached_ok = cached is None or accept is None or accept(cached[0])
        due = force or cached is None or not cached_ok or now_ms - cached[1] >= int(refresh_sec * 1000)
        if not due:
            return CacheResult(cached[0], self._status("cached", cached[1], now_ms))
        try:
            payload = collect()
            if accept is not None and not accept(payload):
                raise ValueError("collector returned degraded payload")
        except Exception as exc:  # noqa: BLE001 - snapshot can reuse previous good section.
            if (cached is None or not cached_ok) and default is None:
                raise
            if cached is None or not cached_ok:
                status = self._status("error", now_ms, now_ms)
                status["refresh_error"] = f"{type(exc).__name__}: {redact_text(exc)}"
                return CacheResult(default or {}, status)
            status = self._status("stale", cached[1], now_ms)
            status["refresh_error"] = f"{type(exc).__name__}: {redact_text(exc)}"
            return CacheResult(cached[0], status)
        self._items[section] = (payload, now_ms)
        store.record_snapshot_section(section, payload, now_ms)
        return CacheResult(payload, self._status("fresh", now_ms, now_ms))

    def _latest(self, section: str, store: BtcusdtMarketDataStore) -> tuple[dict[str, Any], int] | None:
        if section in self._items:
            return self._items[section]
        latest = store.latest_section_with_ts(section)
        if latest:
            self._items[section] = latest
        return latest

    @staticmethod
    def _status(status: str, collected_ms: int, now_ms: int) -> dict[str, Any]:
        return {
            "status": status,
            "collected_ms": collected_ms,
            "age_sec": max(0.0, (now_ms - collected_ms) / 1000),
            "wall_age_sec": max(0.0, time.time() - collected_ms / 1000),
        }
