from __future__ import annotations

from collections.abc import Callable
from datetime import datetime, timedelta, timezone
from typing import Any

from trading_gateway.infrastructure.exchange.factory import build_ccxt_client, close_client
from trading_gateway.support.redaction import redact_mapping, redact_text

OKX_BTC_INST_ID = "BTC-USDT-SWAP"
OKX_PRIVATE_SOURCE = "OKX private account/trade API"
CST = timezone(timedelta(hours=8))


def collect_okx_btc_account(client_factory: Callable[[], Any] | None = None) -> dict[str, Any]:
    factory = client_factory or _default_okx_swap_client
    client = None
    try:
        client = factory()
        positions = _native_positions(client)
        open_orders = _native_open_orders(client)
        algo_orders = _native_algo_orders(client)
        return redact_mapping(
            {
                "source": OKX_PRIVATE_SOURCE,
                "status": "ok",
                "timestamp_cst": _now_cst(),
                "positions": [_compact_position(row) for row in positions if _is_btc_row(row) and _has_position(row)],
                "open_orders": [_compact_open_order(row) for row in open_orders if _is_btc_row(row)],
                "algo_orders": {kind: [_compact_algo_order(row) for row in rows if _is_btc_row(row)] for kind, rows in algo_orders.items()},
            }
        )
    except Exception as exc:  # noqa: BLE001 - account overlay should not break public market snapshot.
        return {
            "source": OKX_PRIVATE_SOURCE,
            "status": "error",
            "timestamp_cst": _now_cst(),
            "error": redact_text(f"{type(exc).__name__}: {exc}"),
            "positions": [],
            "open_orders": [],
            "algo_orders": {"conditional": [], "oco": [], "trigger": []},
        }
    finally:
        if client is not None:
            close_client(client)


def add_okx_account_counts(account: dict[str, Any]) -> dict[str, Any]:
    algo = account.get("algo_orders") or {}
    counts = {
        "positions": len(account.get("positions") or []),
        "open_orders": len(account.get("open_orders") or []),
        "algo_orders": sum(len(rows or []) for rows in algo.values()),
    }
    return {**account, "counts": counts}


def _default_okx_swap_client() -> Any:
    return build_ccxt_client("okx", "swap", require_private=True)


def _native_positions(client: Any) -> list[dict[str, Any]]:
    if hasattr(client, "privateGetAccountPositions"):
        return _data(client.privateGetAccountPositions({"instType": "SWAP", "instId": OKX_BTC_INST_ID}))
    rows = client.fetch_positions(["BTC/USDT:USDT"]) if hasattr(client, "fetch_positions") else []
    return list(rows or [])


def _native_open_orders(client: Any) -> list[dict[str, Any]]:
    if hasattr(client, "privateGetTradeOrdersPending"):
        return _data(client.privateGetTradeOrdersPending({"instType": "SWAP", "instId": OKX_BTC_INST_ID}))
    rows = client.fetch_open_orders("BTC/USDT:USDT") if hasattr(client, "fetch_open_orders") else []
    return list(rows or [])


def _native_algo_orders(client: Any) -> dict[str, list[dict[str, Any]]]:
    if not hasattr(client, "privateGetTradeOrdersAlgoPending"):
        return {"conditional": [], "oco": [], "trigger": []}
    result: dict[str, list[dict[str, Any]]] = {}
    for kind in ("conditional", "oco", "trigger"):
        result[kind] = _data(client.privateGetTradeOrdersAlgoPending({"ordType": kind, "instId": OKX_BTC_INST_ID}))
    return result


def _compact_position(row: dict[str, Any]) -> dict[str, Any]:
    info = row.get("info") if isinstance(row.get("info"), dict) else row
    return {
        "inst_id": _first(row.get("instId"), info.get("instId"), row.get("symbol"), OKX_BTC_INST_ID),
        "side": _position_side(row, info),
        "size": _first(row.get("pos"), info.get("pos"), row.get("contracts"), row.get("size")),
        "entry_price": _first(row.get("avgPx"), info.get("avgPx"), row.get("entryPrice")),
        "mark_price": _first(row.get("markPx"), info.get("markPx"), row.get("markPrice")),
        "notional_usd": _first(row.get("notionalUsd"), info.get("notionalUsd"), row.get("notional")),
        "unrealized_pnl": _first(row.get("upl"), info.get("upl"), row.get("unrealizedPnl")),
        "liq_price": _first(row.get("liqPx"), info.get("liqPx"), row.get("liquidationPrice")),
        "leverage": _first(row.get("lever"), info.get("lever"), row.get("leverage")),
        "margin_mode": _first(row.get("mgnMode"), info.get("mgnMode"), row.get("marginMode")),
        "margin_ratio": _first(row.get("mgnRatio"), info.get("mgnRatio"), row.get("marginRatio")),
        "updated_at_cst": _cst_from_ms(_first(row.get("uTime"), info.get("uTime"), row.get("lastUpdateTimestamp"), row.get("timestamp"))),
    }


