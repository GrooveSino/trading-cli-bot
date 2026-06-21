from __future__ import annotations

import json
from contextlib import redirect_stdout
from io import StringIO
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest.mock import patch

from fakes import FakeAggTradeClient, FakeAggTradeFallbackClient, FakeMarketClient, FakeOkxBtcAccountClient, CliTestCase


class MarketBtcusdtTest(CliTestCase):
    def test_btcusdt_snapshot_calculates_walls_oi_cvd_rsi_and_vpp(self) -> None:
        from trading_gateway.application.market import btcusdt_snapshot as market

        client = FakeMarketClient()
        snapshot = market.build_btcusdt_snapshot(client, now_ms=1_780_820_000_000, include_okx_account=False)

        self.assertEqual(snapshot["mode"], "btcusdt_market_snapshot")
        self.assertEqual(snapshot["okx_market"]["contract_btc"], 0.01)
        self.assertEqual(snapshot["okx_market"]["super_ask_walls"][0]["price"], 62500.0)
        self.assertGreater(snapshot["okx_market"]["super_ask_walls"][0]["notional_usd"], 1_000_000)
        self.assertAlmostEqual(snapshot["okx_market"]["depth_bands"]["0.5%"]["bid_ask_ratio"], 309400 / 310500, places=6)
        self.assertFalse(snapshot["okx_market"]["depth_bands"]["1.5%"]["coverage_complete"])
        self.assertAlmostEqual(snapshot["binance_oi"]["delta"]["5m"]["btc_delta"], 10.0)
        self.assertAlmostEqual(snapshot["binance_oi"]["delta"]["5m"]["estimated_notional_delta_usd"], 642_857.1428571428)
        self.assertAlmostEqual(snapshot["binance_oi"]["delta"]["15m"]["btc_delta"], 30.0)
        self.assertAlmostEqual(snapshot["binance_oi"]["delta"]["1h"]["btc_delta"], 120.0)
        self.assertAlmostEqual(snapshot["binance_cvd"]["taker_buy_usd"], 372100.0)
        self.assertAlmostEqual(snapshot["binance_cvd"]["taker_sell_usd"], 558000.0)
        self.assertAlmostEqual(snapshot["binance_cvd"]["delta_usd"], -185900.0)
        self.assertAlmostEqual(snapshot["binance_cvd"]["whale_over_100k"]["delta_usd"], -248000.0)
        self.assertEqual(snapshot["binance_whale_flow"]["whale_over_100k"]["delta_usd"], -248000.0)
        self.assertAlmostEqual(snapshot["binance_ofi_3m"]["taker_buy_usd"], 62100.0)
        self.assertAlmostEqual(snapshot["binance_ofi_3m"]["taker_sell_usd"], 310000.0)
        self.assertAlmostEqual(snapshot["binance_ofi_3m"]["sell_share"], 310000 / 372100)
        self.assertAlmostEqual(snapshot["binance_ofi_3m"]["price_change_pct"], -100 / 62100 * 100)
        self.assertEqual(snapshot["binance_ofi_3m"]["tier"], "sell_dominant")
        self.assertIn("collector_timings_ms", snapshot)
        self.assertEqual(snapshot["fetch_strategy"]["aggTrades"]["mode"], "parallel_minute_slices")
        self.assertEqual(client.calls["aggTrades"], 15)
        self.assertEqual(client.calls["market/books"], 1)
        self.assertEqual(client.calls["15m_klines"], 1)
        self.assertIsNotNone(snapshot["binance_momentum"]["rsi"]["15m"]["rsi14_latest_closed"])
        latest = snapshot["binance_momentum"]["latest_closed_15m"]
        self.assertAlmostEqual(latest["pct_change"], -0.1)
        self.assertAlmostEqual(latest["range_pct"], 0.6)
        self.assertAlmostEqual(latest["vpp_by_close"], 1_200_000_000.0)
        self.assertAlmostEqual(latest["vpp_by_range"], 200_000_000.0)
        self.assertGreater(snapshot["binance_momentum"]["vpp_baseline_24h"]["sample_count"], 20)
        self.assertGreater(len(snapshot["binance_momentum"]["liquidity_buckets_24h"]["buckets"]), 0)
        self.assertFalse(snapshot["binance_liquidations"]["available"])

    def test_liquidity_buckets_allocate_volume_across_kline_range(self) -> None:
        from trading_gateway.application.market.btcusdt.momentum import _volume_buckets

        buckets = _volume_buckets([{"low": 61990, "high": 62510, "quote_volume_usd": 900}], 250)
        by_bucket = {row["price_bucket"]: row["quote_volume_usd"] for row in buckets}

        self.assertEqual(sorted(by_bucket), [61750, 62000, 62250, 62500])
        self.assertAlmostEqual(sum(by_bucket.values()), 900.0)

    def test_btcusdt_snapshot_markdown_mentions_public_data_limits(self) -> None:
        from trading_gateway.application.market.btcusdt_snapshot import build_btcusdt_snapshot, render_markdown

        markdown = render_markdown(build_btcusdt_snapshot(FakeMarketClient(), now_ms=1_780_820_000_000, include_okx_account=False))

        self.assertIn("数据快照时间", markdown)
        self.assertIn("超级卖墙", markdown)
        self.assertIn("visible_only", markdown)
        self.assertIn("exchange_value_delta", markdown)
        self.assertIn("VPP_by_close", markdown)
        self.assertIn("无法公开精确获取", markdown)
        self.assertIn("采集", markdown)

    def test_agg_trade_parallel_fetch_dedupes_overlapping_same_ms_trades(self) -> None:
        from trading_gateway.application.market import btcusdt_snapshot as market

        client = FakeAggTradeClient()
        payload = market.fetch_agg_trades(client, 1_000, 121_000)

        self.assertEqual(sorted(payload["trades"]), [1, 2, 3])
        self.assertEqual(payload["fetch_strategy"]["mode"], "parallel_minute_slices")
        self.assertFalse(payload["fetch_strategy"]["fallback"])

    def test_agg_trade_fetch_falls_back_to_serial_on_parallel_failure(self) -> None:
        from trading_gateway.application.market import btcusdt_snapshot as market

        client = FakeAggTradeFallbackClient()
        payload = market.fetch_agg_trades(client, 1_000, 61_000)

        self.assertEqual(sorted(payload["trades"]), [10, 11])
        self.assertEqual(payload["fetch_strategy"]["mode"], "serial_from_id")
        self.assertTrue(payload["fetch_strategy"]["fallback"])
        self.assertIn("fallback_reason", payload["fetch_strategy"])

    def test_order_flow_imbalance_handles_empty_and_extreme_sides(self) -> None:
        from trading_gateway.application.market import btcusdt_snapshot as market

        empty = market.order_flow_imbalance_payload({}, 1_000, 181_000)
        sell = market.order_flow_imbalance_payload({1: {"a": 1, "T": 2_000, "p": "10", "q": "1", "m": True}}, 1_000, 181_000)
        buy = market.order_flow_imbalance_payload({2: {"a": 2, "T": 2_000, "p": "10", "q": "1", "m": False}}, 1_000, 181_000)

        self.assertEqual(empty["tier"], "empty")
        self.assertIsNone(empty["log_skew"])
        self.assertEqual(sell["tier"], "extreme_sell")
        self.assertEqual(sell["log_skew"], -10.0)
        self.assertEqual(buy["tier"], "extreme_buy")
        self.assertEqual(buy["log_skew"], 10.0)

    def test_market_btcusdt_cli_outputs_json_with_mock_snapshot(self) -> None:
        from trading_gateway.interfaces.cli import market

        raw = {
            "mode": "btcusdt_market_snapshot",
            "symbol": "BTCUSDT",
            "binance_oi": {"source": "Binance /fapi/v1/openInterest + /futures/data/openInterestHist"},
            "binance_cvd": {"source": "Binance /fapi/v1/aggTrades"},
            "binance_ofi_3m": {"tier": "neutral"},
            "binance_momentum": {"source": "Binance /fapi/v1/klines"},
            "data_sources": ["OKX public market API", "Binance USD-M Futures public API"],
        }
        with patch.object(market, "build_btcusdt_snapshot", return_value=raw) as build_snapshot:
            output = StringIO()
            with redirect_stdout(output):
                market.market_btcusdt(json_output=True, write=False, output=market.DEFAULT_BTCUSDT_REPORT, okx_account=True, remote=False)

        payload = json.loads(output.getvalue())
        self.assertEqual(payload["mode"], "btcusdt_market_snapshot")
        self.assertIn("derivatives_oi", payload)
        self.assertIn("derivatives_cvd", payload)
        self.assertIn("derivatives_ofi_3m", payload)
        serialized = json.dumps(payload)
        self.assertNotIn("binance_", serialized)
        self.assertNotIn("Binance", serialized)
        build_snapshot.assert_called_once_with(include_okx_account=True)

    def test_market_btcusdt_cli_writes_markdown_to_output_path(self) -> None:
        from trading_gateway.interfaces.cli import market

        with TemporaryDirectory() as temp_dir:
            target = Path(temp_dir) / "snapshot.md"
            with patch.object(market, "build_btcusdt_snapshot", return_value={"mode": "btcusdt_market_snapshot"}) as build_snapshot:
                with patch.object(market, "render_markdown", return_value="BTC markdown\n"):
                    with redirect_stdout(StringIO()):
                        market.market_btcusdt(json_output=False, llm=False, write=True, output=target, okx_account=False, remote=False)

            self.assertEqual(target.read_text(encoding="utf-8"), "BTC markdown\n")
            build_snapshot.assert_called_once_with(include_okx_account=False)

    def test_market_btcusdt_cli_prints_elapsed_timing(self) -> None:
        from trading_gateway.interfaces.cli import market

        with patch.object(market, "load_remote_btcusdt_snapshot", return_value={"mode": "btcusdt_market_snapshot", "remote_snapshot": {"fetch_ms": 0}}):
            with patch.object(market, "render_markdown") as render:
                render.side_effect = lambda snapshot: f"cli={snapshot['cli_elapsed_ms']} remote={snapshot['remote_snapshot']['fetch_ms']}\n"
                output = StringIO()
                with redirect_stdout(output):
                    market.market_btcusdt(json_output=False, llm=False, write=False, output=market.DEFAULT_BTCUSDT_REPORT, okx_account=False)

        self.assertIn("cli=", output.getvalue())
        self.assertIn("remote=0", output.getvalue())

    def test_liquidation_density_buckets_long_and_short(self) -> None:
        from trading_gateway.application.marketdata.btcusdt.liquidations import force_order_messages, liquidation_density, parse_force_order

        long_event = parse_force_order({"E": 1000, "o": {"s": "BTCUSDT", "S": "SELL", "ap": "62490", "z": "2"}})
        short_event = parse_force_order({"E": 2000, "o": {"s": "BTCUSDT", "S": "BUY", "ap": "62510", "z": "1"}})
        density = liquidation_density([long_event, short_event], bucket_usd=500, now_ms=3000, stream_status={"status": "connected"})

        self.assertEqual(density["kind"], "realized_liquidation_density_24h")
        self.assertAlmostEqual(density["long_liq_usd"], 124980.0)
        self.assertAlmostEqual(density["short_liq_usd"], 62510.0)
        self.assertEqual(density["buckets"][0]["price_bucket"], 62500)
        self.assertEqual(len(force_order_messages([{"o": {}}, {"o": {}}])), 2)

    def test_empty_liquidation_density_reports_stream_state(self) -> None:
        from trading_gateway.application.marketdata.btcusdt.liquidations import liquidation_density

        density = liquidation_density(
            [],
            bucket_usd=500,
            now_ms=3000,
            stream_status={"status": "connected", "total_force_order_messages": 10, "btc_force_order_messages": 0},
        )

        self.assertIn("WebSocket 已连接", density["note"])
        self.assertEqual(density["stream_status"]["total_force_order_messages"], 10)
        self.assertEqual(density["buckets"], [])

    def test_storage_prune_keeps_size_under_cap(self) -> None:
        from trading_gateway.application.marketdata.btcusdt.storage import BtcusdtMarketDataStore, storage_bytes

        with TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            store = BtcusdtMarketDataStore(root / "btcusdt.sqlite3")
            store.record_liquidation({"event_ms": 1, "symbol": "BTCUSDT", "side": "SELL", "liquidated_side": "long", "price": 60000, "qty_btc": 1, "notional_usd": 60000})
            (root / "large.tmp").write_text("x" * 4096, encoding="utf-8")
            report = store.prune(now_ms=2, liquidation_retention_ms=100000, summary_retention_ms=100000, max_bytes=1024, managed_dirs=[root])
            store.close()

            self.assertGreaterEqual(report["deleted_oldest_liquidation_rows"], 1)
            self.assertGreater(storage_bytes([root]), 0)

    def test_section_cache_reuses_previous_good_payload_on_refresh_error(self) -> None:
        from trading_gateway.application.marketdata.btcusdt.cache import SectionCache
        from trading_gateway.application.marketdata.btcusdt.storage import BtcusdtMarketDataStore

        with TemporaryDirectory() as temp_dir:
            store = BtcusdtMarketDataStore(Path(temp_dir) / "btcusdt.sqlite3")
            cache = SectionCache()
            first = cache.get_or_refresh(section="funding_basis", store=store, now_ms=1000, refresh_sec=1, collect=lambda: {"ok": True})
            second = cache.get_or_refresh(section="funding_basis", store=store, now_ms=3000, refresh_sec=1, collect=lambda: (_ for _ in ()).throw(RuntimeError("429")))
            store.close()

        self.assertEqual(first.status["status"], "fresh")
        self.assertEqual(second.payload, {"ok": True})
        self.assertEqual(second.status["status"], "stale")
        self.assertIn("429", second.status["refresh_error"])

    def test_section_cache_returns_default_on_initial_error(self) -> None:
        from trading_gateway.application.marketdata.btcusdt.cache import SectionCache
        from trading_gateway.application.marketdata.btcusdt.storage import BtcusdtMarketDataStore

        with TemporaryDirectory() as temp_dir:
            store = BtcusdtMarketDataStore(Path(temp_dir) / "btcusdt.sqlite3")
            result = SectionCache().get_or_refresh(section="public_snapshot", store=store, now_ms=1000, refresh_sec=60, collect=lambda: (_ for _ in ()).throw(RuntimeError("429")), default={"errors": {}})
            retry = SectionCache().get_or_refresh(section="public_snapshot", store=store, now_ms=1001, refresh_sec=60, collect=lambda: {"ok": True}, default={"errors": {}})
            store.close()

        self.assertEqual(result.payload, {"errors": {}})
        self.assertEqual(result.status["status"], "error")
        self.assertEqual(retry.payload, {"ok": True})

    def test_section_cache_refreshes_structurally_stale_payload(self) -> None:
        from trading_gateway.application.marketdata.btcusdt.cache import SectionCache
        from trading_gateway.application.marketdata.btcusdt.storage import BtcusdtMarketDataStore

        with TemporaryDirectory() as temp_dir:
            store = BtcusdtMarketDataStore(Path(temp_dir) / "btcusdt.sqlite3")
            cache = SectionCache()
            store.record_snapshot_section("public_snapshot", {"version": "old"}, 1000)
            result = cache.get_or_refresh(
                section="public_snapshot",
                store=store,
                now_ms=2000,
                refresh_sec=600,
                collect=lambda: {"version": "new"},
                accept=lambda payload: payload.get("version") == "new",
            )
            store.close()

        self.assertEqual(result.payload, {"version": "new"})
        self.assertEqual(result.status["status"], "fresh")

    def test_cloud_public_snapshot_requires_llm_feature_inputs(self) -> None:
        from trading_gateway.application.marketdata.btcusdt.daemon import _public_snapshot_ok

        old_shape = {"okx_market": {"last": 62000}, "binance_momentum": {"latest_closed_15m": {}}}
        new_shape = {
            "okx_market": {"last": 62000, "orderbook_geometry": {"weighted_gravity": 0}},
            "binance_ofi_3m": {"tier": "neutral"},
            "binance_momentum": {"vpp_baseline_24h": {"sample_count": 96}, "liquidity_buckets_24h": {"buckets": []}},
        }

        self.assertFalse(_public_snapshot_ok(old_shape))
        self.assertTrue(_public_snapshot_ok(new_shape))

    def test_okx_btc_account_collects_positions_open_orders_and_algos(self) -> None:
        from trading_gateway.application.market.okx_btc_account import collect_okx_btc_account, add_okx_account_counts

        account = add_okx_account_counts(collect_okx_btc_account(lambda: FakeOkxBtcAccountClient()))

        self.assertEqual(account["status"], "ok")
        self.assertEqual(account["counts"], {"positions": 1, "open_orders": 1, "algo_orders": 2})
        self.assertEqual(account["positions"][0]["side"], "short")
        self.assertEqual(account["positions"][0]["size"], "-0.05")
        self.assertEqual(account["open_orders"][0]["order_id"], "ord-1")
        self.assertEqual(account["algo_orders"]["conditional"][0]["sl_trigger"], "62545")
        self.assertEqual(account["algo_orders"]["oco"][0]["tp_trigger"], "61300")

    def test_btcusdt_markdown_includes_okx_account_section(self) -> None:
        from trading_gateway.application.market.btcusdt_snapshot import render_markdown

        markdown = render_markdown(
            {
                "snapshot_time_cst": "2026-06-08 10:00:00",
                "okx_account": {
                    "source": "OKX private account/trade API",
                    "status": "ok",
                    "timestamp_cst": "2026-06-08 10:00:01",
                    "counts": {"positions": 1, "open_orders": 1, "algo_orders": 1},
                    "positions": [{"side": "short", "size": "-0.05", "entry_price": "62465", "mark_price": "62490"}],
                    "open_orders": [{"order_id": "ord-1", "side": "sell", "price": "62500", "size": "0.05", "state": "live"}],
                    "algo_orders": {"conditional": [{"algo_id": "algo-1", "side": "buy", "sl_trigger": "62545", "size": "0.05", "state": "live"}]},
                },
            }
        )

        self.assertIn("OKX BTC持仓", markdown)
        self.assertIn("OKX BTC普通订单", markdown)
        self.assertIn("OKX BTC Algo", markdown)

    def test_btcusdt_markdown_includes_empty_liquidation_stream_status(self) -> None:
        from trading_gateway.application.market.btcusdt_snapshot import render_markdown

        markdown = render_markdown(
            {
                "snapshot_time_cst": "2026-06-08 18:00:00",
                "liquidation_density_24h": {
                    "source": "Binance !forceOrder@arr websocket filtered to BTCUSDT",
                    "note": "已发生强平密度，不是未来爆仓热力图。WebSocket 已连接，但 24h 存储窗口内尚未捕获 BTCUSDT 强平事件。",
                    "event_count": 0,
                    "long_liq_usd": 0,
                    "short_liq_usd": 0,
                    "generated_at_cst": "2026-06-08 18:00:00",
                    "stream_status": {"status": "connected", "uptime_sec": 60, "total_force_order_messages": 12, "btc_force_order_messages": 0},
                    "buckets": [],
                },
            }
        )

        self.assertIn("暂无 BTCUSDT 强平档位", markdown)
        self.assertIn("stream=connected", markdown)
