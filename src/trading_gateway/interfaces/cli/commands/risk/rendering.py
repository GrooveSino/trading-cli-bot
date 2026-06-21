from __future__ import annotations

from trading_gateway.support.formatting import print_json


def _print_plan(plan: dict) -> None:
    print(f"OKX risk bracket plan for {plan['symbol']} {plan['position_side']} size={plan['size']}")
    for order in plan.get("algo_orders") or []:
        payload = order["payload"]
        trigger = payload.get("tpTriggerPx") or payload.get("slTriggerPx")
        print(f"- {order['kind']}: {payload['side']} reduce-only at trigger {trigger}, orderPx={payload.get('tpOrdPx') or payload.get('slOrdPx')}")
    print(f"confirm: {plan['confirm_phrase']}")


def _print_live_result(payload: dict) -> None:
    print_json(payload)


def _print_algo_orders(payload: dict) -> None:
    print_json(payload)
