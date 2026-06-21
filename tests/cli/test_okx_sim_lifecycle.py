from __future__ import annotations

import json
import os
import tempfile
from contextlib import redirect_stdout
from io import StringIO
from unittest.mock import patch

from fakes import CliTestCase


class OkxSimLifecycleCliTest(CliTestCase):
    def test_doctor_reports_missing_demo_keys_without_trade(self) -> None:
        with tempfile.NamedTemporaryFile() as env_file:
            result = self.run_cli(
                "--env-file",
                env_file.name,
                "sim",
                "btc",
                "doctor",
                "--json",
                "--local",
                env=_blank_okx_env(),
            )

        self.assertEqual(result.returncode, 0, result.stderr)
        payload = json.loads(result.stdout)
        checks = {row["name"]: row for row in payload["checks"]}
        self.assertEqual(payload["status"], "not_ready")
        self.assertEqual(checks["okx_sim_credentials"]["status"], "missing")
        self.assertIn("OKX_SIM_PASSWORD", checks["okx_sim_credentials"]["missing_env"])
        self.assertIn(checks["okx_sim_sandbox_header"]["status"], {"ok", "error"})
        self.assertEqual(checks["okx_sim_private_auth"]["status"], "skipped")

    def test_doctor_sandbox_check_uses_okx_sim_header(self) -> None:
        from trading_gateway.interfaces.cli.commands.venues import doctor
        from trading_gateway.interfaces.cli.commands.venues import readiness

        class FakeClient:
            headers = {"x-simulated-trading": "1"}
            trading_gateway_sandbox = True

        with patch.object(readiness, "build_ccxt_client", return_value=FakeClient()):
            payload = doctor.okx_sim_doctor("btc", remote=False)

        checks = {row["name"]: row for row in payload["checks"]}
        self.assertEqual(checks["okx_sim_sandbox_header"]["status"], "ok")

    def test_doctor_reports_demo_auth_error_after_fallback_credentials(self) -> None:
        from trading_gateway.interfaces.cli.commands.venues import doctor
        from trading_gateway.interfaces.cli.commands.venues import readiness

        class HeaderClient:
            headers = {"x-simulated-trading": "1"}
            trading_gateway_sandbox = True

        class AuthFailClient(HeaderClient):
            def fetch_positions(self, symbols: list[str]) -> list[dict]:
                raise RuntimeError('okx {"msg":"APIKey does not match current environment.","code":"50101"}')

        env = {"OKX_API_KEY": "live-key", "OKX_API_SECRET": "live-secret", "OKX_PASSWORD": "live-pass"}
        with patch.dict(os.environ, env, clear=True), patch.object(readiness, "build_ccxt_client", side_effect=[HeaderClient(), AuthFailClient()]):
            payload = doctor.okx_sim_doctor("btc", remote=False)

        checks = {row["name"]: row for row in payload["checks"]}
        self.assertEqual(payload["status"], "not_ready")
        self.assertEqual(checks["okx_sim_credentials"]["credential_source"], "fallback")
        self.assertEqual(checks["okx_sim_private_auth"]["status"], "error")
        self.assertIn("50101", checks["okx_sim_private_auth"]["error"])

    def test_live_lifecycle_stops_when_readiness_fails(self) -> None:
        from trading_gateway.application.market.specs import get_venue_profile
        from trading_gateway.interfaces.cli.commands.venues import lifecycle

        readiness = {
            "status": "not_ready",
            "checks": [{"name": "okx_sim_private_auth", "status": "error", "error": "50101"}],
            "error": "okx_sim_private_auth: 50101",
        }
        with patch.object(lifecycle, "okx_sim_trade_readiness", return_value=readiness), patch.object(lifecycle, "_execute_lifecycle") as execute:
            payload = lifecycle.run_okx_sim_lifecycle(get_venue_profile("okx-sim"), "btc", "buy", 10, 1, "cross", True, "OKX_SIM_LIFECYCLE:BTC:BUY:QUOTE_10")

        execute.assert_not_called()
        self.assertEqual(payload["status"], "not_ready")
        self.assertEqual(payload["checks"][0]["name"], "okx_sim_private_auth")

    def test_doctor_is_not_available_under_okx_live_namespace(self) -> None:
        result = self.run_cli("okx", "doctor", "btc")

        self.assertNotEqual(result.returncode, 0)
        self.assertIn("OKX sim only", result.stderr)

    def test_dry_run_and_live_namespace_guard(self) -> None:
        sim = self.run_cli("sim", "btc", "test", "--usd", "10", "--json")
        live = self.run_cli("okx", "trade", "lifecycle", "btc", "--side", "buy", "--quote-usdt", "10", "--json")

        self.assertEqual(sim.returncode, 0, sim.stderr)
        payload = json.loads(sim.stdout)
        self.assertEqual(payload["mode"], "okx_sim_lifecycle_plan")
        self.assertEqual(payload["live_confirm_phrase"], "OKX_SIM_LIFECYCLE:BTC:BUY:QUOTE_10")
        self.assertNotEqual(live.returncode, 0)
        self.assertIn("OKX sim only", f"{live.stdout}\n{live.stderr}")
        self.assertNotIn("Invalid value for --confirm", live.stderr)
        self.assertNotIn("Traceback", live.stderr)

    def test_missing_demo_keys_is_not_confirm_error(self) -> None:
        result = self.run_cli(
            "--env-file",
            _empty_env_file_path(),
            "sim",
            "btc",
            "test",
            "--usd",
            "10",
            "--yes",
            "--json",
            env=_blank_okx_env(),
        )

        self.assertNotEqual(result.returncode, 0)
        self.assertIn("OKX_SIM_API_KEY", result.stderr)
        self.assertNotIn("Invalid value for --confirm", result.stderr)
        self.assertNotIn("Traceback", result.stderr)
        self.assertNotIn("AttributeError", result.stderr)

    def test_yes_shortcut_auto_supplies_sim_confirmation(self) -> None:
        from trading_gateway.interfaces.cli.commands.venues import app

        seen: list[tuple] = []

        def fake_run(*args):
            seen.append(args)
            return {"mode": "okx_sim_lifecycle", "status": "ok", "market": "spot", "symbol": "btc", "steps": []}

        with patch.object(app, "run_okx_sim_lifecycle", side_effect=fake_run):
            with redirect_stdout(StringIO()):
                app._quick_test_fn("btc")(usd=10, yes=True, json_output=True)

        self.assertEqual(seen[0][6], True)
        self.assertEqual(seen[0][7], "OKX_SIM_LIFECYCLE:BTC:BUY:QUOTE_10")
        self.assertEqual(seen[0][8], "spot")

    def test_old_okx_sim_namespace_returns_migration_hint(self) -> None:
        result = self.run_cli("okx-sim", "--help")

        self.assertEqual(result.returncode, 2)
        self.assertIn("No such command", result.stderr)


def _blank_okx_env() -> dict[str, str]:
    return {
        "OKX_API_KEY": "",
        "OKX_API_SECRET": "",
        "OKX_PASSWORD": "",
        "OKX_SIM_API_KEY": "",
        "OKX_SIM_API_SECRET": "",
        "OKX_SIM_PASSWORD": "",
    }


def _empty_env_file_path() -> str:
    handle = tempfile.NamedTemporaryFile(delete=False)
    handle.close()
    return handle.name
