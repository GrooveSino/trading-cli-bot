from __future__ import annotations

import json
import os
import subprocess
import time
from pathlib import Path
from typing import Any
from urllib.error import HTTPError, URLError
from urllib.request import ProxyHandler, Request, build_opener, urlopen

from trading_gateway.app.config import get_gateway_config

from .storage import read_snapshot, write_snapshot

REMOTE_REFRESH_BACKOFF_SEC = 20.0
PUBLIC_SNAPSHOT_MAX_AGE_SEC = 30.0
REMOTE_FETCH_TIMEOUT_SEC = 6.0


def load_remote_btcusdt_snapshot(
    *,
    remote: bool,
    remote_host: str | None = None,
    max_age_sec: float | None = None,
) -> dict[str, Any] | None:
    config = get_gateway_config().btcusdt_marketdata
    host = remote_host or config.remote_host
    max_age = config.max_remote_age_sec if max_age_sec is None else max_age_sec
    cache = config.local_snapshot_cache
    if not remote:
        return None
    cached = _read_cached(cache)
    if cached is not None and (not _is_compatible(cached) or not _cache_host_matches(cached, host)):
        cached = None
    if cached is not None and _is_fresh(cached, max_age):
        return _with_remote_status(cached, "cached", host, cache, max_age, fetch_ms=0)
    if _recent_refresh_failure(cache):
        return None
    if remote:
        started = time.perf_counter()
        fetched = _fetch_remote_snapshot_http(
            host,
            config.remote_http_url,
            config.remote_http_token_env,
            config.remote_http_timeout_sec,
            config.remote_http_proxy_url,
            cache,
        ) if config.remote_transport == "http" else None
        transport = "http" if fetched is not None else None
        if fetched is None and (config.remote_transport != "http" or config.remote_ssh_fallback):
            fetched = _fetch_remote_snapshot(host, config.remote_snapshot_path, cache)
            transport = "ssh" if fetched is not None else None
        if fetched is not None:
            _clear_refresh_failure(cache)
            return _with_remote_status(fetched, "fresh", host, cache, max_age, fetch_ms=int((time.perf_counter() - started) * 1000), transport=transport)
        _record_refresh_failure(cache)
    return None


def _fetch_remote_snapshot_http(host: str, url: str, token_env: str, timeout_sec: float, proxy_url: str, cache: Path) -> dict[str, Any] | None:
    if not url:
        return None
    token = (os.getenv(token_env) or "").strip()
    if not token:
        return None
    request = Request(url, headers={"Authorization": f"Bearer {token}", "User-Agent": "TradingGateway/1.0"})
    try:
        with _open_http(request, timeout_sec, proxy_url) as response:
            body = response.read().decode("utf-8")
    except (OSError, HTTPError, URLError):
        return None
    payload = _decode_payload(body)
    if payload is None:
        _record_refresh_failure(cache, "http returned invalid JSON")
        return None
    if not _is_compatible(payload):
        _record_refresh_failure(cache, "http incompatible snapshot: " + "; ".join(_compatibility_issues(payload)))
        return None
    payload = _tag_cache_origin(payload, host, "http", url=url)
    write_snapshot(cache, payload)
    return payload


def _open_http(request: Request, timeout_sec: float, proxy_url: str) -> Any:
    if not proxy_url:
        return urlopen(request, timeout=timeout_sec)  # noqa: S310 - URL comes from local config.
    opener = build_opener(ProxyHandler({"http": proxy_url, "https": proxy_url}))
    return opener.open(request, timeout=timeout_sec)


