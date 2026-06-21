from __future__ import annotations

import typer


def _parse_price_allocs(values: list[str], option_name: str) -> list[tuple[float, float]]:
    parsed: list[tuple[float, float]] = []
    for raw in values:
        text = str(raw or "").strip()
        if ":" not in text:
            raise typer.BadParameter(f"{option_name} must use price:allocation_pct", param_hint=option_name)
        price_text, alloc_text = text.split(":", 1)
        try:
            price = float(price_text)
            allocation = float(alloc_text)
        except ValueError as exc:
            raise typer.BadParameter(f"{option_name} must contain numeric price and allocation", param_hint=option_name) from exc
        parsed.append((price, allocation))
    if not parsed:
        raise typer.BadParameter(f"at least one {option_name} is required", param_hint=option_name)
    return parsed


def _parse_static_entries(values: list[str]) -> list[dict]:
    parsed: list[dict] = []
    for raw in values:
        text = str(raw or "").strip()
        parts = text.split(":", 3)
        if len(parts) != 4:
            raise typer.BadParameter(
                "--entry must use price:allocation_pct:stop_loss:tp_px@tp_pct,tp_px@tp_pct",
                param_hint="--entry",
            )
        price_text, alloc_text, sl_text, tp_text = parts
        try:
            price = float(price_text)
            allocation = float(alloc_text)
            stop_loss = float(sl_text)
        except ValueError as exc:
            raise typer.BadParameter("--entry price, allocation_pct, and stop_loss must be numeric", param_hint="--entry") from exc
        take_profits = _parse_tp_matrix(tp_text)
        parsed.append({"price": price, "allocation_pct": allocation, "stop_loss": stop_loss, "take_profits": take_profits})
    if not parsed:
        raise typer.BadParameter("at least one --entry is required", param_hint="--entry")
    return parsed


def _parse_tp_matrix(value: str) -> list[dict]:
    parsed: list[dict] = []
    for raw_part in str(value or "").split(","):
        part = raw_part.strip()
        if not part:
            continue
        if "@" not in part:
            raise typer.BadParameter("take-profit entries must use tp_px@tp_pct", param_hint="--entry")
        price_text, alloc_text = part.split("@", 1)
        try:
            parsed.append({"price": float(price_text), "allocation_pct": float(alloc_text)})
        except ValueError as exc:
            raise typer.BadParameter("take-profit price and allocation must be numeric", param_hint="--entry") from exc
    if not parsed:
        raise typer.BadParameter("at least one take-profit target is required", param_hint="--entry")
    return parsed


def _parse_static_notional_entries(values: list[str]) -> list[dict]:
    parsed: list[dict] = []
    for raw in values:
        text = str(raw or "").strip()
        parts = text.split(":", 5)
        if len(parts) != 6:
            raise typer.BadParameter(
                "--entry must use kind:price:notional_usdt:trigger_price_or_NONE:stop_loss:tp_px@tp_pct,tp_px@tp_pct",
                param_hint="--entry",
            )
        kind_text, price_text, notional_text, trigger_text, sl_text, tp_text = parts
        try:
            entry = {
                "kind": kind_text.strip().lower().replace("-", "_"),
                "price": float(price_text),
                "notional_usdt": float(notional_text),
                "stop_loss": float(sl_text),
                "take_profits": _parse_tp_matrix(tp_text),
            }
            if entry["kind"] == "stop_limit":
                entry["trigger_price"] = float(trigger_text)
        except ValueError as exc:
            raise typer.BadParameter("--entry numeric fields are invalid", param_hint="--entry") from exc
        parsed.append(entry)
    if not parsed:
        raise typer.BadParameter("at least one --entry is required", param_hint="--entry")
    return parsed


def _parse_trigger_oco_legs(values: list[str]) -> list[dict]:
    parsed: list[dict] = []
    for raw in values:
        text = str(raw or "").strip()
        parts = text.split(":", 6)
        if len(parts) != 7:
            raise typer.BadParameter(
                "--leg must use label:side:trigger_price:order_price:notional_usdt:stop_loss:tp_px@tp_pct,tp_px@tp_pct",
                param_hint="--leg",
            )
        label_text, side_text, trigger_text, order_text, notional_text, sl_text, tp_text = parts
        try:
            parsed.append(
                {
                    "label": label_text,
                    "side": side_text.strip().lower(),
                    "trigger_price": float(trigger_text),
                    "order_price": float(order_text),
                    "notional_usdt": float(notional_text),
                    "stop_loss": float(sl_text),
                    "take_profits": _parse_tp_matrix(tp_text),
                }
            )
        except ValueError as exc:
            raise typer.BadParameter("--leg numeric fields are invalid", param_hint="--leg") from exc
    if len(parsed) != 2:
        raise typer.BadParameter("exactly two --leg values are required", param_hint="--leg")
    return parsed
