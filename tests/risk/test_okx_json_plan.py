from __future__ import annotations

import json
import tempfile
from pathlib import Path
from unittest.mock import patch

import typer

from fakes import CliTestCase


def valid_plan() -> dict:
    return {
        "version": 1,
        "exchange": "okx",
        "instrument": "BTC-USDT-SWAP",
        "margin_mode": "isolated",
        "leverage": 20,
        "execution": {"mode": "one_shot", "replace_existing": False},
        "orders": [
            {
                "id": "short_breakdown",
                "side": "sell",
                "entry": {
                    "type": "stop_limit",
                    "trigger_price": "61680",
                    "order_price": "61640",
                    "trigger_price_type": "last",
                },
                "notional_usdt": "2800",
                "stop_loss": {"trigger_price": "61950", "order_price": "-1", "trigger_price_type": "mark"},
                "take_profits": [{"price": "60850", "size_pct": "60"}, {"price": "60250", "size_pct": "40"}],
            }
        ],
    }


def static_post_only_plan() -> dict:
    return {
        "version": 1,
        "exchange": "okx",
        "instrument": "BTC-USDT-SWAP",
        "margin_mode": "isolated",
        "leverage": 15,
        "execution": {"mode": "one_shot", "replace_existing": True},
        "orders": [
            {
                "id": "short_tp1",
                "side": "sell",
                "entry": {"type": "post_only", "price": "64310"},
                "notional_usdt": "1500",
                "stop_loss": {"trigger_price": "64445", "order_price": "-1", "trigger_price_type": "mark"},
                "take_profits": [{"price": "63650", "size_pct": "100"}],
            }
        ],
    }


