from __future__ import annotations

import json
import os
from pathlib import Path
from unittest.mock import patch

import typer

from fakes import CONFIG, CliTestCase


class CliRegistrationTest(CliTestCase):
    def test_default_config_uses_local_env_file(self) -> None:
        from trading_gateway.app.config import load_gateway_config

        config = load_gateway_config(CONFIG)

        self.assertEqual(Path(config.dotenv_path), Path(".env"))

    def test_missing_route_universe_uses_embedded_btc_eth_fallback(self) -> None:
        from trading_gateway.domain.route_universe import validate_trading_symbol

        validation = validate_trading_symbol("BTC/USDT", "perp", "okx", Path("var/data/symbol_route_universe.json"))

        self.assertTrue(validation["supported"])
        self.assertEqual(validation["symbol"], "BTC/USDT")

    def test_trade_plan_with_static_price_is_dry_run_json(self) -> None:
        result = self.run_cli(
            "trade",
            "plan",
            "--exchange",
            "okx",
            "--market",
            "perp",
            "--symbol",
            "BTC/USDT",
            "--side",
            "buy",
            "--quote-usdt",
            "10",
            "--last-price",
            "70000",
            "--json",
        )

        self.assertEqual(result.returncode, 0, result.stderr)
        payload = json.loads(result.stdout)
        self.assertEqual(payload["mode"], "plan")
        self.assertEqual(payload["exchange"], "okx")
        self.assertEqual(payload["market"], "perp")
        self.assertEqual(payload["symbol"], "BTC/USDT")
        self.assertIn("live_confirm_phrase", payload)
        self.assertEqual(payload["live_confirm_phrase"], "LIVE_ORDER:OKX_LIVE:perp:BTC/USDT:10")

    def test_help_output_does_not_print_received_banner(self) -> None:
        result = self.run_cli("market", "btcusdt", "--help")

        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn("Usage: tbot market btcusdt", result.stdout)
        self.assertIn("--table", result.stdout)
        self.assertNotIn("tbot: received", result.stderr)

    def test_okx_sim_lab_plan_marks_account_mode_in_confirm_phrase(self) -> None:
        result = self.run_cli(
            "plan",
            "okx",
            "perp",
            "open-long",
            "BTC/USDT",
            "10",
            "--account-mode",
            "sim",
            "--last-price",
            "70000",
            "--json",
        )

        self.assertEqual(result.returncode, 0, result.stderr)
        payload = json.loads(result.stdout)
        plan = payload["plan"]
        self.assertEqual(plan["exchange"], "okx")
        self.assertEqual(plan["account_mode"], "sim")
        self.assertIn("LIVE_OKX_SIM_PERP_OPEN_LONG", plan["confirm_phrase"])

    def test_okx_sim_lab_plan_next_command_keeps_account_mode(self) -> None:
        from trading_gateway.interfaces.cli.presenters.single_leg_helpers import next_command

        plan = {
            "exchange": "okx",
            "market": "perp",
            "action": "open-long",
            "canonical_symbol": "BTC/USDT",
            "requested_quote_usdt": 10,
            "account_mode": "sim",
            "confirm_phrase": "LIVE_OKX_SIM_PERP_OPEN_LONG:BTCUSDT:QUOTE_10",
        }

        self.assertIn("--account-mode sim", next_command(plan))

    def test_okx_sim_trade_plan_marks_account_mode_in_confirm_phrase(self) -> None:
        result = self.run_cli(
            "trade",
            "plan",
            "--exchange",
            "okx",
            "--market",
            "perp",
            "--symbol",
            "BTC/USDT",
            "--side",
            "buy",
            "--quote-usdt",
            "10",
            "--account-mode",
            "sim",
            "--last-price",
            "70000",
            "--json",
        )

        self.assertEqual(result.returncode, 0, result.stderr)
        payload = json.loads(result.stdout)
        self.assertEqual(payload["account_mode"], "sim")
        self.assertEqual(payload["live_confirm_phrase"], "LIVE_ORDER:OKX_SIM:perp:BTC/USDT:10")

    def test_live_and_sim_shortcuts_are_registered_without_help_banner(self) -> None:
        for args, expected in [
            (("live", "--help"), "OKX live shortcuts"),
            (("live", "btc", "--help"), "OKX Live BTC shortcut"),
            (("sim", "--help"), "OKX demo shortcuts"),
            (("sim", "btc", "--help"), "OKX Sim BTC shortcut"),
            (("sim", "btc", "test", "--help"), "--yes"),
        ]:
            result = self.run_cli(*args)
            self.assertEqual(result.returncode, 0, result.stderr)
            self.assertIn(expected, result.stdout)
            self.assertNotIn("tbot: received", result.stderr)

    def test_namespaced_trade_plans_mark_okx_live_and_sim(self) -> None:
        okx = self.run_cli("okx", "trade", "plan", "btc", "--side", "buy", "--quote-usdt", "10", "--last-price", "70000", "--json")

        self.assertEqual(okx.returncode, 0, okx.stderr)
        self.assertIn("OKX_LIVE", json.loads(okx.stdout)["live_confirm_phrase"])

    def test_okx_sim_verbose_namespace_is_removed(self) -> None:
        result = self.run_cli("okx-sim", "--help")

        self.assertEqual(result.returncode, 2)
        self.assertIn("No such command", result.stderr)

    def test_live_smoke_rejects_bad_confirm_before_exchange_access(self) -> None:
        result = self.run_cli(
            "trade",
            "smoke",
            "--exchange",
            "okx",
            "--market",
            "perp",
            "--symbol",
            "BTC/USDT",
            "--side",
            "buy",
            "--quote-usdt",
            "10",
            "--last-price",
            "70000",
            "--live",
            "--confirm",
            "wrong",
            "--json",
        )

        combined_output = f"{result.stdout}\n{result.stderr}"
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("confirmation mismatch", combined_output)
        self.assertNotIn("IP whitelist", combined_output)
        self.assertNotIn("www.okx.com", combined_output)

    def test_positions_reports_private_api_failure_instead_of_empty_positions(self) -> None:
        from trading_gateway.interfaces.cli import wallet

        snapshot = {
            "exchange": "okx",
            "status": "partial_error",
            "warnings": ["okx perp PermissionDenied: IP whitelist"],
            "perp": {
                "positions": [],
                "status": "okx perp PermissionDenied: IP whitelist",
            },
        }

        with patch.object(wallet, "fetch_exchange_snapshot", return_value=snapshot):
            with self.assertRaises(typer.BadParameter) as raised:
                wallet.wallet_positions("okx")

        self.assertIn("IP whitelist", str(raised.exception))

    def test_orders_reports_redacted_exchange_failure_without_traceback(self) -> None:
        from trading_gateway.interfaces.cli import wallet

        error = RuntimeError("okx PermissionDenied: API key secret-api-key is not in IP whitelist")

        with patch.dict(os.environ, {"OKX_API_KEY": "secret-api-key"}):
            with patch.object(wallet, "_wallet_snapshot", side_effect=error):
                with self.assertRaises(typer.BadParameter) as raised:
                    wallet.wallet_orders("okx", "perp", "BTC/USDT")

        text = str(raised.exception)
        self.assertIn("IP whitelist", text)
        self.assertIn("<redacted>", text)
        self.assertNotIn("secret-api-key", text)

    def test_wallet_summary_defaults_to_okx_only(self) -> None:
        from trading_gateway.application.wallet import summary_runner

        seen: list[tuple[str, bool]] = []

        def fake_fetch(exchange: str, include_positions: bool = False) -> list[dict]:
            seen.append((exchange, include_positions))
            return []

        with patch.object(summary_runner, "fetch_summary_exchange", side_effect=fake_fetch):
            with patch.object(summary_runner, "build_summary_payload", return_value={"exchanges": []}):
                with patch.object(summary_runner, "print_json"):
                    summary_runner.print_wallet_summary(None, json_output=True, progress_enabled=False)

        self.assertEqual(seen, [("okx", False)])

    def test_binance_private_account_commands_are_disabled_without_client_access(self) -> None:
        from trading_gateway.interfaces.cli import wallet

        with patch.object(wallet, "build_ccxt_client") as build_client:
            with self.assertRaises(typer.BadParameter) as raised:
                wallet.wallet_positions("binance", "BTC/USDT:USDT")

        build_client.assert_not_called()
        self.assertIn("Binance private account features are disabled", str(raised.exception))

    def test_okx_sim_private_account_can_be_selected(self) -> None:
        from trading_gateway.interfaces.cli import wallet

        with patch.object(wallet, "fetch_exchange_snapshot", return_value={"perp": {"positions": [], "status": "ok"}, "warnings": []}) as fetch:
            wallet.wallet_positions("okx", "BTC/USDT:USDT", account_mode="sim")

        fetch.assert_called_once()
        self.assertEqual(fetch.call_args.kwargs["account_mode"], "sim")

    def test_gate_private_account_is_blocked_without_client_access(self) -> None:
        from trading_gateway.interfaces.cli import wallet

        with patch.object(wallet, "build_ccxt_client") as build_client:
            with self.assertRaises(typer.BadParameter) as raised:
                wallet.wallet_positions("gate", "BTC/USDT:USDT", account_mode="sim")

        build_client.assert_not_called()
        self.assertIn("Gate private account features are disabled", str(raised.exception))

    def test_okx_sim_factory_enables_ccxt_sandbox_with_sim_env(self) -> None:
        from trading_gateway.infrastructure.exchange import factory

        events: list[object] = []

        class FakeOkx:
            def __init__(self, config: dict) -> None:
                events.append(config)

            def set_sandbox_mode(self, enabled: bool) -> None:
                events.append(("sandbox", enabled))

        fake_ccxt = type("FakeCcxt", (), {"okx": FakeOkx})
        env = {"OKX_SIM_API_KEY": "sim-key", "OKX_SIM_API_SECRET": "sim-secret", "OKX_SIM_PASSWORD": "sim-pass"}
        with patch.object(factory, "_import_ccxt", return_value=fake_ccxt), patch.dict(os.environ, env, clear=False):
            client = factory.build_ccxt_client("okx", "swap", require_private=True, account_mode="sim")

        self.assertIsInstance(client, FakeOkx)
        self.assertEqual(events[0]["apiKey"], "sim-key")
        self.assertEqual(events[0]["secret"], "sim-secret")
        self.assertEqual(events[0]["password"], "sim-pass")
        self.assertEqual(events[1], ("sandbox", True))
        self.assertEqual(client.trading_gateway_account_mode, "sim")

    def test_okx_sim_missing_credentials_names_sim_and_fallback_env_vars(self) -> None:
        from trading_gateway.infrastructure.exchange import factory

        with patch.dict(os.environ, {}, clear=True):
            with self.assertRaises(ValueError) as raised:
                factory.build_ccxt_client("okx", "swap", require_private=True, account_mode="sim")

        text = str(raised.exception)
        self.assertIn("OKX_SIM_API_KEY", text)
        self.assertIn("OKX_SIM_API_SECRET", text)
        self.assertIn("OKX_API_KEY", text)
        self.assertIn("OKX_API_SECRET", text)

    def test_okx_sim_partial_sim_credentials_do_not_mix_live_fallback(self) -> None:
        from trading_gateway.infrastructure.exchange import factory

        env = {
            "OKX_SIM_API_KEY": "sim-key",
            "OKX_SIM_API_SECRET": "sim-secret",
            "OKX_API_KEY": "live-key",
            "OKX_API_SECRET": "live-secret",
            "OKX_PASSWORD": "live-pass",
        }
        with patch.dict(os.environ, env, clear=True):
            with self.assertRaises(ValueError) as raised:
                factory.build_ccxt_client("okx", "swap", require_private=True, account_mode="sim")

        text = str(raised.exception)
        self.assertIn("OKX_SIM_PASSWORD", text)
        self.assertNotIn("OKX_API_KEY", text)

    def test_daemon_okx_sim_routes_enable_with_okx_live_env_fallback(self) -> None:
        from trading_gateway.interfaces.daemon import runtime

        with patch.dict(os.environ, {"OKX_API_KEY": "ok", "OKX_API_SECRET": "sec", "OKX_PASSWORD": "pass"}, clear=True):
            daemon = runtime.DaemonRuntime()
            routes = {row["route"] for row in daemon.status_payload()["routes"]}
            daemon.stop()

        self.assertIn("okx:spot:live", routes)
        self.assertIn("okx:spot:sim", routes)
        self.assertIn("okx:perp:sim", routes)

    def test_daemon_okx_sim_routes_enable_with_sim_credentials(self) -> None:
        from trading_gateway.interfaces.daemon import runtime

        env = {
            "OKX_API_KEY": "ok",
            "OKX_API_SECRET": "sec",
            "OKX_PASSWORD": "pass",
            "OKX_SIM_API_KEY": "demo",
            "OKX_SIM_API_SECRET": "demo-sec",
            "OKX_SIM_PASSWORD": "demo-pass",
        }
        with patch.dict(os.environ, env, clear=True):
            daemon = runtime.DaemonRuntime()
            routes = {row["route"] for row in daemon.status_payload()["routes"]}
            daemon.stop()

        self.assertIn("okx:spot:sim", routes)
        self.assertIn("okx:perp:sim", routes)
