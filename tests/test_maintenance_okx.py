from __future__ import annotations

from fakes import CliTestCase


class MaintenanceOkxTest(CliTestCase):
    def test_maintenance_position_view_requires_live_reduce_only_oco(self) -> None:
        from trading_gateway.application.maintenance.okx_position_maintenance import MaintenanceConfig, _build_positions

        positions = [
            {
                "instId": "NG-USDT-SWAP",
                "pos": "-47",
                "avgPx": "3.36",
                "markPx": "3.34",
                "upl": "0.94",
                "realizedPnl": "-0.08",
                "mgnMode": "isolated",
                "lever": "10",
                "closeOrderAlgo": [{"algoId": "abc", "tpTriggerPx": "3.326", "slTriggerPx": "3.379"}],
            }
        ]
        good_oco = [
            {
                "instId": "NG-USDT-SWAP",
                "algoId": "abc",
                "state": "live",
                "side": "buy",
                "reduceOnly": "true",
                "closeFraction": "1",
                "tpTriggerPx": "3.326",
                "slTriggerPx": "3.379",
            }
        ]

        view = _build_positions(positions, good_oco, MaintenanceConfig())[0]

        self.assertTrue(view.protected)
        self.assertEqual(view.owner, "automation")

    def test_maintenance_position_view_marks_external_symbols(self) -> None:
        from trading_gateway.application.maintenance.okx_position_maintenance import MaintenanceConfig, _build_positions

        positions = [
            {
                "instId": "ANTHROPIC-USDT-SWAP",
                "pos": "0.12",
                "avgPx": "1781",
                "markPx": "1778.7",
                "upl": "-0.276",
                "realizedPnl": "-0.042744",
                "mgnMode": "isolated",
                "lever": "5",
                "closeOrderAlgo": [{"algoId": "def", "tpTriggerPx": "2207.7", "slTriggerPx": "1533.4"}],
            }
        ]
        oco = [
            {
                "instId": "ANTHROPIC-USDT-SWAP",
                "algoId": "def",
                "state": "live",
                "side": "sell",
                "reduceOnly": "true",
                "closeFraction": "1",
                "tpTriggerPx": "2207.7",
                "slTriggerPx": "1533.4",
            }
        ]

        view = _build_positions(positions, oco, MaintenanceConfig())[0]

        self.assertTrue(view.protected)
        self.assertEqual(view.owner, "external/user-managed")

    def test_maintenance_daily_audit_resets_across_cst_date(self) -> None:
        from datetime import datetime
        from zoneinfo import ZoneInfo

        from trading_gateway.application.maintenance.okx_position_maintenance import _next_audit

        prior = "- Timestamp basis: Asia/Shanghai time, 2026-06-04 23:55:18 CST (+0800).\n- Current 2026-06-04 hard-refusal count: 3."
        audit_entry, refusal_count = _next_audit(prior, datetime(2026, 6, 5, 0, 21, tzinfo=ZoneInfo("Asia/Shanghai")), 1, False)

        self.assertEqual(audit_entry, 1)
        self.assertEqual(refusal_count, 1)

    def test_maintenance_detects_live_oco_bracket_change(self) -> None:
        from trading_gateway.application.maintenance.okx_position_maintenance import PositionView, _decision, _detect_protection_changes

        prior_state = """
## Current Live Positions

| Symbol | Owner | Side | Size | Entry | Mark | UPL | TP | SL | OCO Algo ID | Protection |
|---|---|---:|---:|---:|---:|---:|---:|---:|---|---|
| ANTHROPIC-USDT-SWAP | external/user-managed | long | 0.12 | 1781 | 1757.4 | -2.8320 | 2150.3 | 1464.2 | `3626808656114913280` | Live reduce-only OCO |
"""
        position = PositionView(
            inst_id="ANTHROPIC-USDT-SWAP",
            owner="external/user-managed",
            side="long",
            size=0.12,
            entry=1781,
            mark=1769.6,
            upl=-1.368,
            realized_pnl=-0.042744,
            fee=-0.042744,
            funding_fee=0,
            margin_mode="isolated",
            leverage="5",
            liq_px="1584.09",
            margin=42.744,
            tp=1796.1,
            sl=1740.6,
            oco_algo_id="3626808656114913280",
            protected=True,
            protection_note="live reduce-only full-close OCO",
        )

        changes = _detect_protection_changes(prior_state, [position])
        decision, notify, messages = _decision(
            positions=[position],
            gap=0,
            candidates=[],
            rejected=[],
            fills=[],
            bills=[],
            oco_history=[],
            protection_changes=changes,
            normal_order_count=0,
            conditional_count=0,
            trigger_count=0,
            refusal_count=0,
            refusal_limit=6,
        )

        self.assertEqual(len(changes), 1)
        self.assertEqual(changes[0]["oldTp"], 2150.3)
        self.assertEqual(changes[0]["newTp"], 1796.1)
        self.assertEqual(decision, "HOLD_PROTECTED_POSITIONS")
        self.assertTrue(notify)
        self.assertIn("Live OCO TP/SL changed", "\n".join(messages))

    def test_maintenance_requires_clear_ev_edge(self) -> None:
        from trading_gateway.application.maintenance.okx_position_maintenance import MaintenanceConfig, _candidate

        config = MaintenanceConfig()
        thin = _candidate(
            "HOME-USDT-SWAP",
            "long",
            42,
            "thin_ev",
            f"estimated EV 0.08U is below clear-edge minimum {config.min_expected_value_usdt:.2f}U after costs",
            0.0435,
            2.3,
            gross_tp=2.0,
            gross_sl=1.56,
            ev=0.08,
        )

        self.assertEqual(config.min_expected_value_usdt, 0.2)
        self.assertEqual(thin.status, "thin_ev")
        self.assertLess(thin.expected_value_usdt, config.min_expected_value_usdt)

    def test_maintenance_refusal_parser_ignores_ratio_text(self) -> None:
        from datetime import datetime
        from zoneinfo import ZoneInfo

        from trading_gateway.application.maintenance.okx_position_maintenance import _next_audit

        prior = "\n".join(
            [
                "- Timestamp basis: Asia/Shanghai time, 2026-06-05 02:29:20 CST (+0800).",
                "- Current daily hard-refusal count: 1.",
                "- Audit threshold: 1 of 6 allowed hard-refusal rounds used for 2026-06-05.",
            ]
        )

        audit_entry, refusal_count = _next_audit(prior, datetime(2026, 6, 5, 2, 52, tzinfo=ZoneInfo("Asia/Shanghai")), 1, True)

        self.assertEqual(audit_entry, 1)
        self.assertEqual(refusal_count, 1)