class RiskOkxJsonPlanTest(CliTestCase):
    def test_stop_limit_multi_tp_compiles_to_split_trigger_brackets(self) -> None:
        from trading_gateway.application.risk.okx.json_plan import build_okx_json_plan

        plan = build_okx_json_plan(valid_plan(), contract_btc="0.01", lot_size="0.01", min_size="0.01")

        self.assertEqual(plan["mode"], "okx_json_order_plan")
        self.assertFalse(plan["mutual_exclusion_enforced"])
        self.assertEqual(len(plan["orders"]), 2)
        self.assertEqual([order["id"] for order in plan["orders"]], ["short_breakdown#1", "short_breakdown#2"])
        self.assertTrue(all(order["endpoint"] == "order_algo" for order in plan["orders"]))
        self.assertTrue(all(order["payload"]["ordType"] == "trigger" for order in plan["orders"]))
        self.assertEqual([order["payload"]["attachAlgoOrds"][0]["tpTriggerPx"] for order in plan["orders"]], ["60850", "60250"])
        self.assertLessEqual(float(plan["actual_notional_usdt"]), float(plan["target_notional_usdt"]))

    def test_rejects_unknown_fields(self) -> None:
        from trading_gateway.application.risk.okx.json_plan import build_okx_json_plan

        raw = valid_plan()
        raw["surprise"] = True
        with self.assertRaises(ValueError) as raised:
            build_okx_json_plan(raw)
        self.assertIn("unknown field", str(raised.exception))

    def test_rejects_inverted_short_geometry(self) -> None:
        from trading_gateway.application.risk.okx.json_plan import build_okx_json_plan

        raw = valid_plan()
        raw["orders"][0]["take_profits"][0]["price"] = "63000"
        with self.assertRaises(ValueError) as raised:
            build_okx_json_plan(raw)
        self.assertIn("short take_profit prices must be below entry", str(raised.exception))

    def test_hedge_mode_injects_pos_side(self) -> None:
        from trading_gateway.application.risk.okx.json_plan import build_okx_json_plan

        plan = build_okx_json_plan(valid_plan(), position_mode="long_short_mode")

        self.assertEqual(plan["orders"][0]["payload"]["posSide"], "short")
        self.assertEqual(plan["orders"][0]["payload"]["attachAlgoOrds"][0]["posSide"], "short")

    def test_cli_apply_dry_run_reads_json_file(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "plan.json"
            path.write_text(json.dumps(valid_plan()), encoding="utf-8")
            result = self.run_cli("risk", "apply", str(path), "--json")

        self.assertEqual(result.returncode, 0, result.stderr)
        payload = json.loads(result.stdout)
        self.assertEqual(payload["mode"], "okx_json_order_plan")
        self.assertIn("LIVE_JSON_PLAN:okx:BTC-USDT-SWAP", payload["confirm_phrase"])

    def test_cli_apply_dry_run_reads_stdin(self) -> None:
        import subprocess
        import sys

        from fakes import CLI, ROOT

        result = subprocess.run(
            [sys.executable, str(CLI), "risk", "apply", "-", "--json"],
            cwd=ROOT,
            input=json.dumps(valid_plan()),
            text=True,
            capture_output=True,
            check=False,
        )

        self.assertEqual(result.returncode, 0, result.stderr)
        payload = json.loads(result.stdout)
        self.assertEqual(payload["mode"], "okx_json_order_plan")

    def test_cli_apply_live_rejects_bad_confirm_before_exchange_access(self) -> None:
        from trading_gateway.interfaces.cli import risk

        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "plan.json"
            path.write_text(json.dumps(valid_plan()), encoding="utf-8")
            with patch.object(risk, "build_ccxt_client") as build_client:
                with self.assertRaises(typer.BadParameter):
                    risk.risk_apply(str(path), live=True, confirm="wrong", json_output=True)

        build_client.assert_not_called()

    def test_cli_guarded_apply_dry_run_reads_json_file(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "plan.json"
            path.write_text(json.dumps(static_post_only_plan()), encoding="utf-8")
            result = self.run_cli("risk", "guarded-apply", str(path), "--json")

        self.assertEqual(result.returncode, 0, result.stderr)
        payload = json.loads(result.stdout)
        self.assertEqual(payload["mode"], "okx_json_order_plan")
        self.assertIn("LIVE_JSON_PLAN:okx:BTC-USDT-SWAP", payload["confirm_phrase"])

    def test_cli_guarded_apply_live_rejects_bad_confirm_before_exchange_access(self) -> None:
        from trading_gateway.interfaces.cli import risk

        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "plan.json"
            path.write_text(json.dumps(static_post_only_plan()), encoding="utf-8")
            with patch.object(risk, "build_ccxt_client") as build_client:
                with self.assertRaises(typer.BadParameter):
                    risk.risk_guarded_apply(str(path), live=True, confirm="wrong", json_output=True)

        build_client.assert_not_called()

    def test_live_execution_recompiles_with_okx_specs(self) -> None:
        from trading_gateway.application.risk.okx.json_plan import place_okx_json_plan_orders, prepare_okx_json_plan_for_live

        class FakeClient:
            def __init__(self) -> None:
                self.payloads: list[dict] = []

            def publicGetPublicInstruments(self, params: dict) -> dict:
                return {"code": "0", "data": [{"ctVal": "0.01", "lotSz": "0.1", "minSz": "0.1", "tickSz": "0.1"}]}

            def privateGetAccountConfig(self) -> dict:
                return {"code": "0", "data": [{"posMode": "long_short_mode"}]}

            def privatePostAccountSetLeverage(self, payload: dict) -> dict:
                return {"code": "0", "data": [payload]}

            def privatePostTradeOrderAlgo(self, payload: dict) -> dict:
                self.payloads.append(payload)
                return {"code": "0", "data": [{"algoId": str(len(self.payloads)), "sCode": "0"}]}

            def privateGetTradeOrdersPending(self, params: dict) -> dict:
                return {"code": "0", "data": []}

            def privateGetTradeOrdersAlgoPending(self, params: dict) -> dict:
                return {"code": "0", "data": []}

            def privateGetAccountPositions(self, params: dict) -> dict:
                return {"code": "0", "data": []}

        client = FakeClient()
        result = place_okx_json_plan_orders(client, prepare_okx_json_plan_for_live(valid_plan()))

        self.assertEqual(result["status"], "submitted")
        self.assertEqual(client.payloads[0]["sz"], "2.7")
        self.assertEqual(client.payloads[0]["posSide"], "short")

    def test_guarded_execution_blocks_active_position_before_submit(self) -> None:
        from trading_gateway.application.risk.okx.json_plan import place_okx_guarded_json_plan_orders, prepare_okx_json_plan_for_live

        client = GuardedFakeClient(pre_positions=[{"instId": "BTC-USDT-SWAP", "pos": "-0.1"}])
        result = place_okx_guarded_json_plan_orders(client, prepare_okx_json_plan_for_live(static_post_only_plan()))

        self.assertEqual(result["status"], "blocked")
        self.assertEqual(result["final_outcome"], "precheck_blocked")
        self.assertEqual(result["violations"][0]["kind"], "positions")
        self.assertEqual(client.submitted_payloads, [])

    def test_guarded_execution_blocks_pending_order_before_submit(self) -> None:
        from trading_gateway.application.risk.okx.json_plan import place_okx_guarded_json_plan_orders, prepare_okx_json_plan_for_live

        client = GuardedFakeClient(pre_open_orders=[{"instId": "BTC-USDT-SWAP", "ordId": "old", "state": "live"}])
        result = place_okx_guarded_json_plan_orders(client, prepare_okx_json_plan_for_live(static_post_only_plan()))

        self.assertEqual(result["status"], "blocked")
        self.assertEqual(result["violations"][0]["kind"], "open_orders")
        self.assertEqual(client.submitted_payloads, [])

    def test_guarded_execution_blocks_algo_order_before_submit(self) -> None:
        from trading_gateway.application.risk.okx.json_plan import place_okx_guarded_json_plan_orders, prepare_okx_json_plan_for_live

        client = GuardedFakeClient(pre_algos={"conditional": [{"instId": "BTC-USDT-SWAP", "algoId": "old"}]})
        result = place_okx_guarded_json_plan_orders(client, prepare_okx_json_plan_for_live(static_post_only_plan()))

        self.assertEqual(result["status"], "blocked")
        self.assertEqual(result["violations"][0]["kind"], "algo_orders")
        self.assertEqual(client.submitted_payloads, [])

    def test_guarded_execution_reports_resting_when_submitted_order_is_pending(self) -> None:
        from trading_gateway.application.risk.okx.json_plan import place_okx_guarded_json_plan_orders, prepare_okx_json_plan_for_live

        client = GuardedFakeClient(post_open_orders=[{"instId": "BTC-USDT-SWAP", "ordId": "ord-1", "state": "live"}])
        result = place_okx_guarded_json_plan_orders(client, prepare_okx_json_plan_for_live(static_post_only_plan()))

        self.assertEqual(result["status"], "success")
        self.assertEqual(result["final_outcome"], "resting")
        self.assertEqual(result["submitted_order_ids"], ["ord-1"])

    def test_guarded_execution_reports_filled_when_position_exists_after_submit(self) -> None:
        from trading_gateway.application.risk.okx.json_plan import place_okx_guarded_json_plan_orders, prepare_okx_json_plan_for_live

        client = GuardedFakeClient(post_positions=[{"instId": "BTC-USDT-SWAP", "pos": "-2.33"}])
        result = place_okx_guarded_json_plan_orders(client, prepare_okx_json_plan_for_live(static_post_only_plan()))

        self.assertEqual(result["status"], "success")
        self.assertEqual(result["final_outcome"], "filled_or_partial")

    def test_guarded_execution_reports_not_resting_or_filled_when_post_only_disappears(self) -> None:
        from trading_gateway.application.risk.okx.json_plan import place_okx_guarded_json_plan_orders, prepare_okx_json_plan_for_live

        client = GuardedFakeClient()
        result = place_okx_guarded_json_plan_orders(client, prepare_okx_json_plan_for_live(static_post_only_plan()))

        self.assertEqual(result["status"], "failed")
        self.assertEqual(result["final_outcome"], "not_resting_or_filled")

    def test_old_complex_order_commands_are_not_registered(self) -> None:
        for command in ("grid-short", "static-grid", "static-notional", "trigger-oco"):
            result = self.run_cli("risk", command, "--help")
            self.assertNotEqual(result.returncode, 0)


class GuardedFakeClient:
    def __init__(
        self,
        *,
        pre_positions: list[dict] | None = None,
        pre_open_orders: list[dict] | None = None,
        pre_algos: dict[str, list[dict]] | None = None,
        post_positions: list[dict] | None = None,
        post_open_orders: list[dict] | None = None,
        post_algos: dict[str, list[dict]] | None = None,
    ) -> None:
        self.pre_positions = pre_positions or []
        self.pre_open_orders = pre_open_orders or []
        self.pre_algos = pre_algos or {}
        self.post_positions = post_positions or []
        self.post_open_orders = post_open_orders or []
        self.post_algos = post_algos or {}
        self.submitted_payloads: list[dict] = []
        self._submitted = False

    def publicGetPublicInstruments(self, params: dict) -> dict:
        return {"code": "0", "data": [{"ctVal": "0.01", "lotSz": "0.01", "minSz": "0.01", "tickSz": "0.1"}]}

    def privateGetAccountConfig(self) -> dict:
        return {"code": "0", "data": [{"posMode": "net_mode"}]}

    def privatePostAccountSetLeverage(self, payload: dict) -> dict:
        return {"code": "0", "data": [payload]}

    def privatePostTradeOrder(self, payload: dict) -> dict:
        self._submitted = True
        self.submitted_payloads.append(payload)
        return {"code": "0", "data": [{"ordId": "ord-1", "sCode": "0", "sMsg": "Order placed"}]}

    def privatePostTradeOrderAlgo(self, payload: dict) -> dict:
        self._submitted = True
        self.submitted_payloads.append(payload)
        return {"code": "0", "data": [{"ordId": "ord-1", "algoId": "algo-1", "sCode": "0"}]}

    def privateGetTradeOrdersPending(self, params: dict) -> dict:
        return {"code": "0", "data": self.post_open_orders if self._submitted else self.pre_open_orders}

    def privateGetTradeOrdersAlgoPending(self, params: dict) -> dict:
        rows = self.post_algos if self._submitted else self.pre_algos
        return {"code": "0", "data": rows.get(str(params.get("ordType")), [])}

    def privateGetAccountPositions(self, params: dict) -> dict:
        return {"code": "0", "data": self.post_positions if self._submitted else self.pre_positions}
