from __future__ import annotations

import json
import time
import unittest
from pathlib import Path
from unittest.mock import patch

from fakes import compatible_remote_payload


class BtcusdtRemoteSnapshotTest(unittest.TestCase):
    def test_multi_remote_snapshot_success_marks_http_transport(self) -> None:
        from trading_gateway.application.marketdata import multi

        payload = {
            "mode": "market_snapshot",
            "venue_profile": {"id": "okx-sim"},
            "symbol": "eth",
            "snapshot_time_ms": int(time.time() * 1000),
        }
        with patch.object(multi, "_read_cache", return_value=None), patch.object(multi, "_fetch_http", return_value=payload), patch.object(multi, "_fetch_ssh") as ssh:
            snapshot = multi.load_remote_market_snapshot("okx-sim", "eth", remote=True, max_age_sec=30)

        ssh.assert_not_called()
        self.assertEqual(snapshot["remote_snapshot"]["transport"], "http")
        self.assertEqual(snapshot["venue_profile"]["id"], "okx-sim")

    def test_multi_remote_snapshot_falls_back_to_ssh(self) -> None:
        from trading_gateway.application.marketdata import multi

        payload = {
            "mode": "market_snapshot",
            "venue_profile": {"id": "okx-live"},
            "symbol": "btc",
            "snapshot_time_ms": int(time.time() * 1000),
        }
        with patch.object(multi, "_read_cache", return_value=None), patch.object(multi, "_fetch_http", return_value=None), patch.object(multi, "_fetch_ssh", return_value=payload) as ssh:
            snapshot = multi.load_remote_market_snapshot("okx-live", "btc", remote=True, max_age_sec=30)

        ssh.assert_called_once()
        self.assertEqual(snapshot["remote_snapshot"]["transport"], "ssh")

    def test_remote_snapshot_success_marks_status(self) -> None:
        from trading_gateway.app.config import get_gateway_config
        from trading_gateway.application.marketdata.btcusdt.remote import load_remote_btcusdt_snapshot

        config = get_gateway_config().btcusdt_marketdata
        payload = compatible_remote_payload(1_780_820_000_000)

        with patch("trading_gateway.application.marketdata.btcusdt.remote._read_cached", return_value=None), patch(
            "trading_gateway.application.marketdata.btcusdt.remote._recent_refresh_failure", return_value=False
        ), patch("trading_gateway.application.marketdata.btcusdt.remote._fetch_remote_snapshot", return_value=payload):
            snapshot = load_remote_btcusdt_snapshot(remote=True, remote_host="tokyo", max_age_sec=999999999)

        self.assertEqual(snapshot["remote_snapshot"]["status"], "fresh")
        self.assertEqual(snapshot["remote_snapshot"]["host"], "tokyo")
        self.assertEqual(snapshot["remote_snapshot"]["local_cache"], str(config.local_snapshot_cache))
        self.assertIsNotNone(snapshot["remote_snapshot"]["fetch_ms"])

    def test_remote_snapshot_prefers_http_when_configured(self) -> None:
        from trading_gateway.application.marketdata.btcusdt import remote
        from trading_gateway.application.marketdata.btcusdt.remote import load_remote_btcusdt_snapshot

        payload = compatible_remote_payload(int(time.time() * 1000))
        with patch("trading_gateway.application.marketdata.btcusdt.remote._read_cached", return_value=None), patch(
            "trading_gateway.application.marketdata.btcusdt.remote._recent_refresh_failure", return_value=False
        ), patch.object(remote, "_fetch_remote_snapshot_http", return_value=payload) as http, patch.object(remote, "_fetch_remote_snapshot") as ssh:
            snapshot = load_remote_btcusdt_snapshot(remote=True, remote_host="tokyo", max_age_sec=999999999)

        http.assert_called_once()
        ssh.assert_not_called()
        self.assertEqual(snapshot["remote_snapshot"]["transport"], "http")

    def test_remote_snapshot_falls_back_to_ssh_after_http_failure(self) -> None:
        from trading_gateway.application.marketdata.btcusdt import remote
        from trading_gateway.application.marketdata.btcusdt.remote import load_remote_btcusdt_snapshot

        payload = compatible_remote_payload(int(time.time() * 1000))
        with patch("trading_gateway.application.marketdata.btcusdt.remote._read_cached", return_value=None), patch(
            "trading_gateway.application.marketdata.btcusdt.remote._recent_refresh_failure", return_value=False
        ), patch.object(remote, "_fetch_remote_snapshot_http", return_value=None), patch.object(remote, "_fetch_remote_snapshot", return_value=payload) as ssh:
            snapshot = load_remote_btcusdt_snapshot(remote=True, remote_host="tokyo", max_age_sec=999999999)

        ssh.assert_called_once()
        self.assertEqual(snapshot["remote_snapshot"]["transport"], "ssh")

    def test_remote_snapshot_uses_fresh_local_cache_before_ssh(self) -> None:
        from trading_gateway.application.marketdata.btcusdt.remote import load_remote_btcusdt_snapshot

        payload = compatible_remote_payload(int(time.time() * 1000))
        payload["remote_snapshot"] = {"host": "tokyo"}
        with patch("trading_gateway.application.marketdata.btcusdt.remote._read_cached", return_value=payload), patch(
            "trading_gateway.application.marketdata.btcusdt.remote._fetch_remote_snapshot"
        ) as fetch:
            snapshot = load_remote_btcusdt_snapshot(remote=True, remote_host="tokyo", max_age_sec=30)

        fetch.assert_not_called()
        self.assertEqual((snapshot["remote_snapshot"]["status"], snapshot["remote_snapshot"]["fetch_ms"]), ("cached", 0))

    def test_remote_snapshot_does_not_reuse_untagged_cache(self) -> None:
        from trading_gateway.application.marketdata.btcusdt.remote import load_remote_btcusdt_snapshot

        cached = compatible_remote_payload(int(time.time() * 1000))
        fetched = compatible_remote_payload(int(time.time() * 1000))
        with patch("trading_gateway.application.marketdata.btcusdt.remote._read_cached", return_value=cached), patch(
            "trading_gateway.application.marketdata.btcusdt.remote._recent_refresh_failure", return_value=False
        ), patch("trading_gateway.application.marketdata.btcusdt.remote._fetch_remote_snapshot", return_value=fetched) as fetch:
            snapshot = load_remote_btcusdt_snapshot(remote=True, remote_host="tokyo", max_age_sec=30)

        fetch.assert_called_once()
        self.assertEqual(snapshot["remote_snapshot"]["status"], "fresh")

    def test_remote_snapshot_does_not_reuse_cache_from_different_host(self) -> None:
        from trading_gateway.application.marketdata.btcusdt.remote import load_remote_btcusdt_snapshot

        cached = compatible_remote_payload(int(time.time() * 1000))
        cached["remote_snapshot"] = {"host": "tokyo-a"}
        fetched = compatible_remote_payload(int(time.time() * 1000))
        with patch("trading_gateway.application.marketdata.btcusdt.remote._read_cached", return_value=cached), patch(
            "trading_gateway.application.marketdata.btcusdt.remote._recent_refresh_failure", return_value=False
        ), patch("trading_gateway.application.marketdata.btcusdt.remote._fetch_remote_snapshot", return_value=fetched) as fetch:
            snapshot = load_remote_btcusdt_snapshot(remote=True, remote_host="tokyo-b", max_age_sec=30)

        fetch.assert_called_once()
        self.assertEqual(snapshot["remote_snapshot"]["status"], "fresh")

    def test_remote_snapshot_ignores_incompatible_fresh_cache(self) -> None:
        from trading_gateway.application.marketdata.btcusdt.remote import load_remote_btcusdt_snapshot

        cached = {"mode": "btcusdt_market_snapshot", "snapshot_time_ms": int(time.time() * 1000)}
        fetched = compatible_remote_payload(int(time.time() * 1000))
        with patch("trading_gateway.application.marketdata.btcusdt.remote._read_cached", return_value=cached), patch(
            "trading_gateway.application.marketdata.btcusdt.remote._recent_refresh_failure", return_value=False
        ), patch("trading_gateway.application.marketdata.btcusdt.remote._fetch_remote_snapshot", return_value=fetched) as fetch:
            snapshot = load_remote_btcusdt_snapshot(remote=True, remote_host="tokyo", max_age_sec=30)

        fetch.assert_called_once()
        self.assertEqual(snapshot["remote_snapshot"]["status"], "fresh")

    def test_remote_snapshot_rejects_old_depth_or_oi_shapes(self) -> None:
        from trading_gateway.application.marketdata.btcusdt.remote import load_remote_btcusdt_snapshot

        for key in ("okx_market", "binance_oi"):
            cached = compatible_remote_payload(int(time.time() * 1000))
            cached.pop(key)
            fetched = compatible_remote_payload(int(time.time() * 1000))
            with patch("trading_gateway.application.marketdata.btcusdt.remote._read_cached", return_value=cached), patch(
                "trading_gateway.application.marketdata.btcusdt.remote._recent_refresh_failure", return_value=False
            ), patch("trading_gateway.application.marketdata.btcusdt.remote._fetch_remote_snapshot", return_value=fetched) as fetch:
                snapshot = load_remote_btcusdt_snapshot(remote=True, remote_host="tokyo", max_age_sec=30)

            fetch.assert_called_once()
            self.assertEqual(snapshot["remote_snapshot"]["status"], "fresh")

    def test_remote_snapshot_rejects_old_sparse_liquidity_gap_score(self) -> None:
        from trading_gateway.application.marketdata.btcusdt.remote import load_remote_btcusdt_snapshot

        cached = compatible_remote_payload(int(time.time() * 1000))
        cached["llm_feature_vectors"]["features"]["liquidity_vacuum_down"] = {
            "value": 1.0,
            "status": "high",
            "evidence": "observed_down_buckets=1; down_avg=0; bucket_median=1",
        }
        fetched = compatible_remote_payload(int(time.time() * 1000))
        with patch("trading_gateway.application.marketdata.btcusdt.remote._read_cached", return_value=cached), patch(
            "trading_gateway.application.marketdata.btcusdt.remote._recent_refresh_failure", return_value=False
        ), patch("trading_gateway.application.marketdata.btcusdt.remote._fetch_remote_snapshot", return_value=fetched) as fetch:
            snapshot = load_remote_btcusdt_snapshot(remote=True, remote_host="tokyo", max_age_sec=30)

        fetch.assert_called_once()
        self.assertEqual(snapshot["remote_snapshot"]["status"], "fresh")

    def test_remote_snapshot_rejects_recent_stale_cache_for_local_fallback(self) -> None:
        from trading_gateway.application.marketdata.btcusdt.remote import load_remote_btcusdt_snapshot

        payload = compatible_remote_payload(int((time.time() - 40) * 1000))
        with patch("trading_gateway.application.marketdata.btcusdt.remote._read_cached", return_value=payload), patch(
            "trading_gateway.application.marketdata.btcusdt.remote._fetch_remote_snapshot", return_value=None
        ):
            snapshot = load_remote_btcusdt_snapshot(remote=True, remote_host="tokyo", max_age_sec=30)

        self.assertIsNone(snapshot)

    def test_remote_snapshot_rejects_old_stale_cache_for_local_fallback(self) -> None:
        from trading_gateway.application.marketdata.btcusdt.remote import load_remote_btcusdt_snapshot

        payload = compatible_remote_payload(int((time.time() - 120) * 1000))
        with patch("trading_gateway.application.marketdata.btcusdt.remote._read_cached", return_value=payload), patch(
            "trading_gateway.application.marketdata.btcusdt.remote._fetch_remote_snapshot", return_value=None
        ):
            snapshot = load_remote_btcusdt_snapshot(remote=True, remote_host="tokyo", max_age_sec=30)

        self.assertIsNone(snapshot)

    def test_remote_snapshot_rejects_stale_public_section(self) -> None:
        from trading_gateway.application.marketdata.btcusdt.remote import load_remote_btcusdt_snapshot

        cached = compatible_remote_payload(int(time.time() * 1000), public_age_sec=40)
        fetched = compatible_remote_payload(int(time.time() * 1000), public_age_sec=5)
        with patch("trading_gateway.application.marketdata.btcusdt.remote._read_cached", return_value=cached), patch(
            "trading_gateway.application.marketdata.btcusdt.remote._recent_refresh_failure", return_value=False
        ), patch("trading_gateway.application.marketdata.btcusdt.remote._fetch_remote_snapshot", return_value=fetched) as fetch:
            snapshot = load_remote_btcusdt_snapshot(remote=True, remote_host="tokyo", max_age_sec=30)

        fetch.assert_called_once()
        self.assertEqual(snapshot["remote_snapshot"]["status"], "fresh")

    def test_remote_snapshot_counts_outer_age_against_public_section(self) -> None:
        from trading_gateway.application.marketdata.btcusdt.remote import load_remote_btcusdt_snapshot

        cached = compatible_remote_payload(int((time.time() - 25) * 1000), public_age_sec=10)
        fetched = compatible_remote_payload(int(time.time() * 1000), public_age_sec=5)
        with patch("trading_gateway.application.marketdata.btcusdt.remote._read_cached", return_value=cached), patch(
            "trading_gateway.application.marketdata.btcusdt.remote._fetch_remote_snapshot", return_value=fetched
        ) as fetch:
            snapshot = load_remote_btcusdt_snapshot(remote=True, remote_host="tokyo", max_age_sec=30)

        fetch.assert_called_once()
        self.assertEqual(snapshot["remote_snapshot"]["status"], "fresh")

    def test_remote_snapshot_refresh_failure_backoff_uses_local_fallback(self) -> None:
        from trading_gateway.application.marketdata.btcusdt.remote import load_remote_btcusdt_snapshot

        payload = compatible_remote_payload(int((time.time() - 40) * 1000))
        with patch("trading_gateway.application.marketdata.btcusdt.remote._read_cached", return_value=payload), patch(
            "trading_gateway.application.marketdata.btcusdt.remote._recent_refresh_failure", return_value=True
        ), patch("trading_gateway.application.marketdata.btcusdt.remote._fetch_remote_snapshot") as fetch:
            snapshot = load_remote_btcusdt_snapshot(remote=True, remote_host="tokyo", max_age_sec=30)

        fetch.assert_not_called()
        self.assertIsNone(snapshot)

    def test_remote_snapshot_tags_cache_origin_after_fetch(self) -> None:
        from trading_gateway.application.marketdata.btcusdt import remote

        writes = []
        payload = compatible_remote_payload(int(time.time() * 1000))
        with patch("subprocess.run") as run, patch.object(remote, "write_snapshot", side_effect=lambda path, data: writes.append(data)):
            run.return_value.returncode = 0
            run.return_value.stdout = json.dumps(payload)
            fetched = remote._fetch_remote_snapshot("tokyo", "/tmp/snapshot.json", Path("var/tmp-cache.json"))

        self.assertIsNotNone(fetched)
        self.assertEqual(writes[0]["remote_snapshot"]["host"], "tokyo")

    def test_http_fetch_requires_token_and_valid_json(self) -> None:
        from trading_gateway.application.marketdata.btcusdt import remote

        with patch.dict("os.environ", {}, clear=True):
            self.assertIsNone(remote._fetch_remote_snapshot_http("tokyo", "http://example/snapshot", "TBOT_MARKETDATA_TOKEN", 0.1, "", Path("var/cache.json")))
        with patch.dict("os.environ", {"TBOT_MARKETDATA_TOKEN": "secret"}), patch("trading_gateway.application.marketdata.btcusdt.remote.urlopen") as urlopen:
            urlopen.return_value.__enter__.return_value.read.return_value = b"not-json"
            self.assertIsNone(remote._fetch_remote_snapshot_http("tokyo", "http://example/snapshot", "TBOT_MARKETDATA_TOKEN", 0.1, "", Path("var/cache.json")))

    def test_http_fetch_can_use_configured_proxy(self) -> None:
        from trading_gateway.application.marketdata.btcusdt import remote

        writes = []
        payload = compatible_remote_payload(int(time.time() * 1000))
        with patch.dict("os.environ", {"TBOT_MARKETDATA_TOKEN": "secret"}), patch.object(remote, "_open_http") as open_http, patch.object(
            remote, "write_snapshot", side_effect=lambda path, data: writes.append(data)
        ):
            open_http.return_value.__enter__.return_value.read.return_value = json.dumps(payload).encode()
            fetched = remote._fetch_remote_snapshot_http("tokyo", "http://example/snapshot", "TBOT_MARKETDATA_TOKEN", 0.1, "http://127.0.0.1:1056", Path("var/cache.json"))

        self.assertIsNotNone(fetched)
        self.assertEqual(open_http.call_args.args[2], "http://127.0.0.1:1056")
        self.assertEqual(writes[0]["remote_snapshot"]["transport"], "http")
