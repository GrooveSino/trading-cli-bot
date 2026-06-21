from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

from trading_gateway.domain.models import normalize_exchange


@dataclass(frozen=True)
class SafetyConfig:
    max_quote_usdt: float
    require_live_confirm: bool


@dataclass(frozen=True)
class DaemonConfig:
    host: str
    port: int
    private_refresh_interval_sec: float
    readiness_ttl_sec: float
    startup_warmup_timeout_sec: float
    runtime_dir: Path


@dataclass(frozen=True)
class WebPollingConfig:
    daemon_status_ms: int
    summary_ms: int
    snapshot_ms: int
    jobs_ms: int
    journals_ms: int
    live_job_ms: int
    query_stale_ms: int


@dataclass(frozen=True)
class BtcusdtMarketDataConfig:
    remote_host: str
    remote_snapshot_path: str
    remote_transport: str
    remote_http_url: str
    remote_http_proxy_url: str
    remote_http_token_env: str
    remote_ssh_fallback: bool
    remote_http_timeout_sec: float
    http_host: str
    http_port: int
    local_snapshot_cache: Path
    sqlite_path: Path
    snapshot_path: Path
    poll_interval_sec: float
    public_refresh_interval_sec: float
    enrichment_refresh_interval_sec: float
    account_refresh_interval_sec: float
    prune_interval_sec: float
    retention_liquidation_hours: float
    retention_summary_hours: float
    max_storage_bytes: int
    liquidation_bucket_usd: float
    max_remote_age_sec: float


@dataclass(frozen=True)
class PlanningConfig:
    account_state_source: str
    account_state_max_age_sec: float
    allow_direct_exchange_fallback: bool
    market_data_source: str
    refresh_routes_after_live_completion: bool


@dataclass(frozen=True)
class PairExecutionConfig:
    normal_max_requotes: int
    unhedged_recovery_max_requotes: int
    allow_taker_rescue: bool
    max_taker_rescue_quote_usdt: float
    max_slippage_bps: float
    max_unhedged_quote_usdt: float
    poll_interval_sec: float
    target_tolerance_steps: int
    target_tolerance_quote_usdt: float
    journal_dir: Path
    cancel_retry_count: int
    order_lookup_retry_count: int
    state_restore_timeout_sec: float
    submit_unknown_recovery_sec: float
    spot_quote_reserve_usdt: float
    perp_margin_reserve_usdt: float
    fee_buffer_bps: float
    min_free_quote_after_order_usdt: float
    external_open_order_policy: str
    allow_cancel_external_orders: bool
    overfill_policy: str
    max_overfill_quote_usdt: float
    allow_reduce_overfilled_leg: bool


@dataclass(frozen=True)
class SpotExecutionView:
    order_timeout_sec: float
    order_type: str
    post_only: bool
    target_tolerance_quote_usdt: float
    sell_all_rescue_mode: str
    sell_all_rescue_max_quote_usdt: float
    sell_all_rescue_max_slippage_bps: float


@dataclass(frozen=True)
class PerpExecutionView:
    order_type: str
    time_in_force: str
    price_match: str
    target_leverage: int
    target_tolerance_quote_usdt: float


@dataclass(frozen=True)
class ExchangeEnvSpec:
    key_env: str
    secret_env: str
    password_env: str | None = None


@dataclass(frozen=True)
class GatewayConfig:
    path: Path
    dotenv_path: Path
    daemon: DaemonConfig
    btcusdt_marketdata: BtcusdtMarketDataConfig
    planning: PlanningConfig
    web_polling: WebPollingConfig
    credential_envs: dict[str, ExchangeEnvSpec]
    route_universe: Path
    wallet_summary_cache: Path
    ccxt_timeout_ms: int
    account_snapshot_timeout_ms: int
    wallet_summary_timeout_ms: int
    enable_rate_limit: bool
    lab_max_quote_usdt: float
    require_live_confirm: bool
    perp_order_timeout_sec: float
    perp_max_requotes: int
    perp_close_all_maker_attempts: int
    perp_poll_interval_sec: float
    perp_min_poll_interval_sec: float
    perp_target_tolerance_steps: int
    perp_target_tolerance_quote_usdt: float
    perp_target_leverage: int
    bbo_order_type: str
    bbo_time_in_force: str
    bbo_price_match: str
    spot_order_timeout_sec: float
    spot_max_requotes: int
    spot_poll_interval_sec: float
    spot_min_poll_interval_sec: float
    spot_target_tolerance_steps: int
    spot_target_tolerance_quote_usdt: float
    spot_sell_all_rescue_mode: str
    spot_sell_all_rescue_max_quote_usdt: float
    spot_sell_all_rescue_max_slippage_bps: float
    spot_bbo_order_type: str
    spot_bbo_price_source: str
    spot_bbo_post_only: bool
    spot_bbo_price_refresh: str
    pair_config: PairExecutionConfig
    fee_rates_bps: dict[str, dict[str, float]]

    @property
    def safety(self) -> SafetyConfig:
        return SafetyConfig(self.lab_max_quote_usdt, self.require_live_confirm)

    @property
    def pair_execution(self) -> PairExecutionConfig:
        return self.pair_config

    @property
    def spot_execution(self) -> SpotExecutionView:
        return SpotExecutionView(
            self.spot_order_timeout_sec,
            self.spot_bbo_order_type,
            self.spot_bbo_post_only,
            self.spot_target_tolerance_quote_usdt,
            self.spot_sell_all_rescue_mode,
            self.spot_sell_all_rescue_max_quote_usdt,
            self.spot_sell_all_rescue_max_slippage_bps,
        )

    @property
    def perp_execution(self) -> PerpExecutionView:
        return PerpExecutionView(
            self.bbo_order_type,
            self.bbo_time_in_force,
            self.bbo_price_match,
            self.perp_target_leverage,
            self.perp_target_tolerance_quote_usdt,
        )

    def fee_bps(self, market: str, liquidity: str = "maker", exchange: str | None = None) -> float:
        name = normalize_exchange(exchange or "binance")
        return self.fee_rates_bps[name][f"{market}_{liquidity}_bps"]
