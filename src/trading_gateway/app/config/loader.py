from __future__ import annotations

import tomllib
from pathlib import Path
from typing import Any

from .schema import BtcusdtMarketDataConfig, DaemonConfig, ExchangeEnvSpec, GatewayConfig, PairExecutionConfig, PlanningConfig, WebPollingConfig


def load_config_file(path: Path) -> GatewayConfig:
    if not path.exists():
        raise ValueError(f"Trading Gateway config file not found: {path}")
    try:
        raw = tomllib.loads(path.read_text(encoding="utf-8"))
    except tomllib.TOMLDecodeError as exc:
        raise ValueError(f"invalid Trading Gateway config TOML: {path}: {exc}") from exc
    return parse_gateway_config(path, raw)


def parse_gateway_config(path: Path, raw: dict[str, Any]) -> GatewayConfig:
    daemon = _table(raw, "daemon")
    polling = _table(_table(raw, "web"), "polling")
    network = _table(raw, "network")
    trading = _table(raw, "trading")
    planning = _table(trading, "planning")
    safety = _table(trading, "safety")
    perp = _table(trading, "perp_execution")
    bbo = _table(trading, "binance_perp_bbo")
    spot = _table(trading, "spot_execution")
    spot_bbo = _table(trading, "binance_spot_bbo")
    pair = _table(trading, "pair_execution")
    fees = _table(trading, "fees")
    paths = _table(raw, "paths")
    marketdata = _table(_table(raw, "marketdata"), "btcusdt")
    return GatewayConfig(
        path=path,
        dotenv_path=Path(_table(raw, "env")["dotenv_path"]),
        daemon=DaemonConfig(
            host=str(daemon["host"]),
            port=_int(daemon, "port"),
            private_refresh_interval_sec=_float(daemon, "private_refresh_interval_sec"),
            readiness_ttl_sec=_float(daemon, "readiness_ttl_sec"),
            startup_warmup_timeout_sec=_float(daemon, "startup_warmup_timeout_sec"),
            runtime_dir=Path(daemon["runtime_dir"]),
        ),
        btcusdt_marketdata=BtcusdtMarketDataConfig(
            remote_host=str(marketdata["remote_host"]),
            remote_snapshot_path=str(marketdata["remote_snapshot_path"]),
            remote_transport=str(marketdata.get("remote_transport", "ssh")),
            remote_http_url=str(marketdata.get("remote_http_url", "")),
            remote_http_proxy_url=str(marketdata.get("remote_http_proxy_url", "")),
            remote_http_token_env=str(marketdata.get("remote_http_token_env", "TBOT_MARKETDATA_TOKEN")),
            remote_ssh_fallback=bool(marketdata.get("remote_ssh_fallback", True)),
            remote_http_timeout_sec=float(marketdata.get("remote_http_timeout_sec", 1.5)),
            http_host=str(marketdata.get("http_host", "0.0.0.0")),
            http_port=int(marketdata.get("http_port", 8787)),
            local_snapshot_cache=Path(marketdata["local_snapshot_cache"]),
            sqlite_path=Path(marketdata["sqlite_path"]),
            snapshot_path=Path(marketdata["snapshot_path"]),
            poll_interval_sec=_float(marketdata, "poll_interval_sec"),
            public_refresh_interval_sec=_float(marketdata, "public_refresh_interval_sec"),
            enrichment_refresh_interval_sec=_float(marketdata, "enrichment_refresh_interval_sec"),
            account_refresh_interval_sec=_float(marketdata, "account_refresh_interval_sec"),
            prune_interval_sec=_float(marketdata, "prune_interval_sec"),
            retention_liquidation_hours=_float(marketdata, "retention_liquidation_hours"),
            retention_summary_hours=_float(marketdata, "retention_summary_hours"),
            max_storage_bytes=_int(marketdata, "max_storage_bytes"),
            liquidation_bucket_usd=_float(marketdata, "liquidation_bucket_usd"),
            max_remote_age_sec=_float(marketdata, "max_remote_age_sec"),
        ),
        planning=PlanningConfig(
            account_state_source=str(planning["account_state_source"]),
            account_state_max_age_sec=_float(planning, "account_state_max_age_sec"),
            allow_direct_exchange_fallback=bool(planning["allow_direct_exchange_fallback"]),
            market_data_source=str(planning["market_data_source"]),
            refresh_routes_after_live_completion=bool(planning["refresh_routes_after_live_completion"]),
        ),
        web_polling=WebPollingConfig(
            daemon_status_ms=_int(polling, "daemon_status_ms"),
            summary_ms=_int(polling, "summary_ms"),
            snapshot_ms=_int(polling, "snapshot_ms"),
            jobs_ms=_int(polling, "jobs_ms"),
            journals_ms=_int(polling, "journals_ms"),
            live_job_ms=_int(polling, "live_job_ms"),
            query_stale_ms=_int(polling, "query_stale_ms"),
        ),
        credential_envs=_credential_envs(),
        route_universe=Path(paths["route_universe"]),
        wallet_summary_cache=Path(paths["wallet_summary_cache"]),
        ccxt_timeout_ms=_int(network, "ccxt_timeout_ms"),
        account_snapshot_timeout_ms=_int(network, "account_snapshot_timeout_ms"),
        wallet_summary_timeout_ms=_int(network, "wallet_summary_timeout_ms"),
        enable_rate_limit=bool(network["enable_rate_limit"]),
        lab_max_quote_usdt=_float(safety, "max_quote_usdt"),
        require_live_confirm=bool(safety["require_live_confirm"]),
        perp_order_timeout_sec=_float(perp, "order_timeout_sec"),
        perp_max_requotes=_int(perp, "max_requotes"),
        perp_close_all_maker_attempts=_int(perp, "close_all_maker_attempts"),
        perp_poll_interval_sec=_float(perp, "poll_interval_sec"),
        perp_min_poll_interval_sec=_float(perp, "min_poll_interval_sec"),
        perp_target_tolerance_steps=_int(perp, "target_tolerance_steps"),
        perp_target_tolerance_quote_usdt=_float(perp, "target_tolerance_quote_usdt"),
        perp_target_leverage=_int(perp, "target_leverage"),
        bbo_order_type=str(bbo["order_type"]),
        bbo_time_in_force=str(bbo["time_in_force"]),
        bbo_price_match=str(bbo["price_match"]),
        spot_order_timeout_sec=_float(spot, "order_timeout_sec"),
        spot_max_requotes=_int(spot, "max_requotes"),
        spot_poll_interval_sec=_float(spot, "poll_interval_sec"),
        spot_min_poll_interval_sec=_float(spot, "min_poll_interval_sec"),
        spot_target_tolerance_steps=_int(spot, "target_tolerance_steps"),
        spot_target_tolerance_quote_usdt=_float(spot, "target_tolerance_quote_usdt"),
        spot_sell_all_rescue_mode=str(spot["sell_all_rescue_mode"]),
        spot_sell_all_rescue_max_quote_usdt=_float(spot, "sell_all_rescue_max_quote_usdt"),
        spot_sell_all_rescue_max_slippage_bps=_float(spot, "sell_all_rescue_max_slippage_bps"),
        spot_bbo_order_type=str(spot_bbo["order_type"]),
        spot_bbo_price_source=str(spot_bbo["price_source"]),
        spot_bbo_post_only=bool(spot_bbo["post_only"]),
        spot_bbo_price_refresh=str(spot_bbo["price_refresh"]),
        pair_config=_parse_pair_execution(pair),
        fee_rates_bps=_fee_rates(fees),
    )


