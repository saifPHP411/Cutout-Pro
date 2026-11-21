"""No-network security and parsing tests for Windsor.ai's read-only importer."""
import os
import time
import unittest
from unittest.mock import patch

import httpx
from fastapi.testclient import TestClient

from studio import config, ctx, learning, posting, server, windsor

CHANNEL = "UC" + "A" * 22  # synthetic channel identifier, never a user account
VIDEO = "TestingID01"
PUBLISHED = time.time() - 20 * 86400
ACCOUNT = {"platform": "youtube", "id": "yt_" + CHANNEL}


class WindsorTests(unittest.TestCase):
    def setUp(self):
        self.environ = patch.dict(os.environ, {"PEAKCUT_WINDSOR_YOUTUBE_CHANNEL_ID": CHANNEL})
        self.environ.start()
        self.secret = patch.object(config, "_secret", return_value="private-windsor-test-key")
        self.secret.start()

    def tearDown(self):
        self.secret.stop()
        self.environ.stop()

    def fake(self, rows, status=200):
        def request(url, **kwargs):
            self.assertEqual(url, windsor.API)
            self.assertEqual(kwargs["params"]["select_accounts"], CHANNEL)
            self.assertEqual(kwargs["params"]["fields"], windsor.FIELDS)
            self.assertEqual(kwargs["params"]["api_key"], "private-windsor-test-key")
            self.assertEqual(kwargs["params"]["filter"], '[["video","eq","TestingID01"]]')
            self.assertFalse(kwargs["follow_redirects"])
            return httpx.Response(status, json={"data": rows})
        return request

    def test_matching_row_and_exact_dates(self):
        row = {"account_id": CHANNEL, "video": VIDEO, "views": "200", "average_view_percentage": 65.3}
        result = windsor.youtube(ACCOUNT, VIDEO, PUBLISHED, request=self.fake([row]))
        self.assertEqual(result["views"], 200)
        self.assertEqual(result["retention"], 65.3)
        self.assertTrue(result["evidence"].startswith("Windsor.ai YouTube"))

    def test_windsor_selector_may_differ_from_public_channel_id(self):
        record = {"account_id": CHANNEL, "video": VIDEO, "views": 20,
                  "average_view_percentage": "77.2"}
        with patch.dict(os.environ, {"PEAKCUT_WINDSOR_ACCOUNT_ID": "external123"}):
            def request(url, **kwargs):
                self.assertEqual(kwargs["params"]["select_accounts"], "external123")
                return httpx.Response(200, json={"data": [record]})
            self.assertEqual(windsor.youtube(ACCOUNT, VIDEO, PUBLISHED, request=request)["views"], 20)

    def test_wrong_owner_channel_is_rejected_before_network(self):
        with self.assertRaisesRegex(ValueError, "binding"):
            windsor.youtube({"platform": "youtube", "id": "yt_UC______________________"}, VIDEO,
                            PUBLISHED, request=lambda *a, **k: self.fail("unexpected network call"))

    def test_absent_or_ambiguous_data_fail_closed(self):
        row = {"account_id": CHANNEL, "video": VIDEO, "views": 100, "average_view_percentage": 67}
        for rows in ([], [row, row], [{**row, "account_id": "UC_other"}], [{**row, "video": "OtherTest01"}]):
            with self.subTest(rows=rows), self.assertRaisesRegex(ValueError, "unambiguous"):
                windsor.youtube(ACCOUNT, VIDEO, PUBLISHED, request=self.fake(rows))

    def test_bad_metrics_are_rejected(self):
        for views, retention in ((0, 50), (True, 50), (10.5, 50), (10, "nan"), (10, -5), (10, 2000)):
            row = {"account_id": CHANNEL, "video": VIDEO, "views": views,
                   "average_view_percentage": retention}
            with self.subTest(views=views, retention=retention), self.assertRaisesRegex(ValueError, "invalid views"):
                windsor.youtube(ACCOUNT, VIDEO, PUBLISHED, request=self.fake([row]))

    def test_premature_cohort_is_rejected(self):
        with self.assertRaisesRegex(ValueError, "three days"):
            windsor.youtube(ACCOUNT, VIDEO, time.time() - 5 * 86400,
                            request=lambda *a, **k: self.fail("unexpected network call"))

    def test_api_errors_do_not_disclose_key(self):
        def request(*a, **k):
            raise httpx.ConnectError("private-windsor-test-key", request=httpx.Request("GET", windsor.API))
        with self.assertRaises(RuntimeError) as err:
            windsor.youtube(ACCOUNT, VIDEO, PUBLISHED, request=request)
        self.assertNotIn("private-windsor-test-key", str(err.exception))
        with self.assertRaises(RuntimeError) as err:
            windsor.youtube(ACCOUNT, VIDEO, PUBLISHED, request=self.fake([], status=401))
        self.assertNotIn("private-windsor-test-key", str(err.exception))

    def test_absent_api_key_fails_before_network(self):
        with patch.object(config, "_secret", return_value=""):
            self.assertFalse(windsor.configured())
            with self.assertRaisesRegex(ValueError, "not configured"):
                windsor.youtube(ACCOUNT, VIDEO, PUBLISHED,
                                request=lambda *a, **k: self.fail("unexpected network call"))

    def test_automatic_import_is_owner_bound_idempotent_and_opt_in(self):
        now = time.time()
        posted = {"id": "p1", "user": "u_windsor", "status": "posted", "platform": "youtube",
                  "account": "yt_" + CHANNEL, "remote_id": VIDEO, "posted_at": now - 20*86400,
                  "job": "job-1", "clip": "clip-1"}
        other = {**posted, "id": "p2", "account": "yt_UC______________________"}
        job = {"id": "job-1", "created": posted["posted_at"]-100, "clips": [
            {"id": "clip-1", "status": "done", "ratings": {k: 50 for k in learning.FIELDS},
             "start": 0, "end": 12}]}
        report = {"published_at": "2026-11-01T10:00:00+00:00", "observed_at": "2026-11-12T10:00:00+00:00",
                  "views": 300, "retention": 52.0, "evidence": "fixture"}
        with (ctx.as_user("u_windsor"),
              patch.dict(os.environ, {"PEAKCUT_WINDSOR_AUTOPULL": "1"}),
              patch.object(posting, "_mine", return_value=[posted, other]),
              patch.object(posting.accounts, "get", return_value=ACCOUNT),
              patch.object(posting.jobs, "get", return_value=job),
              patch.object(learning, "status", return_value={"consent": True}),
              patch.object(learning, "_read", return_value={"observations": {}}),
              patch.object(learning, "add", return_value={"observations": 1}) as write,
              patch.object(windsor, "youtube", return_value=report) as read):
            self.assertEqual(posting._windsor_due(now), 1)
            read.assert_called_once()
            write.assert_called_once()
            self.assertEqual(write.call_args.kwargs["provider"], "windsor_youtube")
            self.assertTrue(posted["windsor_imported"])
            self.assertEqual(posting._windsor_due(now), 0)
        self.assertNotIn("windsor_imported", other)

    def test_automatic_import_never_overwrites_manual_measurement(self):
        now = time.time()
        posted = {"id": "p1", "user": "u_windsor", "status": "posted", "platform": "youtube",
                  "account": "yt_" + CHANNEL, "remote_id": VIDEO, "posted_at": now - 20*86400,
                  "job": "job-1", "clip": "clip-1"}
        job = {"id": "job-1", "created": posted["posted_at"]-100, "clips": [
            {"id": "clip-1", "status": "done", "ratings": {k: 50 for k in learning.FIELDS}}]}
        with (ctx.as_user("u_windsor"),
              patch.dict(os.environ, {"PEAKCUT_WINDSOR_AUTOPULL": "1"}),
              patch.object(posting, "_mine", return_value=[posted]),
              patch.object(posting.accounts, "get", return_value=ACCOUNT),
              patch.object(posting.jobs, "get", return_value=job),
              patch.object(learning, "status", return_value={"consent": True}),
              patch.object(learning, "_read", return_value={"observations": {"job-1:clip-1": {}}}),
              patch.object(windsor, "youtube") as read):
            self.assertEqual(posting._windsor_due(now), 0)
            read.assert_not_called()
            self.assertTrue(posted["windsor_imported"])

    def test_private_status_requires_auth_when_accounts_enabled(self):
        # The test server's auth gate rejects unauthenticated API reads.
        with patch.object(config, "ACCOUNTS", True):
            response = TestClient(server.app).get("/api/pro/windsor/status")
            self.assertIn(response.status_code, (401, 403))


if __name__ == "__main__":
    unittest.main()