def _compact_open_order(row: dict[str, Any]) -> dict[str, Any]:
    info = row.get("info") if isinstance(row.get("info"), dict) else row
    return {
        "order_id": _first(row.get("ordId"), info.get("ordId"), row.get("id")),
        "client_order_id": _first(row.get("clOrdId"), info.get("clOrdId"), row.get("clientOrderId")),
        "inst_id": _first(row.get("instId"), info.get("instId"), row.get("symbol"), OKX_BTC_INST_ID),
        "state": _first(row.get("state"), info.get("state"), row.get("status")),
        "side": _first(row.get("side"), info.get("side")),
        "pos_side": _first(row.get("posSide"), info.get("posSide")),
        "order_type": _first(row.get("ordType"), info.get("ordType"), row.get("type")),
        "price": _first(row.get("px"), info.get("px"), row.get("price")),
        "size": _first(row.get("sz"), info.get("sz"), row.get("amount")),
        "filled": _first(row.get("accFillSz"), info.get("accFillSz"), row.get("filled")),
        "average": _first(row.get("avgPx"), info.get("avgPx"), row.get("average")),
        "reduce_only": _first(row.get("reduceOnly"), info.get("reduceOnly")),
        "created_at_cst": _cst_from_ms(_first(row.get("cTime"), info.get("cTime"), row.get("timestamp"))),
        "updated_at_cst": _cst_from_ms(_first(row.get("uTime"), info.get("uTime"), row.get("lastTradeTimestamp"))),
    }


def _compact_algo_order(row: dict[str, Any]) -> dict[str, Any]:
    return {
        "algo_id": row.get("algoId"),
        "inst_id": row.get("instId"),
        "state": row.get("state"),
        "side": row.get("side"),
        "ord_type": row.get("ordType"),
        "td_mode": row.get("tdMode"),
        "reduce_only": row.get("reduceOnly"),
        "size": row.get("sz"),
        "tp_trigger": row.get("tpTriggerPx"),
        "sl_trigger": row.get("slTriggerPx"),
        "trigger": row.get("triggerPx"),
        "order_px": _first(row.get("ordPx"), row.get("tpOrdPx"), row.get("slOrdPx")),
        "created_at_cst": _cst_from_ms(row.get("cTime")),
        "updated_at_cst": _cst_from_ms(row.get("uTime")),
    }


def _is_btc_row(row: dict[str, Any]) -> bool:
    text = " ".join(str(row.get(key) or "") for key in ("instId", "symbol", "id"))
    info = row.get("info")
    if isinstance(info, dict):
        text += " " + " ".join(str(info.get(key) or "") for key in ("instId", "symbol", "id"))
    compact = "".join(ch for ch in text.upper() if ch.isalnum())
    return "BTCUSDT" in compact


def _has_position(row: dict[str, Any]) -> bool:
    info = row.get("info") if isinstance(row.get("info"), dict) else row
    value = _first(row.get("pos"), info.get("pos"), row.get("contracts"), row.get("size"))
    try:
        return abs(float(value or 0)) > 0
    except (TypeError, ValueError):
        return False


def _position_side(row: dict[str, Any], info: dict[str, Any]) -> str | None:
    raw = str(_first(row.get("posSide"), info.get("posSide"), row.get("side")) or "").lower()
    if raw in {"long", "short"}:
        return raw
    value = _first(row.get("pos"), info.get("pos"), row.get("contracts"), row.get("size"))
    try:
        number = float(value or 0)
    except (TypeError, ValueError):
        return raw or None
    if number < 0:
        return "short"
    if number > 0:
        return "long"
    return None


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


def _cst_from_ms(value: Any) -> str | None:
    try:
        parsed = int(float(str(value)))
    except (TypeError, ValueError):
        return None
    if parsed <= 0:
        return None
    return datetime.fromtimestamp(parsed / 1000, timezone.utc).astimezone(CST).strftime("%Y-%m-%d %H:%M:%S")


def _now_cst() -> str:
    return datetime.now(timezone.utc).astimezone(CST).strftime("%Y-%m-%d %H:%M:%S")
