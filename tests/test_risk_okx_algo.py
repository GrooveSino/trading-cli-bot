from __future__ import annotations

import json
from contextlib import redirect_stdout
from io import StringIO
from unittest.mock import patch

import typer

from fakes import CliTestCase


class RiskOkxAlgoTest(CliTestCase):
    def test_okx_bracket_plan_for_long_uses_sell_reduce_only_algos(self) -> None:
        from trading_gateway.application.risk.okx_algo import build_okx_bracket_plan

        plan = build_okx_bracket_plan("okx", "BTC-USDT-SWAP", "long", 2.56, take_profit=76000, stop_loss=72900)

        self.assertEqual(plan["mode"], "plan")
        self.assertEqual(plan["confirm_phrase"], "LIVE_BRACKET:okx:BTC-USDT-SWAP:long:2.56:TP_76000:SL_72900")
        self.assertEqual([order["kind"] for order in plan["algo_orders"]], ["take_profit", "stop_loss"])
        for order in plan["algo_orders"]:
            payload = order["payload"]
            self.assertEqual(payload["instId"], "BTC-USDT-SWAP")
            self.assertEqual(payload["tdMode"], "cross")
            self.assertEqual(payload["side"], "sell")
            self.assertEqual(payload["sz"], "2.56")
            self.assertEqual(payload["ordType"], "conditional")
            self.assertEqual(payload["reduceOnly"], "true")
        self.assertEqual(plan["algo_orders"][0]["payload"]["tpTriggerPx"], "76000")
        self.assertEqual(plan["algo_orders"][0]["payload"]["tpOrdPx"], "-1")
        self.assertEqual(plan["algo_orders"][1]["payload"]["slTriggerPx"], "72900")
        self.assertEqual(plan["algo_orders"][1]["payload"]["slOrdPx"], "-1")

    def test_okx_bracket_plan_for_short_uses_buy_reduce_only_algos(self) -> None:
        from trading_gateway.application.risk.okx_algo import build_okx_bracket_plan

        plan = build_okx_bracket_plan("okx", "INTC-USDT-SWAP", "short", 0.3, take_profit=108.16, stop_loss=121.13)

        self.assertEqual(plan["confirm_phrase"], "LIVE_BRACKET:okx:INTC-USDT-SWAP:short:0.3:TP_108.16:SL_121.13")
        self.assertEqual({order["payload"]["side"] for order in plan["algo_orders"]}, {"buy"})

    def test_okx_bracket_plan_requires_take_profit_or_stop_loss(self) -> None:
        from trading_gateway.application.risk.okx_algo import build_okx_bracket_plan

        with self.assertRaises(ValueError):
            build_okx_bracket_plan("okx", "BTC-USDT-SWAP", "long", 2.56)

    def test_risk_plan_cli_outputs_confirmation_phrase(self) -> None:
        result = self.run_cli(
            "risk",
            "plan",
            "okx",
            "BTC-USDT-SWAP",
            "long",
            "2.56",
            "--take-profit",
            "76000",
            "--stop-loss",
            "72900",
            "--json",
        )

        self.assertEqual(result.returncode, 0, result.stderr)
        payload = json.loads(result.stdout)
        self.assertEqual(payload["confirm_phrase"], "LIVE_BRACKET:okx:BTC-USDT-SWAP:long:2.56:TP_76000:SL_72900")
        self.assertEqual(len(payload["algo_orders"]), 2)

    def test_risk_bracket_rejects_bad_live_confirm_before_exchange_access(self) -> None:
        result = self.run_cli(
            "risk",
            "bracket",
            "okx",
            "BTC-USDT-SWAP",
            "long",
            "2.56",
            "--take-profit",
            "76000",
            "--stop-loss",
            "72900",
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

    def test_risk_bracket_live_places_two_okx_algo_orders_after_confirm(self) -> None:
        from trading_gateway.interfaces.cli import risk

        class FakeClient:
            def __init__(self) -> None:
                self.payloads: list[dict] = []
                self.closed = False

            def privatePostTradeOrderAlgo(self, payload: dict) -> dict:
                self.payloads.append(payload)
                return {"code": "0", "data": [{"algoId": str(len(self.payloads))}]}

            def close(self) -> None:
                self.closed = True

        client = FakeClient()
        output = StringIO()

        with patch.object(risk, "build_ccxt_client", return_value=client) as build_client:
            with redirect_stdout(output):
                risk.risk_bracket(
                    "okx",
                    "BTC-USDT-SWAP",
                    "long",
                    2.56,
                    take_profit=76000,
                    stop_loss=72900,
                    live=True,
                    confirm="LIVE_BRACKET:okx:BTC-USDT-SWAP:long:2.56:TP_76000:SL_72900",
                    json_output=True,
                )

        build_client.assert_called_once_with("okx", "swap", require_private=True)
        self.assertEqual(json.loads(output.getvalue())["status"], "live")
        self.assertTrue(client.closed)
        self.assertEqual(len(client.payloads), 2)
        self.assertEqual(client.payloads[0]["tpTriggerPx"], "76000")
        self.assertEqual(client.payloads[1]["slTriggerPx"], "72900")

    def test_risk_cancel_rejects_bad_confirm_before_exchange_access(self) -> None:
        from trading_gateway.interfaces.cli import risk

        with patch.object(risk, "build_ccxt_client") as build_client:
            with self.assertRaises(typer.BadParameter) as raised:
                risk.risk_cancel(
                    "okx",
                    "BTC-USDT-SWAP",
                    ["123", "456"],
                    confirm="wrong",
                    json_output=True,
                )

        build_client.assert_not_called()
        self.assertIn("LIVE_CANCEL_ALGOS:okx:BTC-USDT-SWAP:123,456", str(raised.exception))

    def test_okx_grid_short_plan_builds_split_attach_algos(self) -> None:
        from trading_gateway.application.risk.okx_algo import build_okx_grid_short_plan

        plan = build_okx_grid_short_plan(
            "okx",
            "BTCUSDT",
            232.24,
            entries=[(61859, 40), (62015, 60)],
            take_profits=[(61200, 50), (60800, 30), (59600, 20)],
            stop_loss=62185,
            leverage=15,
            margin_mode="isolated",
        )

        self.assertEqual(plan["mode"], "grid_short_plan")
        self.assertEqual(plan["symbol"], "BTC-USDT-SWAP")
        self.assertEqual(plan["orders"][0]["size"], "2.25")
        self.assertEqual(plan["orders"][1]["size"], "3.37")
        first_attach = plan["orders"][0]["payload"]["attachAlgoOrds"]
        self.assertEqual([row.get("tpTriggerPx") for row in first_attach[:3]], ["61200", "60800", "59600"])
        self.assertEqual([row.get("sz") for row in first_attach[:3]], ["1.13", "0.68", "0.44"])
        self.assertEqual(first_attach[3]["slTriggerPx"], "62185")
        self.assertEqual(first_attach[3]["slTriggerPxType"], "mark")
        self.assertIn("LIVE_GRID_SHORT:okx:BTC-USDT-SWAP", plan["confirm_phrase"])

    def test_risk_grid_short_cli_is_no_longer_public(self) -> None:
        self.assertFalse(hasattr(__import__("trading_gateway.interfaces.cli.risk", fromlist=["risk_grid_short"]), "risk_grid_short"))

    def test_okx_static_grid_plan_builds_per_entry_static_payloads(self) -> None:
        from trading_gateway.application.risk.okx_algo import build_okx_static_grid_plan

        plan = build_okx_static_grid_plan(
            "okx",
            "BTCUSDT",
            230,
            side="sell",
            entries=[
                {
                    "price": 61520,
                    "allocation_pct": 40,
                    "stop_loss": 61785,
                    "take_profits": [
                        {"price": 60950, "allocation_pct": 50},
                        {"price": 60500, "allocation_pct": 30},
                        {"price": 59600, "allocation_pct": 20},
                    ],
                },
                {
                    "price": 61850,
                    "allocation_pct": 60,
                    "stop_loss": 62110,
                    "take_profits": [
                        {"price": 61100, "allocation_pct": 50},
                        {"price": 60500, "allocation_pct": 30},
                        {"price": 59600, "allocation_pct": 20},
                    ],
                },
            ],
            leverage=15,
            margin_mode="isolated",
            order_type="post-only",
        )

        self.assertEqual(plan["mode"], "static_grid_plan")
        self.assertEqual(plan["symbol"], "BTC-USDT-SWAP")
        self.assertEqual(plan["side"], "sell")
        self.assertEqual(plan["order_type"], "post_only")
        self.assertEqual(plan["entry_allocation_total_pct"], "100")
        self.assertEqual(plan["orders"][0]["payload"]["ordType"], "post_only")
        self.assertEqual(plan["orders"][0]["payload"]["px"], "61520")
        self.assertEqual(plan["orders"][0]["payload"]["sz"], "2.24")
        self.assertEqual(plan["orders"][1]["payload"]["px"], "61850")
        self.assertEqual(plan["orders"][1]["payload"]["sz"], "3.34")

        first_attach = plan["orders"][0]["payload"]["attachAlgoOrds"]
        second_attach = plan["orders"][1]["payload"]["attachAlgoOrds"]
        self.assertEqual([row.get("tpTriggerPx") for row in first_attach[:3]], ["60950", "60500", "59600"])
        self.assertEqual([row.get("sz") for row in first_attach[:3]], ["1.12", "0.67", "0.45"])
        self.assertEqual(first_attach[3]["slTriggerPx"], "61785")
        self.assertEqual(first_attach[3]["slTriggerPxType"], "mark")
        self.assertEqual([row.get("tpTriggerPx") for row in second_attach[:3]], ["61100", "60500", "59600"])
        self.assertEqual(second_attach[3]["slTriggerPx"], "62110")
        self.assertIn("LIVE_STATIC_GRID:okx:BTC-USDT-SWAP:sell", plan["confirm_phrase"])

    def test_risk_static_grid_cli_is_no_longer_public(self) -> None:
        self.assertFalse(hasattr(__import__("trading_gateway.interfaces.cli.risk", fromlist=["risk_static_grid"]), "risk_static_grid"))

    def test_okx_static_notional_stop_limit_uses_managed_exit_plan(self) -> None:
        from trading_gateway.application.risk.okx_algo import build_okx_static_notional_plan

        plan = build_okx_static_notional_plan(
            "okx",
            "BTCUSDT",
            side="sell",
            entries=[
                {
                    "kind": "stop_limit",
                    "price": 62480,
                    "notional_usdt": 2800,
                    "trigger_price": 62515,
                    "stop_loss": 62785,
                    "take_profits": [
                        {"price": 61650, "allocation_pct": 60},
                        {"price": 61250, "allocation_pct": 40},
                    ],
                }
            ],
            leverage=15,
            margin_mode="isolated",
        )

        order = plan["orders"][0]
        self.assertEqual(order["endpoint"], "managed_order_algo")
        self.assertNotIn("attachAlgoOrds", order["payload"])
        self.assertEqual(order["payload"]["ordType"], "trigger")
        self.assertEqual(order["payload"]["orderPx"], "62480")
        self.assertEqual(order["payload"]["sz"], "4.48")
        self.assertEqual(order["actual_notional_usdt"], "2799.104")
        self.assertEqual(order["notional_shortfall_usdt"], "0.896")
        self.assertEqual(order["exit_plan"]["close_side"], "buy")
        self.assertEqual([tp["trigger_px"] for tp in order["exit_plan"]["take_profits"]], ["61650", "61250"])
        self.assertEqual([tp["target_size"] for tp in order["exit_plan"]["take_profits"]], ["2.69", "1.79"])
        self.assertEqual(order["exit_plan"]["stop_loss"]["target_size"], "4.48")

    def test_okx_static_notional_limit_still_attaches_split_algos(self) -> None:
        from trading_gateway.application.risk.okx_algo import build_okx_static_notional_plan

        plan = build_okx_static_notional_plan(
            "okx",
            "BTCUSDT",
            side="sell",
            entries=[
                {
                    "kind": "limit",
                    "price": 62480,
                    "notional_usdt": 2800,
                    "stop_loss": 62785,
                    "take_profits": [
                        {"price": 61650, "allocation_pct": 60},
                        {"price": 61250, "allocation_pct": 40},
                    ],
                }
            ],
        )

        order = plan["orders"][0]
        self.assertEqual(order["endpoint"], "order")
        self.assertIn("attachAlgoOrds", order["payload"])
        self.assertEqual([row.get("tpTriggerPx") for row in order["payload"]["attachAlgoOrds"][:2]], ["61650", "61250"])