def _fetch_remote_snapshot_ssh(host: str, remote_path: str, cache: Path) -> dict[str, Any] | None:
    cache.parent.mkdir(parents=True, exist_ok=True)
    temp = cache.with_suffix(cache.suffix + ".tmp")
    control_path = _control_path(host, cache)
    command = [
        "ssh",
        "-o",
        "BatchMode=yes",
        "-o",
        "ConnectTimeout=3",
        "-o",
        "ConnectionAttempts=1",
        "-o",
        "ServerAliveInterval=2",
        "-o",
        "ServerAliveCountMax=1",
        "-o",
        "ControlMaster=auto",
        "-o",
        "ControlPersist=60",
        "-o",
        f"ControlPath={control_path}",
        host,
        f"cat {remote_path}",
    ]
    try:
        result = subprocess.run(command, text=True, capture_output=True, check=False, timeout=REMOTE_FETCH_TIMEOUT_SEC)
    except (OSError, subprocess.TimeoutExpired):
        return None
    if result.returncode != 0:
        try:
            temp.unlink()
        except FileNotFoundError:
            pass
        return None
    temp.write_text(result.stdout, encoding="utf-8")
    try:
        payload = read_snapshot(temp)
    except Exception:
        temp.unlink(missing_ok=True)
        return None
    if not _is_compatible(payload):
        temp.unlink(missing_ok=True)
        return None
    payload = _tag_cache_origin(payload, host, "ssh")
    write_snapshot(cache, payload)
    temp.unlink(missing_ok=True)
    return payload


def _fetch_remote_snapshot(host: str, remote_path: str, cache: Path) -> dict[str, Any] | None:
    return _fetch_remote_snapshot_ssh(host, remote_path, cache)


def _read_cached(cache: Path) -> dict[str, Any] | None:
    if not cache.exists():
        return None
    try:
        return read_snapshot(cache)
    except Exception:
        return None


def _payload_from_text(body: str) -> dict[str, Any] | None:
    payload = _decode_payload(body)
    return payload if payload is not None and _is_compatible(payload) else None


def _decode_payload(body: str) -> dict[str, Any] | None:
    try:
        payload = json.loads(body)
    except json.JSONDecodeError:
        return None
    return payload if isinstance(payload, dict) else None


def _with_remote_status(payload: dict[str, Any], status: str, host: str, cache: Path, max_age_sec: float, *, fetch_ms: int | None, transport: str | None = None) -> dict[str, Any]:
    age = _snapshot_age_sec(payload)
    payload = dict(payload)
    payload["remote_snapshot"] = {
        "status": _effective_status(status, age, max_age_sec),
        "host": host,
        "transport": transport or ((payload.get("remote_snapshot") or {}).get("transport")) or status,
        "local_cache": str(cache),
        "age_sec": age,
        "max_age_sec": max_age_sec,
        "fetch_ms": fetch_ms,
    }
    return payload


def _snapshot_age_sec(payload: dict[str, Any]) -> float | None:
    generated_ms = payload.get("snapshot_time_ms") or payload.get("generated_ms")
    if generated_ms is None:
        return None
    return max(0.0, time.time() - int(generated_ms) / 1000)


def snapshot_age_sec(payload: dict[str, Any]) -> float | None:
    return _snapshot_age_sec(payload)


def _is_fresh(payload: dict[str, Any], max_age_sec: float) -> bool:
    age = _snapshot_age_sec(payload)
    return age is not None and age <= max_age_sec


def _is_compatible(payload: dict[str, Any]) -> bool:
    features = ((payload.get("llm_feature_vectors") or {}).get("features") or {})
    return bool(
        payload.get("binance_ofi_3m")
        and features.get("order_flow_imbalance_3m")
        and _has_depth_coverage(payload)
        and _has_estimated_oi_delta(payload)
        and _has_conservative_liquidity_gap(features)
        and _sections_compatible(payload, get_gateway_config().btcusdt_marketdata.public_refresh_interval_sec)
    )


def _compatibility_issues(payload: dict[str, Any]) -> list[str]:
    features = ((payload.get("llm_feature_vectors") or {}).get("features") or {})
    issues: list[str] = []
    if not payload.get("binance_ofi_3m"):
        issues.append("missing binance_ofi_3m")
    if not features.get("order_flow_imbalance_3m"):
        issues.append("missing OFI feature")
    if not _has_depth_coverage(payload):
        issues.append("missing OKX depth coverage")
    if not _has_estimated_oi_delta(payload):
        issues.append("missing estimated OI delta")
    if not _has_conservative_liquidity_gap(features):
        issues.append("liquidity gap score is sparse/unsafe")
    config = get_gateway_config().btcusdt_marketdata
    if not _sections_compatible(payload, config.public_refresh_interval_sec):
        issues.append(_public_section_issue(payload, config.public_refresh_interval_sec))
    return issues or ["unknown compatibility failure"]


