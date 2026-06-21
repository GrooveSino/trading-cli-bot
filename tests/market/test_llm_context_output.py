from __future__ import annotations

import json
from contextlib import redirect_stdout
from io import StringIO
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest.mock import patch

from fakes import CliTestCase, FakeMarketClient


class MarketLlmContextOutputTest(CliTestCase):
    def test_llm_renderer_contains_full_table_information_without_execution_advice(self) -> None:
        from trading_gateway.application.market import btcusdt_snapshot
        from trading_gateway.application.market.btcusdt.public_view import market_snapshot_public_view
        from trading_gateway.application.market.multi import render_llm_context

        snapshot = btcusdt_snapshot.build_btcusdt_snapshot(FakeMarketClient(), now_ms=1_780_820_000_000, include_okx_account=False)
        text = render_llm_context(market_snapshot_public_view(snapshot))

        self.assertIn("# 市场快照", text)
        self.assertIn("## 快照元信息", text)
        self.assertIn("## 核心读数", text)
        self.assertIn("## LLM 特征", text)
        self.assertIn("market_state=", text)
        self.assertIn("3m OFI", text)
        self.assertIn("## 账户 Overlay", text)
        self.assertIn("## 数据质量", text)
        self.assertIn("## 读数提醒", text)
        self.assertIn("0.5% Ask visible", text)
        self.assertIn("1.0% Bid/Ask visible", text)
        self.assertIn("关键价位", text)
        self.assertIn("完整 LLM Feature JSON", text)
        self.assertIn("btcusdt_llm_features.v1", text)
        self.assertIn("exchange_value_delta", text)
        for forbidden in ("开仓价", "杠杆建议", "止盈", "止损"):
            self.assertNotIn(forbidden, text)

    def test_legacy_market_defaults_to_compact_and_table_restores_full_table(self) -> None:
        compact = self.run_cli("market", "btcusdt", "--local", "--no-okx-account")
        table = self.run_cli("market", "btcusdt", "--local", "--no-okx-account", "--table")

        self.assertEqual(compact.returncode, 0, compact.stderr)
        self.assertEqual(table.returncode, 0, table.stderr)
        self.assertIn("# 市场快照", compact.stdout)
        self.assertIn("## 核心读数", compact.stdout)
        self.assertIn("0.5% Ask visible", compact.stdout)
        self.assertIn("完整 LLM Feature JSON", compact.stdout)
        self.assertNotIn("| 维度 | 指标 | 数值 |", compact.stdout)
        self.assertIn("| 维度 | 指标 | 数值 |", table.stdout)

    def test_namespaced_market_json_remains_machine_payload(self) -> None:
        result = self.run_cli("okx", "market", "btc", "--json", "--local", "--no-account")

        self.assertEqual(result.returncode, 0, result.stderr)
        payload = json.loads(_strip_tbot_banner(result.stdout))
        self.assertEqual(payload["mode"], "market_snapshot")
        self.assertEqual(payload["venue_profile"]["id"], "okx-live")
        self.assertIn("market_source", payload)
        self.assertIn("errors", payload)
        self.assertIn("llm_feature_vectors", payload)

    def test_multi_symbol_bundle_renderers_keep_full_symbol_payloads(self) -> None:
        from trading_gateway.application.market.multi.bundle import build_market_bundle, render_bundle_llm_context, render_bundle_table_markdown

        def load(_venue: str, symbol: str) -> dict:
            return _fake_symbol_snapshot(symbol)

        bundle = build_market_bundle("okx-live", ["btc", "eth"], load)
        llm_text = render_bundle_llm_context(bundle)
        table_text = render_bundle_table_markdown(bundle)

        self.assertEqual(bundle["mode"], "multi_symbol_market_snapshot")
        self.assertEqual(sorted(bundle["snapshots"]), ["btc", "eth"])
        self.assertIn("cross_symbol_features", bundle)
        self.assertIn("# 市场快照：OKX Live BTC + ETH", llm_text)
        self.assertIn("## BTC 完整快照", llm_text)
        self.assertIn("## ETH 完整快照", llm_text)
        self.assertIn("组合 Feature JSON", llm_text)
        self.assertIn("| 维度 | 指标 | 数值 |", table_text)

    def test_namespaced_market_accepts_btc_eth_bundle_json(self) -> None:
        result = self.run_cli("okx", "market", "btc", "eth", "--json", "--local", "--no-account")

        self.assertEqual(result.returncode, 0, result.stderr)
        payload = json.loads(_strip_tbot_banner(result.stdout))
        self.assertEqual(payload["mode"], "multi_symbol_market_snapshot")
        self.assertEqual(payload["symbols"], ["btc", "eth"])
        self.assertEqual(sorted(payload["snapshots"]), ["btc", "eth"])
        self.assertIn("cross_symbol_features", payload)

    def test_legacy_write_uses_selected_llm_or_table_mode(self) -> None:
        from trading_gateway.interfaces.cli import market

        snapshot = {"mode": "btcusdt_market_snapshot", "snapshot_time_cst": "2026-06-08 10:00:00"}
        with TemporaryDirectory() as temp_dir:
            llm_path = Path(temp_dir) / "llm.md"
            table_path = Path(temp_dir) / "table.md"
            with patch.object(market, "load_remote_btcusdt_snapshot", return_value=snapshot):
                with redirect_stdout(StringIO()):
                    market.market_btcusdt(json_output=False, llm=True, write=True, output=llm_path, okx_account=False)
                with redirect_stdout(StringIO()):
                    market.market_btcusdt(json_output=False, llm=False, write=True, output=table_path, okx_account=False)

            self.assertIn("# 市场快照", llm_path.read_text(encoding="utf-8"))
            self.assertIn("| 维度 | 指标 | 数值 |", table_path.read_text(encoding="utf-8"))


def _strip_tbot_banner(stdout: str) -> str:
    lines = stdout.splitlines()
    if lines and lines[0].startswith("tbot:"):
        return "\n".join(lines[1:])
    return stdout


def _fake_symbol_snapshot(symbol: str) -> dict:
    upper = symbol.upper()
    return {
        "mode": "market_snapshot",
        "venue_profile": {"id": "okx-live", "exchange": "okx", "account_mode": "live", "display_name": "OKX Live"},
        "symbol": symbol,
        "snapshot_time_cst": "2026-06-14 03:00:00",
        "market_source": {"last": 60000 if symbol == "btc" else 3000, "best_bid": 1, "best_ask": 2, "source": "test"},
        "global_derivatives": {
            "oi": {"delta": {"5m": {"btc_delta": 1}, "15m": {"btc_delta": 2}, "1h": {"btc_delta": 3}}},
            "cvd": {"delta_usd": 100 if symbol == "btc" else -50},
            "ofi_3m": {"delta_usd": 40 if symbol == "btc" else -20, "tier": "neutral"},
            "momentum": {"rsi": {"15m": {"rsi14_live": 55}}},
            "funding_basis": {"derivatives": {"basis_bps": -1.2}},
        },
        "account_overlay": {"status": "skipped", "counts": {"positions": 0, "open_orders": 0, "algo_orders": 0}},
        "llm_feature_vectors": {
            "schema_version": "btcusdt_llm_features.v1",
            "market_state": f"TEST_{upper}",
            "features": {"weighted_orderbook_gravity": {"value": 0.1}},
        },
        "errors": {},
    }
