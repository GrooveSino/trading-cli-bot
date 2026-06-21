from __future__ import annotations

import os
import time
from pathlib import Path
from typing import Any

from trading_gateway.app.config import get_gateway_config
from trading_gateway.application.market.specs import get_market_spec, get_venue_profile, snapshot_filename, supported_snapshot_pairs
from trading_gateway.application.marketdata.btcusdt.remote import snapshot_age_sec
from trading_gateway.application.marketdata.btcusdt.storage import read_snapshot


def create_snapshot_http_app() -> Any:
    try:
        from fastapi import Depends, FastAPI, Header, HTTPException
    except ModuleNotFoundError as exc:
        raise RuntimeError("marketdata HTTP service requires fastapi and uvicorn") from exc

    app = FastAPI(title="Trading Gateway MarketData Snapshot", version="1.0.0")

    def auth(authorization: str = Header(default="")) -> None:
        expected = _token()
        if not expected:
            raise HTTPException(status_code=503, detail="TBOT_MARKETDATA_TOKEN is not configured")
        if authorization != f"Bearer {expected}":
            raise HTTPException(status_code=401, detail="invalid bearer token")

    @app.get("/health")
    def health(_: None = Depends(auth)) -> dict[str, Any]:
        return {
            "status": "ok",
            "mode": "tbot_marketdata_snapshot_http",
            "snapshots": _snapshot_health(),
        }

    @app.get("/snapshot/btcusdt")
    def legacy_snapshot(_: None = Depends(auth)) -> dict[str, Any]:
        return _read_current_snapshot("okx-live", "btc")

    @app.get("/snapshot/{venue}/{symbol}")
    def venue_snapshot(venue: str, symbol: str, _: None = Depends(auth)) -> dict[str, Any]:
        return _read_current_snapshot(venue, symbol)

    return app


def run_snapshot_http_server(*, host: str | None = None, port: int | None = None) -> None:
    try:
        import uvicorn
    except ModuleNotFoundError as exc:
        raise RuntimeError("marketdata HTTP service requires uvicorn") from exc
    config = get_gateway_config().btcusdt_marketdata
    uvicorn.run(create_snapshot_http_app(), host=host or config.http_host, port=port or config.http_port, log_level="warning")


def _read_current_snapshot(venue: str = "okx-live", symbol: str = "btc") -> dict[str, Any]:
    path = _snapshot_path(venue, symbol)
    if not Path(path).exists():
        from fastapi import HTTPException

        raise HTTPException(status_code=404, detail="snapshot not found")
    payload = read_snapshot(path)
    payload.setdefault("http_snapshot", {})["served_ms"] = int(time.time() * 1000)
    return payload


def _snapshot_health() -> dict[str, Any]:
    rows = {}
    for venue, spec in supported_snapshot_pairs():
        slug = f"{venue.id}/{spec.key}"
        path = _snapshot_path(venue.id, spec.key)
        if not path.exists():
            rows[slug] = {"status": "missing", "path": str(path)}
            continue
        try:
            payload = read_snapshot(path)
        except Exception as exc:  # noqa: BLE001 - health should report corrupt snapshots.
            rows[slug] = {"status": "error", "path": str(path), "error": f"{type(exc).__name__}: {exc}"}
            continue
        rows[slug] = {
            "status": "ok",
            "path": str(path),
            "snapshot_time_cst": payload.get("snapshot_time_cst"),
            "age_sec": snapshot_age_sec(payload),
            "source_mode": payload.get("source_mode"),
        }
    return rows


def _snapshot_path(venue: str, symbol: str) -> Path:
    profile = get_venue_profile(venue)
    spec = get_market_spec(symbol)
    return get_gateway_config().btcusdt_marketdata.snapshot_path.parent / snapshot_filename(profile.id, spec.key)


def _token() -> str:
    env_name = get_gateway_config().btcusdt_marketdata.remote_http_token_env
    return (os.getenv(env_name) or "").strip()
