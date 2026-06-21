from __future__ import annotations

from typing import Any

from trading_gateway.application.market.specs import MarketSpec, VenueProfile
from trading_gateway.infrastructure.exchange.factory import build_ccxt_client, close_client
from trading_gateway.support.redaction import redact_mapping, redact_text


def collect_account_overlay(venue: VenueProfile, spec: MarketSpec) -> dict[str, Any]:
    client = None
    try:
        client = build_ccxt_client(venue.exchange, "swap", require_private=True, account_mode=venue.account_mode)
        positions = _positions(client, venue, spec)
        open_orders = _open_orders(client, venue, spec)
        algos = _algo_orders(client, venue, spec)
        return redact_mapping(
            {
                "source": venue.private_source,
                "status": "ok",
                "venue": venue.id,
                "symbol": spec.key,
                "positions": positions,
                "open_orders": open_orders,
                "algo_orders": algos,
                "counts": {"positions": len(positions), "open_orders": len(open_orders), "algo_orders": sum(len(rows) for rows in algos.values())},
            }
        )
    except Exception as exc:  # noqa: BLE001 - account overlay should not break market snapshots.
        return {"source": venue.private_source, "status": "error", "venue": venue.id, "symbol": spec.key, "error": redact_text(f"{type(exc).__name__}: {exc}"), "positions": [], "open_orders": [], "algo_orders": {}, "counts": {"positions": 0, "open_orders": 0, "algo_orders": 0}}
    finally:
        if client is not None:
            close_client(client)


def _positions(client: Any, venue: VenueProfile, spec: MarketSpec) -> list[dict[str, Any]]:
    if venue.exchange == "okx" and hasattr(client, "privateGetAccountPositions"):
        rows = _data(client.privateGetAccountPositions({"instType": "SWAP", "instId": spec.okx_inst_id}))
    else:
        rows = client.fetch_positions([_ccxt_symbol(venue, spec)]) if hasattr(client, "fetch_positions") else []
    return [_compact_position(row) for row in rows if _matches_symbol(row, spec) and _has_position(row)]


def _open_orders(client: Any, venue: VenueProfile, spec: MarketSpec) -> list[dict[str, Any]]:
    if venue.exchange == "okx" and hasattr(client, "privateGetTradeOrdersPending"):
        rows = _data(client.privateGetTradeOrdersPending({"instType": "SWAP", "instId": spec.okx_inst_id}))
    else:
        rows = client.fetch_open_orders(_ccxt_symbol(venue, spec)) if hasattr(client, "fetch_open_orders") else []
    return [_compact_order(row) for row in rows if _matches_symbol(row, spec)]


def _algo_orders(client: Any, venue: VenueProfile, spec: MarketSpec) -> dict[str, list[dict[str, Any]]]:
    if venue.exchange != "okx" or not hasattr(client, "privateGetTradeOrdersAlgoPending"):
        return {"conditional": [], "oco": [], "trigger": []}
    result: dict[str, list[dict[str, Any]]] = {}
    for kind in ("conditional", "oco", "trigger"):
        rows = _data(client.privateGetTradeOrdersAlgoPending({"ordType": kind, "instId": spec.okx_inst_id}))
        result[kind] = [_compact_algo(row) for row in rows if _matches_symbol(row, spec)]
    return result


