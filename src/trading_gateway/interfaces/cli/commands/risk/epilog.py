from __future__ import annotations

PLAN_EPILOG = """
Examples:

  tbot risk plan okx BTC-USDT-SWAP long 2.56 --take-profit 76000 --stop-loss 72900
  tbot risk plan okx BTCUSDT short 0.05 --take-profit 61300 --stop-loss 62545 --margin-mode isolated --trigger-px-type mark
"""

BRACKET_EPILOG = """
Safe usage:

  1. Run tbot risk plan ... first and inspect the exact algo payloads.
  2. Copy the printed confirm phrase.
  3. Re-run this command with --live --confirm "CONFIRM_FROM_PLAN".
"""

GRID_SHORT_EPILOG = """
Entry format:

  --entry PRICE:ALLOCATION_PCT
  --take-profit PRICE:ALLOCATION_PCT
"""

STATIC_GRID_EPILOG = """
Entry format:

  --entry "PRICE:ALLOCATION_PCT:STOP_LOSS:TP_PRICE@TP_PCT,TP_PRICE@TP_PCT"
"""

STATIC_NOTIONAL_EPILOG = """
Entry format:

  --entry "KIND:PRICE:NOTIONAL_USDT:TRIGGER_PRICE_OR_NONE:STOP_LOSS:TP_PRICE@TP_PCT,TP_PRICE@TP_PCT"

Kinds: post_only, limit, stop_limit. stop_limit requires a numeric trigger price.
"""

ORDERS_EPILOG = """
Examples:

  tbot risk orders okx BTC-USDT-SWAP --ord-type all
  tbot risk orders okx BTCUSDT --ord-type conditional --json
"""

CANCEL_EPILOG = """
Example:

  tbot risk cancel okx BTC-USDT-SWAP 123 456 --confirm "LIVE_CANCEL_ALGOS:okx:BTC-USDT-SWAP:123,456"
"""
