from __future__ import annotations

import os
import subprocess
import sys
import unittest
from pathlib import Path
from typing import Mapping

ROOT = Path(__file__).resolve().parents[1]
CLI = ROOT / "cli" / "tbot.py"
CONFIG = ROOT / "config.toml"


class CliTestCase(unittest.TestCase):
    def run_cli(self, *args: str, env: Mapping[str, str] | None = None) -> subprocess.CompletedProcess[str]:
        return subprocess.run(
            [sys.executable, str(CLI), *args],
            cwd=ROOT,
            env=None if env is None else {**os.environ, **env},
            text=True,
            capture_output=True,
            check=False,
        )


class FakeMarketClient:
    def __init__(self) -> None:
        self.calls: dict[str, int] = {"aggTrades": 0, "market/books": 0, "15m_klines": 0}

    def get_json(self, url: str, params: dict | None = None) -> object:
        params = params or {}
        if "public/instruments" in url:
            return {"data": [{"ctVal": "0.01"}]}
        if "market/ticker" in url:
            return {"data": [{"last": "62000", "askPx": "62000.1", "bidPx": "61999.9"}]}
        if "market/books" in url:
            self.calls["market/books"] += 1
            return {
                "data": [
                    {
                        "ts": "1780820000000",
                        "asks": [["62500", "2000", "0", "1"], ["62100", "500", "0", "1"], ["62800", "1000", "0", "1"]],
                        "bids": [["61900", "400", "0", "1"], ["61800", "100", "0", "1"]],
                    }
                ]
            }
        if "fapi/v1/openInterest" in url:
            return {"openInterest": "1120", "time": 1780820000000}
        if "premiumIndex" in url:
            return {
                "markPrice": "62010",
                "indexPrice": "62000",
                "lastFundingRate": "0.0001",
                "nextFundingTime": "1780828800000",
                "time": "1780820000000",
            }
        if "openInterestHist" in url:
            return [
                {"timestamp": 1780816400000 + index * 300000, "sumOpenInterest": str(1000 + index * 10), "sumOpenInterestValue": str(60_000_000 + index * 1_000_000)}
                for index in range(13)
            ]
        if "aggTrades" in url:
            self.calls["aggTrades"] += 1
            return [
                {"a": 1, "T": 1780819100000, "p": "62000", "q": "2", "m": False},
                {"a": 2, "T": 1780819200000, "p": "62000", "q": "4", "m": True},
                {"a": 3, "T": 1780819300000, "p": "62000", "q": "3", "m": False},
                {"a": 4, "T": 1780819900000, "p": "62100", "q": "1", "m": False},
                {"a": 5, "T": 1780820000000, "p": "62000", "q": "5", "m": True},
            ]
        if "topLongShortPositionRatio" in url:
            return [
                {"symbol": "BTCUSDT", "longAccount": str(0.50 + index / 1000), "shortAccount": str(0.50 - index / 1000), "longShortRatio": str(1.0 + index / 100), "timestamp": 1780816400000 + index * 300000}
                for index in range(13)
            ]
        if "topLongShortAccountRatio" in url:
            return [{"symbol": "BTCUSDT", "longAccount": "0.60", "shortAccount": "0.40", "longShortRatio": "1.5000", "timestamp": 1780820000000}]
        if "globalLongShortAccountRatio" in url:
            return [{"symbol": "BTCUSDT", "longAccount": "0.65", "shortAccount": "0.35", "longShortRatio": "1.8571", "timestamp": 1780820000000}]
        if "klines" in url:
            interval = params.get("interval")
            if interval == "15m":
                self.calls["15m_klines"] += 1
                return fake_klines(latest_closed=[1780819200000, "62000", "62200", "61828", "61938", "10", 1780820099999, "120000000"])
            return fake_klines()
        if "allForceOrders" in url:
            raise RuntimeError("endpoint unavailable")
        if "public/funding-rate" in url:
            return {"data": [{"fundingRate": "0.0002", "nextFundingRate": "0.00025", "fundingTime": "1780820000000", "nextFundingTime": "1780828800000"}]}
        raise AssertionError(f"unexpected URL: {url}")


def compatible_remote_payload(snapshot_time_ms: int, *, public_age_sec: float = 0) -> dict:
    return {
        "mode": "btcusdt_market_snapshot",
        "snapshot_time_ms": snapshot_time_ms,
        "okx_market": {"depth_bands": {"0.5%": {"ask_coverage_pct": 1.0, "bid_coverage_pct": 1.0}}},
        "binance_oi": {"delta": {"15m": {"estimated_notional_delta_usd": 1.0, "exchange_value_delta_usd": 1.0}}},
        "binance_ofi_3m": {"tier": "neutral"},
        "llm_feature_vectors": {
            "features": {
                "order_flow_imbalance_3m": {"tier": "neutral"},
                "liquidity_vacuum_down": {"status": "insufficient", "value": None, "evidence": "observed_down_buckets=1; sample too sparse"},
            }
        },
        "section_cache": {"public_snapshot": {"status": "fresh", "age_sec": public_age_sec}},
    }


