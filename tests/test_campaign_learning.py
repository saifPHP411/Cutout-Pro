"""Campaign isolation and safe chronological model comparison."""
import os
import sys
import tempfile
import unittest
from datetime import datetime, timezone

import numpy as np

sys.path.insert(0, ".")
data = tempfile.TemporaryDirectory(prefix="peakcut-campaign-learning-")
os.environ.update(PEAKCUT_DATA=data.name, DATABASE_URL="")
from unittest.mock import patch
from studio import accounts, alerts, campaigns, ctx, learning


class CampaignTests(unittest.TestCase):
    def test_rules_version_platform_source_and_financial_gates(self):
        with ctx.as_user("u_campaigna"):
            camp = campaigns.save({"name": "April", "creator": "Creator", "source": "upload",
                                   "platforms": ["youtube"], "min_len": 15, "max_len": 30, "rate": 2})
            clip = {"start": 0, "end": 20, "status": "done"}
            job = {"options": {"campaign": camp["id"]}, "source": {"type": "file"}, "clips": [clip]}
            self.assertIn("Confirm", campaigns.policy(job, clip))
            clip["campaign_approval"] = camp["rules_version"]
            self.assertEqual(campaigns.policy(job, clip, "youtube"), "")
            self.assertIn("platform", campaigns.policy(job, clip, "tiktok"))
            clip["end"] = 60
            self.assertIn("duration", campaigns.policy(job, clip))
            clip["end"] = 20
            campaigns.save({"branding": "No logos"}, camp["id"])
            self.assertIn("Confirm", campaigns.policy(job, clip))
            self.assertIsNone(campaigns.list_for([job])[0]["estimated"])
            with self.assertRaises(ValueError):
                campaigns.verify_financial(camp["id"], {"eligible_views": 100, "confirmed": 1})
            campaigns.verify_financial(camp["id"], {"eligible_views": 1500, "confirmed": 1,
                                                       "payment": "paid", "evidence": "Payment 123"})
            self.assertEqual(campaigns.list_for([job])[0]["estimated"], 3)
        with ctx.as_user("u_campaignb"):
            self.assertIsNone(campaigns.get(camp["id"]))
            self.assertEqual(campaigns.list_for([job]), [])

    def test_realistic_record_requires_consent_and_a_complete_horizon(self):
        with ctx.as_user("u_learnmanual"):
            clips = {"id": "clip01", "status": "done", "ratings": {k: 72 for k in learning.FIELDS}, "start": 0, "end": 23}
            job = {"id": "test", "created": 1000}
            def at(ts):
                return datetime.fromtimestamp(ts, timezone.utc).isoformat()
            args = {"published_at": at(1100), "observed_at": at(1100 + learning.HORIZON),
                    "views": 231, "retention": 41.1, "evidence": "Creator dashboard"}
            with self.assertRaises(ValueError):
                learning.add(job, clips, args)
            learning.consent(True)
            with self.assertRaises(ValueError):
                learning.add(job, clips, {**args, "observed_at": at(2200)})
            self.assertEqual(learning.add(job, clips, args)["observations"], 1)

    def test_telegram_job_opt_in_is_separate_from_legacy_alerts(self):
        with ctx.as_user("u_job_alerts"):
            accounts.save_settings({"telegram_job_token": "dummy", "telegram_job_chat": "123"})
            self.assertFalse(alerts.configured())
            self.assertIs(accounts.public_settings()["telegram_job_token"], True)
            with patch.object(alerts.threading, "Thread") as sender:
                alerts.job_message("fixture completed")
                sender.assert_not_called()
                accounts.save_settings({"telegram_job_alerts": True})
                alerts.job_message("fixture completed")
                sender.assert_called_once()

    def test_fit_select_and_rollback_with_ordered_disjoint_sources(self):
        with ctx.as_user("u_learnfit"):
            learning.consent(True)
            model = learning._read()
            rng = np.random.default_rng(77)
            for i in range(44):
                x = rng.integers(0, 95, size=len(learning.FIELDS)).tolist()
                t = 1000 + i * 10 * 86400
                model["observations"][str(i)] = {"source": str(i), "published": t,
                                                 "observed": t + learning.HORIZON,
                                                 "ratings": dict(zip(learning.FIELDS, x)),
                                                 "retention": x[0]}
            learning._save(model)
            train, test = learning._split(list(model["observations"].values()))
            self.assertLess(max(r["observed"] for r in train), min(r["published"] for r in test))
            self.assertFalse({r["source"] for r in train} & {r["source"] for r in test})
            comparison = learning.evaluate()
            self.assertTrue(comparison["eligible"])
            self.assertEqual(learning.active()[1], "default")
            learning.select(comparison["id"])
            self.assertEqual(learning.active()[1], comparison["id"])
            learning.select("default")
            self.assertEqual(learning.active()[1], "default")


if __name__ == "__main__":
    unittest.main()