def _parse_pair_execution(pair: dict[str, Any]) -> PairExecutionConfig:
    return PairExecutionConfig(
        normal_max_requotes=_int(pair, "normal_max_requotes"),
        unhedged_recovery_max_requotes=_int(pair, "unhedged_recovery_max_requotes"),
        allow_taker_rescue=bool(pair["allow_taker_rescue"]),
        max_taker_rescue_quote_usdt=_float(pair, "max_taker_rescue_quote_usdt"),
        max_slippage_bps=_float(pair, "max_slippage_bps"),
        max_unhedged_quote_usdt=_float(pair, "max_unhedged_quote_usdt"),
        poll_interval_sec=_float(pair, "poll_interval_sec"),
        target_tolerance_steps=_int(pair, "target_tolerance_steps"),
        target_tolerance_quote_usdt=_float(pair, "target_tolerance_quote_usdt"),
        journal_dir=Path(pair["pair_journal_dir"]),
        cancel_retry_count=_int(pair, "cancel_retry_count"),
        order_lookup_retry_count=_int(pair, "order_lookup_retry_count"),
        state_restore_timeout_sec=_float(pair, "state_restore_timeout_sec"),
        submit_unknown_recovery_sec=_float(pair, "submit_unknown_recovery_sec"),
        spot_quote_reserve_usdt=_float(pair, "spot_quote_reserve_usdt"),
        perp_margin_reserve_usdt=_float(pair, "perp_margin_reserve_usdt"),
        fee_buffer_bps=_float(pair, "fee_buffer_bps"),
        min_free_quote_after_order_usdt=_float(pair, "min_free_quote_after_order_usdt"),
        external_open_order_policy=str(pair["external_open_order_policy"]),
        allow_cancel_external_orders=bool(pair["allow_cancel_external_orders"]),
        overfill_policy=str(pair["overfill_policy"]),
        max_overfill_quote_usdt=_float(pair, "max_overfill_quote_usdt"),
        allow_reduce_overfilled_leg=bool(pair["allow_reduce_overfilled_leg"]),
    )


def _credential_envs() -> dict[str, ExchangeEnvSpec]:
    return {
        "binance": ExchangeEnvSpec("BINANCE_API_KEY", "BINANCE_API_SECRET"),
        "okx": ExchangeEnvSpec("OKX_API_KEY", "OKX_API_SECRET", "OKX_PASSWORD"),
        "gate": ExchangeEnvSpec("GATE_API_KEY", "GATE_API_SECRET", "GATE_PASSWORD"),
        "mexc": ExchangeEnvSpec("MEXC_API_KEY", "MEXC_API_SECRET", "MEXC_PASSWORD"),
    }


def _fee_rates(fees: dict[str, Any]) -> dict[str, dict[str, float]]:
    keys = ("spot_maker_bps", "spot_taker_bps", "perp_maker_bps", "perp_taker_bps")
    exchanges = ("binance", "okx", "gate", "mexc")
    return {exchange: {key: _float(_table(fees, exchange), key) for key in keys} for exchange in exchanges}


def _table(raw: dict[str, Any], key: str) -> dict[str, Any]:
    value = raw.get(key)
    if not isinstance(value, dict):
        raise ValueError(f"missing config section: {key}")
    return value


def _int(raw: dict[str, Any], key: str) -> int:
    return int(raw[key])


def _float(raw: dict[str, Any], key: str) -> float:
    return float(raw[key])
