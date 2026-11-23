"""Tests NASA/JPL numeric-only subject discovery and recurring anti-duplicate Shorts."""
import io
import json
import os
import sys
import tempfile
import time
import unittest
from datetime import date, datetime, timedelta, timezone
from pathlib import Path
from unittest.mock import patch

sys.path.insert(0, ".")
os.environ["PEAKCUT_DATA"] = tempfile.mkdtemp(prefix="pc-jpl-data-")
os.environ["DATABASE_URL"] = ""
from studio import original_shorts, space_data  # noqa: E402
from tools import original_youtube_autopilot as auto  # noqa: E402

TODAY = date(2026, 11, 23)
VALID = {"signature": {"version": "1.5", "source": "NASA/JPL SBDB Close Approach Data API"},
         "fields": ["des", "cd", "dist", "dist_min", "dist_max", "v_rel"],
         "data": [
             ["2026 AB12", "2026-Nov-28 11:24", "0.0098", "0.00979", "0.00981", "13.5"],
             ["2026 CD34", "2026-Nov-30 18:23", "0.012", "0.01198", "0.01202", "19.6"],
             ["2026 ZX", "2026-Nov-29 18:23", "0.04", "0.039", "0.042", "19.6"],
         ]}


class MockHttp:
    status = 200

    def __init__(self, response):
        self.data = json.dumps(response).encode()

    def __enter__(self):
        return self

    def __exit__(self, *_):
        return False

    def read(self, n):
        return self.data[:n]


class SpaceDataTests(unittest.TestCase):
    def test_documented_endpoint_requires_no_api_key(self):
        url = space_data.source_url(TODAY)
        self.assertTrue(url.startswith("https://ssd-api.jpl.nasa.gov/cad.api?"))
        self.assertIn("date-min=2026-11-23", url)
        self.assertIn("body=Earth", url)
        self.assertNotIn("token=", url)
        self.assertNotIn("key=", url)

    def test_validated_numerical_approach_and_factual_arabic_text(self):
        rows = space_data.parse_approaches(VALID, TODAY)
        self.assertEqual(len(rows), 2)
        self.assertLess(rows[0]["lunar"], 5)
        self.assertAlmostEqual(rows[0]["km"], 0.0098 * space_data.AU_KM)
        e = space_data.episode_for(rows[0], space_data.source_url(TODAY))
        self.assertEqual(e["theme"], "asteroid")
        self.assertIn("مليون كيلومتر", e["narration"])
        self.assertIn("لا تعني اصطداماً", e["narration"])
        self.assertEqual(len({x["headline"] for x in e["scenes"]}), 3)
        self.assertEqual(e["data"]["date"], "2026-11-28")
        self.assertLess(len(e["title"]), 100)
        with tempfile.TemporaryDirectory(prefix="pc-jpl-frames-") as folder:
            original_shorts._scene(e, 0, Path(folder) / "asteroid.png")
            self.assertGreater((Path(folder) / "asteroid.png").stat().st_size, 4000)

    def test_version_change_or_malicious_data_stops(self):
        wrong = dict(VALID, signature={"version": "1.9", "source": "NASA/JPL"})
        with self.assertRaisesRegex(ValueError, "format"):
            space_data.parse_approaches(wrong, TODAY)
        wrong = dict(VALID, fields=["des", "dist"], data=[])
        with self.assertRaisesRegex(ValueError, "fields"):
            space_data.parse_approaches(wrong, TODAY)
        wrong = dict(VALID, data=[["<script>alert(1)</script>", "2026-Nov-28 11:24",
                                   "0.009", "0.008", "0.010", "12"]])
        self.assertEqual(space_data.parse_approaches(wrong, TODAY), [])

    def test_fetch_uses_only_official_json_and_size_limit(self):
        def fake(request, timeout=0):
            self.assertEqual(timeout, 12)
            self.assertEqual(request.full_url.split("?")[0], space_data.URL)
            return MockHttp(VALID)
        items, url = space_data.fetch_approaches(TODAY, opener=fake)
        self.assertEqual(len(items), 2)
        self.assertIn("fullname=true", url)

    def test_curated_first_then_unique_jpl_and_no_repeat(self):
        fixed = original_shorts.catalog()
        state = {"episodes": {ep["id"]: {"status": "published"} for ep in fixed}}
        mock = lambda state, today: space_data.episode_for(
            space_data.parse_approaches(VALID, TODAY)[0], space_data.source_url(TODAY))
        with patch.dict(os.environ, {"PEAKCUT_ORIGINAL_LIVE_JPL": "1"}):
            fresh = auto.choose_episode(fixed, state, today=TODAY, discover=mock)
        self.assertTrue(fresh["id"].startswith("jpl-"))
        self.assertIsNone(auto.choose_episode(fixed, state, requested=fixed[0]["id"], today=TODAY, discover=mock))
        with patch.dict(os.environ, {"PEAKCUT_ORIGINAL_LIVE_JPL": "0"}):
            self.assertIsNone(auto.choose_episode(fixed, state, today=TODAY, discover=mock))

    def test_discovery_respects_cadence_and_object_novelty(self):
        loading = lambda _: (space_data.parse_approaches(VALID, TODAY), space_data.source_url(TODAY))
        one = space_data.discover_unused({"episodes": {}}, TODAY, loader=loading)
        self.assertIn("2026 AB12", one["title"])
        history = {"episodes": {
            one["id"]: {"started": time.time(), "source_designation": "2026 AB12"}}}
        self.assertIsNone(space_data.discover_unused(history, TODAY, loader=loading))
        history["episodes"][one["id"]]["started"] = time.time() - 11 * 86400
        second = space_data.discover_unused(history, TODAY, loader=loading)
        self.assertIn("2026 CD34", second["title"])
        self.assertNotEqual(second["id"], one["id"])

    def test_no_data_does_not_create_an_episode(self):
        no = dict(VALID, data=[])
        self.assertEqual(space_data.parse_approaches(no, TODAY), [])
        self.assertIsNone(space_data.discover_unused({"episodes": {}}, TODAY, loader=lambda _: ([], "x")))


if __name__ == "__main__":
    unittest.main()
