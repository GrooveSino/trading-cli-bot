from __future__ import annotations

import json

from fakes import FakeMarketClient, CliTestCase


class MarketBtcusdtFeaturesTest(CliTestCase):
    def test_llm_features_are_added_to_snapshot(self) -> None:
        from trading_gateway.application.market.btcusdt_snapshot import build_btcusdt_snapshot

        snapshot = build_btcusdt_snapshot(FakeMarketClient(), now_ms=1_780_820_000_000, include_okx_account=False)
        vector = snapshot["llm_feature_vectors"]

        self.assertEqual(vector["schema_version"], "btcusdt_llm_features.v1")
        self.assertIn(vector["market_state"], {"DATA_DEGRADED", "RANGE_NEUTRAL", "BEARISH_DISTRIBUTION", "BULLISH_BREAKOUT", "SQUEEZE_OR_COVERING", "LONG_LIQUIDATION_RISK"})
        self.assertAlmostEqual(vector["features"]["oi_acceleration"]["value"], 1.0)
        self.assertAlmostEqual(vector["features"]["cvd_log_skew"]["value"], -0.405196327, places=6)
        self.assertEqual(vector["features"]["order_flow_imbalance_3m"]["tier"], "sell_dominant")
        self.assertIn("weighted_orderbook_gravity", vector["features"])
        self.assertIn("proxy=24h_15m_range_volume", vector["features"]["liquidity_vacuum_down"]["evidence"])

    def test_feature_flags_cover_thresholds_and_insufficient_history(self) -> None:
        from trading_gateway.application.market.btcusdt.features import build_llm_feature_vectors

        snapshot = _base_snapshot()
        vector = build_llm_feature_vectors(snapshot, basis_history_bps=[-1.0] * 24)
        codes = {flag["code"] for flag in vector["semantic_flags"]}

        self.assertIn("CVD_SELL_PRESSURE", codes)
        self.assertIn("WHALE_SELL_PRESSURE", codes)
        self.assertIn("ASK_GRAVITY", codes)
        self.assertIn("RETAIL_LONG_CROWDED", codes)
        self.assertIn("SMART_MONEY_DISTRIBUTION", codes)
        self.assertEqual(vector["market_state"], "LONG_LIQUIDATION_RISK")

    def test_basis_zscore_handles_missing_history(self) -> None:
        from trading_gateway.application.market.btcusdt.features import build_llm_feature_vectors

        vector = build_llm_feature_vectors(_base_snapshot(), basis_history_bps=[-1.0] * 3)
        codes = {flag["code"] for flag in vector["semantic_flags"]}

        self.assertIsNone(vector["features"]["basis_zscore"]["value"])
        self.assertIn("INSUFFICIENT_BASIS_HISTORY", codes)

    def test_liquidity_gap_rejects_sparse_downside_buckets(self) -> None:
        from trading_gateway.application.market.btcusdt.features import build_llm_feature_vectors

        snapshot = _base_snapshot()
        snapshot["binance_momentum"]["liquidity_buckets_24h"] = {
            "buckets": [{"price_bucket": 61750, "quote_volume_usd": 1}, {"price_bucket": 62000, "quote_volume_usd": 10_000_000}],
        }
        vector = build_llm_feature_vectors(snapshot, basis_history_bps=[-1.0] * 24)
        feature = vector["features"]["liquidity_vacuum_down"]
        flags = {flag["code"]: flag for flag in vector["semantic_flags"]}

        self.assertEqual(feature["status"], "insufficient")
        self.assertIsNone(feature["value"])
        self.assertIn("observed_down_buckets=1", feature["evidence"])
        self.assertNotIn("VACUUM_BELOW", flags)

    def test_markdown_renders_feature_matrix_and_json_block(self) -> None:
        from trading_gateway.application.market.btcusdt_snapshot import build_btcusdt_snapshot, render_markdown

        markdown = render_markdown(build_btcusdt_snapshot(FakeMarketClient(), now_ms=1_780_820_000_000, include_okx_account=False))

        self.assertIn("LLM 决策特征工程矩阵", markdown)
        self.assertIn("OFI", markdown)
        self.assertIn("3m OFI", markdown)
        self.assertIn("llm_feature_vectors", markdown)
        self.assertIn("btcusdt_llm_features.v1", markdown)
        self.assertNotIn("DOWNWARD_PUMP", markdown)
        self.assertNotIn("UPWARD_PUMP", markdown)
        self.assertNotIn("止盈", markdown)
        self.assertNotIn("杠杆建议", markdown)

    def test_cli_json_contains_feature_vector(self) -> None:
        result = self.run_cli("market", "btcusdt", "--json", "--local", "--no-okx-account", "--no-binance-account")
        self.assertEqual(result.returncode, 0, result.stderr)
        payload = json.loads(_strip_tbot_banner(result.stdout))

        self.assertEqual(payload["llm_feature_vectors"]["schema_version"], "btcusdt_llm_features.v1")
        self.assertIn("order_flow_imbalance_3m", payload["llm_feature_vectors"]["features"])


def _base_snapshot() -> dict:
    return {
        "okx_market": {"last": 62000, "orderbook_geometry": {"weighted_gravity": -0.2, "weighted_bid": 1, "weighted_ask": 2}},
        "binance_oi": {"current": {"btc": 1000}, "delta": {"5m": {"btc_delta": -20}, "15m": {"btc_delta": -30}, "1h": {"btc_delta": -100}}},
        "binance_cvd": {
            "taker_buy_usd": 1_000_000,
            "taker_sell_usd": 2_000_000,
            "whale_over_100k": {"delta_usd": -2_000_000, "buy_count": 1, "sell_count": 4},
        },
        "binance_ofi_3m": {"log_skew": -1.8, "tier": "extreme_sell", "buy_share": 0.14, "sell_share": 0.86, "delta_usd": -1_000_000, "agg_trade_count": 120, "price_change_pct": -0.2},
        "binance_ratios": {"global_account": {"longShortRatio": "2.1"}},
        "top_trader_position_delta": {"delta": {"1h": {"long_short_ratio_delta": -0.004}}},
        "funding_basis": {"binance": {"basis_bps": -4.0}},
        "binance_momentum": {
            "vpp_baseline_24h": {"zscore": 2.0, "sample_count": 96, "latest_pct_change": 0.1},
            "liquidity_buckets_24h": {"buckets": [{"price_bucket": 61750, "quote_volume_usd": 1}, {"price_bucket": 62000, "quote_volume_usd": 10_000_000}]},
        },
        "liquidation_density_24h": {"event_count": 0, "long_liq_usd": 0, "short_liq_usd": 0, "stream_status": {"status": "connected", "btc_force_order_messages": 0}},
    }


def _strip_tbot_banner(stdout: str) -> str:
    lines = stdout.splitlines()
    if lines and lines[0].startswith("tbot:"):
        return "\n".join(lines[1:])
    return stdout
