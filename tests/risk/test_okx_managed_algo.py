from __future__ import annotations

from fakes import CliTestCase


class RiskOkxManagedAlgoTest(CliTestCase):
    def _managed_plan(self) -> dict:
        from trading_gateway.application.risk.okx_algo import build_okx_static_notional_plan

        return build_okx_static_notional_plan(
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

    def test_okx_managed_static_notional_places_exits_after_fill(self) -> None:
        from trading_gateway.application.risk.okx_algo import place_okx_static_notional_orders

        class FakeClient:
            def __init__(self) -> None:
                self.algo_payloads: list[dict] = []

            def privatePostAccountSetLeverage(self, payload: dict) -> dict:
                return {"code": "0", "data": [payload]}

            def privatePostTradeOrderAlgo(self, payload: dict) -> dict:
                self.algo_payloads.append(payload)
                return {"code": "0", "data": [{"algoId": f"algo-{len(self.algo_payloads)}", "sCode": "0"}]}

            def privateGetTradeOrdersAlgoPending(self, params: dict) -> dict:
                if params["ordType"] == "trigger":
                    return {"code": "0", "data": []}
                return {"code": "0", "data": [{"algoId": "algo-2", "state": "live"}, {"algoId": "algo-3", "state": "live"}, {"algoId": "algo-4", "state": "live"}]}

            def privateGetTradeOrdersAlgoHistory(self, params: dict) -> dict:
                if params["state"] == "effective":
                    return {"code": "0", "data": [{"algoId": "algo-1", "state": "effective"}]}
                return {"code": "0", "data": []}

            def privateGetTradeOrdersHistory(self, params: dict) -> dict:
                return {"code": "0", "data": []}

            def privateGetTradeFills(self, params: dict) -> dict:
                return {"code": "0", "data": []}

            def privateGetAccountBills(self, params: dict) -> dict:
                return {"code": "0", "data": []}

            def privateGetAccountPositions(self, params: dict) -> dict:
                return {"code": "0", "data": [{"instId": "BTC-USDT-SWAP", "pos": "-4.48"}]}

            def privateGetTradeOrdersPending(self, params: dict) -> dict:
                return {"code": "0", "data": []}

        client = FakeClient()
        result = place_okx_static_notional_orders(client, self._managed_plan(), managed_poll_interval_sec=0, managed_timeout_sec=0)

        self.assertEqual(result["orders"][0]["managed_parent_state"]["state"], "filled")
        self.assertEqual(result["orders"][0]["managed_exits"]["status"], "live")
        self.assertEqual(len(client.algo_payloads), 4)
        self.assertNotIn("attachAlgoOrds", client.algo_payloads[0])
        self.assertTrue(all(payload.get("reduceOnly") == "true" for payload in client.algo_payloads[1:]))

    def test_okx_managed_static_notional_reports_parent_failure_without_exits(self) -> None:
        from trading_gateway.application.risk.okx_algo import place_okx_static_notional_orders

        class FakeClient:
            def __init__(self) -> None:
                self.algo_payloads: list[dict] = []

            def privatePostAccountSetLeverage(self, payload: dict) -> dict:
                return {"code": "0", "data": [payload]}

            def privatePostTradeOrderAlgo(self, payload: dict) -> dict:
                self.algo_payloads.append(payload)
                return {"code": "0", "data": [{"algoId": "parent", "sCode": "0"}]}

            def privateGetTradeOrdersAlgoPending(self, params: dict) -> dict:
                return {"code": "0", "data": []}

            def privateGetTradeOrdersAlgoHistory(self, params: dict) -> dict:
                if params["state"] == "order_failed":
                    return {"code": "0", "data": [{"algoId": "parent", "state": "order_failed"}]}
                return {"code": "0", "data": []}

            def privateGetTradeOrdersHistory(self, params: dict) -> dict:
                return {"code": "0", "data": []}

            def privateGetTradeFills(self, params: dict) -> dict:
                return {"code": "0", "data": []}

            def privateGetAccountBills(self, params: dict) -> dict:
                return {"code": "0", "data": []}

            def privateGetAccountPositions(self, params: dict) -> dict:
                return {"code": "0", "data": []}

            def privateGetTradeOrdersPending(self, params: dict) -> dict:
                return {"code": "0", "data": []}

        client = FakeClient()
        result = place_okx_static_notional_orders(client, self._managed_plan(), managed_poll_interval_sec=0, managed_timeout_sec=0)

        self.assertEqual(result["orders"][0]["managed_parent_state"]["state"], "order_failed")
        self.assertNotIn("managed_exits", result["orders"][0])
        self.assertEqual(len(client.algo_payloads), 1)

    def test_okx_managed_static_notional_emergency_closes_when_exit_verification_fails(self) -> None:
        from trading_gateway.application.risk.okx_algo import place_okx_static_notional_orders

        class FakeClient:
            def __init__(self) -> None:
                self.algo_payloads: list[dict] = []
                self.close_payloads: list[dict] = []

            def privatePostAccountSetLeverage(self, payload: dict) -> dict:
                return {"code": "0", "data": [payload]}

            def privatePostTradeOrderAlgo(self, payload: dict) -> dict:
                self.algo_payloads.append(payload)
                return {"code": "0", "data": [{"algoId": f"algo-{len(self.algo_payloads)}", "sCode": "0"}]}

            def privatePostTradeOrder(self, payload: dict) -> dict:
                self.close_payloads.append(payload)
                return {"code": "0", "data": [{"ordId": "close", "sCode": "0"}]}

            def privateGetTradeOrdersAlgoPending(self, params: dict) -> dict:
                return {"code": "0", "data": []}

            def privateGetTradeOrdersAlgoHistory(self, params: dict) -> dict:
                if params["state"] == "effective":
                    return {"code": "0", "data": [{"algoId": "algo-1", "state": "effective"}]}
                return {"code": "0", "data": []}

            def privateGetTradeOrdersHistory(self, params: dict) -> dict:
                return {"code": "0", "data": []}

            def privateGetTradeFills(self, params: dict) -> dict:
                return {"code": "0", "data": []}

            def privateGetAccountBills(self, params: dict) -> dict:
                return {"code": "0", "data": []}

            def privateGetAccountPositions(self, params: dict) -> dict:
                return {"code": "0", "data": [{"instId": "BTC-USDT-SWAP", "pos": "-4.48"}]}

            def privateGetTradeOrdersPending(self, params: dict) -> dict:
                return {"code": "0", "data": []}

        client = FakeClient()
        result = place_okx_static_notional_orders(client, self._managed_plan(), managed_poll_interval_sec=0, managed_timeout_sec=0)

        self.assertEqual(result["orders"][0]["managed_exits"]["status"], "emergency_closed")
        self.assertEqual(client.close_payloads[0]["reduceOnly"], "true")
        self.assertEqual(client.close_payloads[0]["side"], "buy")
        self.assertEqual(client.close_payloads[0]["sz"], "4.48")
