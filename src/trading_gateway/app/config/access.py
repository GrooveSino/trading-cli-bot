from __future__ import annotations

import os
from pathlib import Path

from trading_gateway.domain.models import ExchangeCreds, normalize_exchange

from .loader import load_config_file
from .schema import GatewayConfig


DEFAULT_ENV_FILE = Path(".env")
DEFAULT_CONFIG_FILE = Path("config.toml")

_ACTIVE_CONFIG: GatewayConfig | None = None


def load_gateway_config(path: str | Path = DEFAULT_CONFIG_FILE) -> GatewayConfig:
    global _ACTIVE_CONFIG
    _ACTIVE_CONFIG = load_config_file(Path(path))
    return _ACTIVE_CONFIG


def get_gateway_config() -> GatewayConfig:
    return _ACTIVE_CONFIG or load_gateway_config()


def load_dotenv_file(path: str | Path) -> None:
    env_path = Path(path)
    if not env_path.exists():
        return
    for raw in env_path.read_text(encoding="utf-8").splitlines():
        line = raw.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, value = line.split("=", 1)
        os.environ.setdefault(key.strip(), value.strip().strip('"').strip("'"))


def read_exchange_creds(exchange: str, env_spec: object | None = None, fallback_env_spec: object | None = None) -> ExchangeCreds:
    creds, _ = read_exchange_creds_with_source(exchange, env_spec, fallback_env_spec)
    return creds


def read_exchange_creds_with_source(exchange: str, env_spec: object | None = None, fallback_env_spec: object | None = None) -> tuple[ExchangeCreds, str]:
    name = normalize_exchange(exchange)
    spec = env_spec or get_gateway_config().credential_envs[name]
    creds = _read_spec(spec)
    if _complete(creds, spec):
        return creds, "primary"
    if fallback_env_spec is not None and not _has_any(creds):
        fallback = _read_spec(fallback_env_spec)
        if _complete(fallback, fallback_env_spec):
            return fallback, "fallback"
    return creds, "primary"


def require_exchange_creds(exchange: str, env_spec: object | None = None, fallback_env_spec: object | None = None) -> ExchangeCreds:
    creds, source = read_exchange_creds_with_source(exchange, env_spec, fallback_env_spec)
    spec = env_spec or get_gateway_config().credential_envs[normalize_exchange(exchange)]
    missing = [] if source == "fallback" else _missing(creds, spec)
    if missing and fallback_env_spec is not None:
        missing.extend(name for name in _missing(_read_spec(fallback_env_spec), fallback_env_spec) if name not in missing)
    if missing:
        raise ValueError(f"{exchange} credentials missing in env: set {', '.join(missing)}")
    return creds


def _read_spec(spec: object) -> ExchangeCreds:
    key = (os.getenv(spec.key_env) or "").strip()
    secret = (os.getenv(spec.secret_env) or "").strip()
    password = (os.getenv(spec.password_env) or "").strip() if spec.password_env else ""
    return ExchangeCreds(api_key=key, api_secret=secret, password=password or None)


def _complete(creds: ExchangeCreds, spec: object) -> bool:
    return not _missing(creds, spec)


def _has_any(creds: ExchangeCreds) -> bool:
    return bool(creds.api_key or creds.api_secret or creds.password)


def _missing(creds: ExchangeCreds, spec: object) -> list[str]:
    missing = []
    if not creds.api_key:
        missing.append(spec.key_env)
    if not creds.api_secret:
        missing.append(spec.secret_env)
    if spec.password_env and not creds.password:
        missing.append(spec.password_env)
    return missing