class FakeAggTradeClient:
    def get_json(self, url: str, params: dict | None = None) -> object:
        params = params or {}
        start = int(params.get("startTime") or 0)
        if "fromId" in params:
            return []
        if start == 1000:
            return [
                {"a": 1, "T": 1_000, "p": "10", "q": "1", "m": False},
                {"a": 2, "T": 60_999, "p": "10", "q": "1", "m": True},
            ]
        if start == 61000:
            return [
                {"a": 2, "T": 60_999, "p": "10", "q": "1", "m": True},
                {"a": 3, "T": 61_000, "p": "10", "q": "2", "m": False},
            ]
        return []


class FakeAggTradeFallbackClient:
    def __init__(self) -> None:
        self.failed_parallel = False

    def get_json(self, url: str, params: dict | None = None) -> object:
        params = params or {}
        if not self.failed_parallel and int(params.get("startTime") or 0) == 1000:
            self.failed_parallel = True
            raise RuntimeError("parallel shard failed")
        if "fromId" in params:
            return []
        return [
            {"a": 10, "T": 1_000, "p": "10", "q": "1", "m": False},
            {"a": 11, "T": 2_000, "p": "10", "q": "1", "m": True},
        ]


class FakeOkxBtcAccountClient:
    def __init__(self) -> None:
        self.closed = False

    def privateGetAccountPositions(self, params: dict) -> dict:
        return {
            "data": [
                {
                    "instId": "BTC-USDT-SWAP",
                    "pos": "-0.05",
                    "posSide": "net",
                    "avgPx": "62465",
                    "markPx": "62490",
                    "notionalUsd": "31.2",
                    "upl": "-0.01",
                    "liqPx": "64257.9",
                    "lever": "30",
                    "mgnMode": "isolated",
                    "uTime": "1780820942670",
                },
                {"instId": "BTC-USDT-SWAP", "pos": "0", "posSide": "net"},
                {"instId": "ETH-USDT-SWAP", "pos": "-0.52", "posSide": "net"},
            ]
        }

    def privateGetTradeOrdersPending(self, params: dict) -> dict:
        return {
            "data": [
                {
                    "instId": "BTC-USDT-SWAP",
                    "ordId": "ord-1",
                    "state": "live",
                    "side": "sell",
                    "ordType": "limit",
                    "px": "62500",
                    "sz": "0.05",
                    "accFillSz": "0",
                    "reduceOnly": "false",
                    "cTime": "1780820940000",
                    "uTime": "1780820941000",
                },
                {"instId": "ETH-USDT-SWAP", "ordId": "ord-eth"},
            ]
        }

    def privateGetTradeOrdersAlgoPending(self, params: dict) -> dict:
        kind = params.get("ordType")
        if kind == "conditional":
            return {
                "data": [
                    {
                        "instId": "BTC-USDT-SWAP",
                        "algoId": "algo-sl",
                        "state": "live",
                        "side": "buy",
                        "ordType": "conditional",
                        "sz": "0.05",
                        "slTriggerPx": "62545",
                        "reduceOnly": "true",
                    }
                ]
            }
        if kind == "oco":
            return {
                "data": [
                    {
                        "instId": "BTC-USDT-SWAP",
                        "algoId": "algo-tp",
                        "state": "live",
                        "side": "buy",
                        "ordType": "oco",
                        "sz": "0.05",
                        "tpTriggerPx": "61300",
                        "reduceOnly": "true",
                    }
                ]
            }
        return {"data": []}

    def close(self) -> None:
        self.closed = True


def fake_klines(latest_closed: list | None = None) -> list[list]:
    rows: list[list] = []
    base_ms = 1780810000000
    limit = 118 if latest_closed is not None else 119
    for index in range(limit):
        open_price = 60000 + index * 10
        close = open_price + (5 if index % 2 else -3)
        rows.append([base_ms + index * 60000, str(open_price), str(open_price + 20), str(open_price - 20), str(close), "1", base_ms + index * 60000 + 59999, "1000000"])
    if latest_closed is not None:
        rows.append(latest_closed)
    rows.append([base_ms + 119 * 60000, "61200", "61300", "61100", "61250", "1", base_ms + 119 * 60000 + 59999, "1000000"])
    return rows