def _has_depth_coverage(payload: dict[str, Any]) -> bool:
    bands = ((payload.get("okx_market") or {}).get("depth_bands") or {})
    first = bands.get("0.5%") or {}
    return first.get("ask_coverage_pct") is not None and first.get("bid_coverage_pct") is not None


def _has_estimated_oi_delta(payload: dict[str, Any]) -> bool:
    delta = (((payload.get("binance_oi") or {}).get("delta") or {}).get("15m") or {})
    return delta.get("estimated_notional_delta_usd") is not None and delta.get("exchange_value_delta_usd") is not None


def _has_conservative_liquidity_gap(features: dict[str, Any]) -> bool:
    gap = features.get("liquidity_vacuum_down") or {}
    evidence = str(gap.get("evidence") or "")
    if "observed_down_buckets=1" in evidence and gap.get("value") is not None:
        return False
    return True


def _cache_host_matches(payload: dict[str, Any], host: str) -> bool:
    cached_host = ((payload.get("remote_snapshot") or {}).get("host"))
    return cached_host == host


def _tag_cache_origin(payload: dict[str, Any], host: str, transport: str, *, url: str | None = None) -> dict[str, Any]:
    tagged = dict(payload)
    tagged["remote_snapshot"] = {"host": host, "transport": transport, "cached_from_remote": True}
    if url:
        tagged["remote_snapshot"]["url"] = url
    return tagged


def _sections_compatible(payload: dict[str, Any], public_refresh_sec: float = PUBLIC_SNAPSHOT_MAX_AGE_SEC) -> bool:
    section = ((payload.get("section_cache") or {}).get("public_snapshot") or {})
    if not section:
        return False
    age = section.get("age_sec")
    snapshot_age = _snapshot_age_sec(payload)
    if age is None or snapshot_age is None:
        return False
    max_age = max(PUBLIC_SNAPSHOT_MAX_AGE_SEC, public_refresh_sec + PUBLIC_SNAPSHOT_MAX_AGE_SEC)
    return float(age) + snapshot_age <= max_age


def _public_section_issue(payload: dict[str, Any], public_refresh_sec: float) -> str:
    section = ((payload.get("section_cache") or {}).get("public_snapshot") or {})
    age = section.get("age_sec")
    snapshot_age = _snapshot_age_sec(payload)
    max_age = max(PUBLIC_SNAPSHOT_MAX_AGE_SEC, public_refresh_sec + PUBLIC_SNAPSHOT_MAX_AGE_SEC)
    if not section:
        return "missing public_snapshot section cache"
    error = section.get("refresh_error")
    return f"public_snapshot stale age={age}; snapshot_age={snapshot_age}; max_age={max_age}; refresh_error={error}"


def _effective_status(status: str, age: float | None, max_age_sec: float) -> str:
    if status in {"fresh", "cached"} and age is not None and age > max_age_sec:
        return "stale"
    return status


def _control_path(host: str, cache: Path) -> str:
    safe_host = "".join(ch if ch.isalnum() or ch in ".-_" else "_" for ch in host)
    return f"/tmp/tbot-ssh-{safe_host}-%p-%r"


def _failure_marker(cache: Path) -> Path:
    return cache.with_suffix(cache.suffix + ".refresh-failed")


def _recent_refresh_failure(cache: Path) -> bool:
    marker = _failure_marker(cache)
    return marker.exists() and time.time() - marker.stat().st_mtime <= REMOTE_REFRESH_BACKOFF_SEC


def _record_refresh_failure(cache: Path, reason: str | None = None) -> None:
    marker = _failure_marker(cache)
    marker.parent.mkdir(parents=True, exist_ok=True)
    marker.write_text(json.dumps({"time": time.time(), "reason": reason or remote_failure_reason(cache) or "remote unavailable"}), encoding="utf-8")


def _clear_refresh_failure(cache: Path) -> None:
    _failure_marker(cache).unlink(missing_ok=True)


def remote_failure_reason(cache: Path | None = None) -> str | None:
    marker = _failure_marker(cache or get_gateway_config().btcusdt_marketdata.local_snapshot_cache)
    if not marker.exists():
        return None
    try:
        payload = json.loads(marker.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return None
    reason = payload.get("reason") if isinstance(payload, dict) else None
    return str(reason) if reason else None