def _compact_position(row: dict[str, Any]) -> dict[str, Any]:
    info = row.get("info") if isinstance(row.get("info"), dict) else row
    return {
        "inst_id": _first(row.get("instId"), info.get("instId"), row.get("contract"), row.get("symbol")),
        "side": _position_side(row, info),
        "size": _first(row.get("pos"), info.get("pos"), row.get("size"), row.get("contracts")),
        "entry_price": _first(row.get("avgPx"), info.get("avgPx"), row.get("entry_price"), row.get("entryPrice")),
        "mark_price": _first(row.get("markPx"), info.get("markPx"), row.get("mark_price"), row.get("markPrice")),
        "notional_usd": _first(row.get("notionalUsd"), info.get("notionalUsd"), row.get("value"), row.get("notional")),
        "unrealized_pnl": _first(row.get("upl"), info.get("upl"), row.get("unrealised_pnl"), row.get("unrealizedPnl")),
        "liq_price": _first(row.get("liqPx"), info.get("liqPx"), row.get("liq_price"), row.get("liquidationPrice")),
        "leverage": _first(row.get("lever"), info.get("lever"), row.get("leverage")),
        "margin_mode": _first(row.get("mgnMode"), info.get("mgnMode"), row.get("marginMode")),
    }


def _compact_order(row: dict[str, Any]) -> dict[str, Any]:
    info = row.get("info") if isinstance(row.get("info"), dict) else row
    return {"order_id": _first(row.get("ordId"), info.get("ordId"), row.get("id")), "inst_id": _first(row.get("instId"), info.get("instId"), row.get("contract"), row.get("symbol")), "state": _first(row.get("state"), info.get("state"), row.get("status")), "side": _first(row.get("side"), info.get("side")), "order_type": _first(row.get("ordType"), info.get("ordType"), row.get("type")), "price": _first(row.get("px"), info.get("px"), row.get("price")), "size": _first(row.get("sz"), info.get("sz"), row.get("amount")), "filled": _first(row.get("accFillSz"), info.get("accFillSz"), row.get("filled")), "reduce_only": _first(row.get("reduceOnly"), info.get("reduceOnly"))}


def _compact_algo(row: dict[str, Any]) -> dict[str, Any]:
    return {"algo_id": row.get("algoId"), "inst_id": row.get("instId"), "state": row.get("state"), "side": row.get("side"), "ord_type": row.get("ordType"), "size": row.get("sz"), "tp_trigger": row.get("tpTriggerPx"), "sl_trigger": row.get("slTriggerPx"), "trigger": row.get("triggerPx"), "reduce_only": row.get("reduceOnly")}


def _matches_symbol(row: dict[str, Any], spec: MarketSpec) -> bool:
    info = row.get("info") if isinstance(row.get("info"), dict) else {}
    text = " ".join(str(item or "") for item in [row.get("instId"), row.get("symbol"), row.get("contract"), row.get("id"), info.get("instId"), info.get("symbol"), info.get("contract")])
    compact = "".join(ch for ch in text.upper() if ch.isalnum())
    return spec.derivatives_symbol in compact or spec.okx_inst_id.replace("-", "") in compact


def _ccxt_symbol(venue: VenueProfile, spec: MarketSpec) -> str:
    return spec.okx_ccxt_symbol


def _has_position(row: dict[str, Any]) -> bool:
    info = row.get("info") if isinstance(row.get("info"), dict) else row
    value = _first(row.get("pos"), info.get("pos"), row.get("size"), row.get("contracts"))
    try:
        return abs(float(value or 0)) > 0
    except (TypeError, ValueError):
        return False


def _position_side(row: dict[str, Any], info: dict[str, Any]) -> str | None:
    raw = str(_first(row.get("posSide"), info.get("posSide"), row.get("side"), info.get("mode")) or "").lower()
    if raw in {"long", "short"}:
        return raw
    value = _first(row.get("pos"), info.get("pos"), row.get("size"), row.get("contracts"))
    try:
        number = float(value or 0)
    except (TypeError, ValueError):
        return raw or None
    return "short" if number < 0 else "long" if number > 0 else None


def _data(payload: Any) -> list[dict[str, Any]]:
    if isinstance(payload, dict):
        rows = payload.get("data")
        return list(rows or []) if isinstance(rows, list) else []
    return list(payload or []) if isinstance(payload, list) else []


def _first(*values: Any) -> Any:
    for value in values:
        if value not in (None, ""):
            return value
    return None
