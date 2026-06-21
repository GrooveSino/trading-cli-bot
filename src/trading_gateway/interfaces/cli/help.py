APP = (
    "Trading CLI Bot for OKX live trading, OKX simulation tests, and BTC/ETH market snapshots.\n\n"
    "Run without a command to print a wallet summary. Credentials are loaded from .env/config.toml and are never printed.\n"
    "Use tbot live ... for OKX live observation and tbot sim ... for OKX demo verification. "
    "Live mutation paths require an exact confirmation phrase from a dry-run plan."
)
APP_EPILOG = """
Start here:

  tbot sim btc test --yes

  tbot sim btc doctor

  tbot live btc

  tbot risk --help

  tbot summary --help

High-use examples:

  tbot live btc --json

  tbot live btc --table

  tbot sim eth --json --remote

  tbot marketdata collector --once --json

  tbot market btcusdt --write  # compatibility facade for OKX live BTC

  tbot sim btc test --usd 5

  tbot sim btc test --usd 5 --yes --json

  tbot okx trade plan btc --side buy --quote-usdt 10 --last-price 70000 --json

  tbot live btc account --json

  tbot sim eth account --json

  tbot risk apply plan.json --json

  tbot risk orders okx BTC-USDT-SWAP --ord-type all

Live safety:

  OKX live is the real private path. OKX sim uses demo trading credentials and ccxt sandbox mode.

  Binance private account access is disabled. Binance public derivatives stay under global_derivatives.

  Plan first, inspect payloads, then re-run with --live and the exact --confirm phrase printed by the dry-run.
"""
WALLET = "Private account commands: OKX live by default, OKX demo with --account-mode sim."
TRADE = "Plan or execute capped trade smoke tests for OKX live or OKX sim."
RISK = "Apply strict OKX JSON order plans, protect existing positions, and audit/cancel OKX algo orders."
RISK_EPILOG = """\b
Risk command pattern:

  1. Run a dry plan first. The plan prints exact payloads and a confirmation phrase.

  2. Re-run with --live and the exact --confirm value only when the payload is correct.

  3. Use tbot risk orders ... --ord-type all to audit pending algo orders after placement.

Focused examples:

  Apply a strict OKX JSON order plan

    tbot risk apply plan.json --json

  Protect an existing BTC long

    tbot risk plan okx BTC-USDT-SWAP long 2.56 --take-profit 76000 --stop-loss 72900 --margin-mode isolated --trigger-px-type mark
"""
MARKET = "Compatibility market commands. Prefer tbot live btc|eth or tbot sim btc|eth."
MARKET_EPILOG = """\b
Snapshot collectors:

  New preferred namespaces:
    tbot live btc|eth
    tbot sim btc|eth

  Global derivatives: OI deltas, 15m aggTrades CVD, OFI, whale flow, long/short ratios, RSI, VPP.
  Account overlay: OKX live under tbot live ... account; OKX demo under tbot sim ... account.
  Default non-JSON output is full LLM-friendly context with the same metric coverage as --table.

Examples:

  tbot live btc --json
  tbot live btc
  tbot live btc --table
  tbot sim eth --json
  tbot marketdata collector --once --json

Compatibility:

  tbot market btcusdt --json
  tbot market btcusdt --json --no-okx-account

  Run the cloud appliance once:
    tbot marketdata collector --once --json
"""
MARKET_BTCUSDT_EPILOG = """\b
Compatibility:

  This command maps to OKX live BTC. Prefer:
    tbot live btc

Output modes:

  Markdown default: full LLM-friendly context with the same metric coverage as --table, plus prose grouping.

  --table: print the same data as a dense audit table.

  --json: machine-readable snapshot with collector_timings_ms and fetch_strategy.

  --write: overwrite a Markdown report using the selected --llm/--table mode. This is intentionally not append-only.

  --no-okx-account: skip private OKX calls for a faster public-only snapshot.

  --no-binance-account: compatibility flag only; Binance private account overlay is disabled.

  --remote/--local: prefer hosted marketdata snapshot or force local live collection.

Operational examples:

  Full snapshot with BTC account overlay:

    tbot market btcusdt

  Hosted JSON for scripts:

    tbot market btcusdt --json --remote

  Full audit table:

    tbot market btcusdt --table

  Local live fallback:

    tbot market btcusdt --json --local --no-okx-account

  Refresh the standard report file:

    tbot market btcusdt --write

  Debug collector latency:

    tbot market btcusdt --json | jq '.collector_timings_ms, .fetch_strategy'
"""
MAINTENANCE = "Run adaptive OKX position-maintenance audits for recurring protected-position workflows."
CAPABILITIES = "Show static CCXT and adapter capability flags; no private API call is made."
SUMMARY = "Fast private wallet overview. Defaults to OKX live; use --exchange okx --account-mode sim for OKX demo."
SNAPSHOT = "Full private account snapshot. Defaults to OKX live; use --account-mode sim for OKX demo."
BALANCE = "Read a private wallet using the normalized account schema; use --raw for debug exchange payloads."
POSITIONS = "Dump redacted perp positions; omit symbol to ask the selected exchange for all positions."
ORDERS = "Dump redacted open orders for one market and symbol."
TRANSFER = "Plan or execute an account-internal transfer. OKX live only for now; live mode requires the exact confirmation phrase."
TOP_BALANCE = "Read private wallet. Examples: tbot balance okx perp; tbot balance okx perp --account-mode sim"
TOP_POSITIONS = "Read perp positions. Examples: tbot positions okx BTC/USDT:USDT; tbot positions okx BTC/USDT:USDT --account-mode sim"
TOP_ORDERS = "Read open orders. Examples: tbot orders okx perp BTC-USDT-SWAP; tbot orders okx perp BTC-USDT-SWAP --account-mode sim"
TOP_TRANSFER = "Plan or execute an internal transfer. Example: tbot transfer okx USDT 10 spot perp"
WEB = "Start the localhost Trading Gateway web dashboard."
DAEMON = "Manage the localhost Trading Gateway live daemon required for live trading paths."
LAB_PLAN = "Preview a single-leg spot/perp intent. OKX live and OKX sim are the supported private execution paths."
LAB_RUN = "Execute a real single-leg order with closed-loop verification. Perp close-long/close-short without quote uses a position-cleanup close-all mode."
LAB_PLAN_EPILOG = """\b
Examples:

  OKX live dry plan:

    tbot plan okx perp open-long BTC/USDT 10 --last-price 70000 --json

  OKX simulation dry plan:

    tbot plan okx perp open-long BTC/USDT 10 --account-mode sim --last-price 70000 --json
"""
LAB_RUN_EPILOG = """\b
Examples:

  OKX simulation live-style run:

    tbot run okx perp open-long BTC/USDT 10 --account-mode sim --confirm "LIVE_OKX_SIM_PERP_OPEN_LONG:BTCUSDT:QUOTE_10"
"""
PAIR_PLAN = "Preview a multi-exchange spot-long + perp-short pair with one shared base quantity."
PAIR_RUN = "Execute a real multi-exchange spot-long + perp-short pair with closed-loop verification. This remains a dual-leg convergence flow, not single-leg cleanup mode."
PAIR_CLOSE_PLAN = "Preview a pair close workflow that sells spot and buys back perp short toward flat exposure."
PAIR_CLOSE_RUN = "Execute a real pair close workflow with closed-loop verification and residual cleanup."
PAIR_STATUS = "Restore a pair run from its local journal and query live order/balance state."
PAIR_RESUME = "Resume a pair run from journal state. The original confirmation phrase is still required."
BINANCE = "Removed."
BINANCE_CHECK = "Removed."
PLAN = "Build an order plan and print the live confirmation phrase; never places an order."
SMOKE = "Run a capped order smoke test. Live quote amount is capped at 10 USDT."
TRADE_PLAN_EPILOG = """\b
Examples:

  OKX live dry plan:

    tbot trade plan --exchange okx --market perp --symbol BTC/USDT --side buy --quote-usdt 10 --last-price 70000 --json

  OKX simulation dry plan:

    tbot trade plan --exchange okx --market perp --symbol BTC/USDT --side buy --quote-usdt 10 --account-mode sim --last-price 70000 --json

  Preferred namespace dry plans:

    tbot okx trade plan btc --side buy --quote-usdt 10 --last-price 70000 --json

    tbot trade plan --exchange okx --market perp --symbol ETH/USDT --side sell --quote-usdt 10 --account-mode sim --last-price 3500 --json
"""
SMOKE_EPILOG = """\b
Examples:

  OKX simulation smoke after inspecting the plan:

    tbot trade smoke --exchange okx --market perp --symbol BTC/USDT --side buy --quote-usdt 10 --account-mode sim --live --confirm "LIVE_ORDER:OKX_SIM:perp:BTC/USDT:10"

  Preferred namespace smoke:

    tbot sim btc test --yes
"""
