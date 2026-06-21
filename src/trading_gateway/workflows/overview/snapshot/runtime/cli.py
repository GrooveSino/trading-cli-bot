from __future__ import annotations

from trading_gateway.workflows.overview.snapshot.runtime.service import build_account_snapshot
from trading_gateway.workflows.overview.snapshot.rendering.render import print_account_snapshot_rich
from trading_gateway.support.formatting import print_json


def print_account_snapshot(
    exchanges: list[str] | None,
    *,
    json_output: bool,
    nonzero_only: bool,
    active_positions_only: bool,
    account_mode: str | None = None,
) -> None:
    payload = build_account_snapshot(
        exchanges,
        nonzero_only=nonzero_only,
        include_empty_positions=not active_positions_only,
        account_mode=account_mode,
    )
    print_json(payload) if json_output else print_account_snapshot_rich(payload)
