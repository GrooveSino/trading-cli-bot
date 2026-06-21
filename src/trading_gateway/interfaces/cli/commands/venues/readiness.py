from __future__ import annotations

from typing import Any

from trading_gateway.app.config import read_exchange_creds_with_source
from trading_gateway.infrastructure.exchange.account_modes import exchange_account_profile
from trading_gateway.infrastructure.exchange.factory import build_ccxt_client, close_client
from trading_gateway.support.redaction import redact_text


def okx_sim_trade_readiness(ccxt_symbol: str) -> dict[str, Any]:
    profile = exchange_account_profile("okx", "sim")
    credentials = okx_sim_credentials_check(profile)
    sandbox = okx_sim_sandbox_check()
    checks = [credentials, sandbox]
    if credentials["status"] == "ok":
        checks.append(okx_sim_private_auth_check(ccxt_symbol, credentials))
    else:
        checks.append({"name": "okx_sim_private_auth", "status": "skipped", "reason": "credentials not ready"})
    ready = all(row["status"] == "ok" for row in checks)
    return {"status": "ok" if ready else "not_ready", "checks": checks, "error": _readiness_error(checks)}


def okx_sim_credentials_check(profile: Any) -> dict[str, Any]:
    creds, source = read_exchange_creds_with_source(profile.exchange, profile.env_spec, profile.fallback_env_spec)
    required = [profile.env_spec.key_env, profile.env_spec.secret_env]
    if profile.env_spec.password_env:
        required.append(profile.env_spec.password_env)
    present = {profile.env_spec.key_env: bool(creds.api_key), profile.env_spec.secret_env: bool(creds.api_secret)}
    if profile.env_spec.password_env:
        present[profile.env_spec.password_env] = bool(creds.password)
    missing = [name for name in required if not present.get(name)] if source == "primary" else []
    return {
        "name": "okx_sim_credentials",
        "status": "ok" if not missing else "missing",
        "credential_source": source,
        "required_env": required,
        "fallback_env": _fallback_names(profile),
        "missing_env": missing,
    }


def okx_sim_private_auth_check(ccxt_symbol: str, credentials: dict[str, Any]) -> dict[str, Any]:
    if credentials["status"] != "ok":
        return {"name": "okx_sim_private_auth", "status": "skipped", "reason": "credentials not ready"}
    client = None
    try:
        client = build_ccxt_client("okx", "swap", require_private=True, account_mode="sim")
        fetch_positions = getattr(client, "fetch_positions", None)
        if callable(fetch_positions):
            fetch_positions([ccxt_symbol])
            method = "fetch_positions"
        else:
            fetch_balance = getattr(client, "fetch_balance", None)
            if not callable(fetch_balance):
                return {"name": "okx_sim_private_auth", "status": "skipped", "reason": "no read-only method"}
            fetch_balance()
            method = "fetch_balance"
        return {"name": "okx_sim_private_auth", "status": "ok", "method": method, "credential_source": credentials.get("credential_source")}
    except SystemExit as exc:
        return {"name": "okx_sim_private_auth", "status": "error", "error": redact_text(f"SystemExit: {exc}")}
    except Exception as exc:  # noqa: BLE001 - readiness should report, not raise.
        return {"name": "okx_sim_private_auth", "status": "error", "error": redact_text(f"{type(exc).__name__}: {exc}")}
    finally:
        if client is not None:
            close_client(client)


def okx_sim_sandbox_check() -> dict[str, Any]:
    client = None
    try:
        client = build_ccxt_client("okx", "swap", require_private=False, account_mode="sim")
        headers = getattr(client, "headers", {}) or {}
        enabled = bool(getattr(client, "trading_gateway_sandbox", False))
        simulated = headers.get("x-simulated-trading") == "1"
        return {
            "name": "okx_sim_sandbox_header",
            "status": "ok" if enabled and simulated else "error",
            "sandbox": enabled,
            "x_simulated_trading": headers.get("x-simulated-trading"),
        }
    except SystemExit as exc:
        return {"name": "okx_sim_sandbox_header", "status": "error", "error": f"SystemExit: {exc}"}
    except Exception as exc:  # noqa: BLE001 - readiness should report, not raise.
        return {"name": "okx_sim_sandbox_header", "status": "error", "error": f"{type(exc).__name__}: {exc}"}
    finally:
        if client is not None:
            close_client(client)


def _fallback_names(profile: Any) -> list[str]:
    spec = profile.fallback_env_spec
    if spec is None:
        return []
    names = [spec.key_env, spec.secret_env]
    if spec.password_env:
        names.append(spec.password_env)
    return names


def _readiness_error(checks: list[dict[str, Any]]) -> str | None:
    failed = [row for row in checks if row["status"] not in {"ok", "skipped"}]
    if not failed:
        return None
    parts = []
    for row in failed:
        if row.get("missing_env"):
            parts.append(f"{row['name']}: set {', '.join(row['missing_env'])} or fallback {', '.join(row.get('fallback_env') or [])}")
        else:
            parts.append(f"{row['name']}: {row.get('error') or row.get('reason') or row['status']}")
    return "; ".join(parts)
