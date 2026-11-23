"""Offline verification of original-science Shorts, rights and posting safety."""
import json
import math
import os
import sys
import tempfile
import unittest
import wave
from pathlib import Path
from unittest.mock import patch
import numpy as np
from PIL import Image

sys.path.insert(0, ".")
os.environ["PEAKCUT_DATA"] = tempfile.mkdtemp(prefix="pc-original-science-")
os.environ["DATABASE_URL"] = ""
from studio import original_shorts  # noqa: E402
from tools import original_youtube_autopilot as auto  # noqa: E402


class OriginalYouTubeTests(unittest.TestCase):
    def test_editorially_checked_facts_and_unique_storyboards(self):
        rows = original_shorts.catalog()
        self.assertEqual(len(rows), 4)
        self.assertEqual(len({r["id"] for r in rows}), 4)
        for e in rows:
            self.assertTrue(e["source"].startswith("https://science.nasa.gov/"))
            self.assertEqual(len(e["scenes"]), 3)
            self.assertEqual(len({s["headline"] for s in e["scenes"]}), 3)
            self.assertIn("#Shorts", auto.description(e))

    def test_reject_repeat_publishing_and_unknown_voices(self):
        ep = original_shorts.catalog()
        doc = {"episodes": {ep[0]["id"]: {"status": "upload_unknown"}}}
        self.assertEqual(auto.pick(ep, doc)["id"], ep[1]["id"])
        self.assertIsNone(auto.pick(ep, doc, ep[0]["id"]))
        with patch.dict(os.environ, {auto.AUTO_FLAG: "0"}):
            with self.assertRaisesRegex(RuntimeError, "disabled"):
                auto.require_publishing_config()
        with patch.dict(os.environ, {auto.AUTO_FLAG: "1", auto.LICENSE_FLAG: "0",
                                      "PEAKCUT_ORIGINAL_BUILTIN_VOICE": "0"}):
            with self.assertRaisesRegex(RuntimeError, "commercial"):
                auto.require_publishing_config()

    def test_pure_local_h264_aac_short_and_arabic_storyboard(self):
        with tempfile.TemporaryDirectory(prefix="pc-original-render-") as folder:
            dst = Path(folder)
            wav = dst / "synthetic-qa.wav"
            # Synthetic audio is for CI only; the publication workflow always
            # obtains real narration from the externally licensed Piper voice.
            with wave.open(str(wav), "wb") as a:
                a.setnchannels(1)
                a.setsampwidth(2)
                a.setframerate(16000)
                a.writeframes(b"\x00\x00" * (16000 * 4))
            path = original_shorts.render(original_shorts.catalog()[0], dst, wav)
            self.assertTrue(path.is_file())
            self.assertGreater(path.stat().st_size, 4096)
            self.assertTrue((dst / "space-light-delay-scene-0.png").is_file())
            diagrams = []
            for i in range(3):
                with Image.open(dst / f"space-light-delay-scene-{i}.png") as frame:
                    diagrams.append(np.asarray(frame.crop((110, 690, 990, 1320)).convert("RGB"), dtype=np.int16))
            for a, b in zip(diagrams, diagrams[1:]):
                different = float(np.mean(np.max(np.abs(a - b), axis=2) > 32))
                self.assertGreater(different, 0.06, "Scenes must explain different concepts visually")
            self.assertEqual(original_shorts._duration(wav), 4.0)

    def test_publish_once_never_double_uploads_or_crosses_account(self):
        rows = original_shorts.catalog()
        with tempfile.TemporaryDirectory(prefix="pc-original-state-") as root:
            data = Path(root)
            with patch.dict(os.environ, {
                auto.AUTO_FLAG: "1", auto.LICENSE_FLAG: "1",
                "PEAKCUT_ORIGINAL_USER_ID": "editor-user",
                "PEAKCUT_ORIGINAL_YOUTUBE_ACCOUNT": "yt_my_channel",
            }):
                with patch.object(auto.config, "user_dir", return_value=data):
                    with patch.object(auto.accounts, "get", return_value={
                        "id": "yt_my_channel", "platform": "youtube", "name": "Private QA"}):
                        with patch.object(auto, "render_episode", return_value=data / "synthetic.mp4") as make:
                            with patch.object(auto.accounts, "youtube_upload", return_value=(
                                "youtube-demo-video", "https://youtube.com/shorts/youtube-demo-video")) as upload:
                                one = auto.publish_once(data / "placeholder.onnx", today="2026-11-23")
                                self.assertEqual(one["status"], "published")
                                self.assertEqual(upload.call_args.kwargs["category_id"], "27")
                                self.assertEqual(auto.publish_once(data / "placeholder.onnx", today="2026-11-23")["status"], "daily_limit")
                                two = auto.publish_once(data / "placeholder.onnx", today="2026-11-24")
                                self.assertEqual(two["status"], "published")
                                self.assertEqual(make.call_count, 2)
                                self.assertEqual(upload.call_count, 2)
            state = json.loads((data / auto.AUTO_DIR / "ledger.json").read_text())
            self.assertEqual(len(state["episodes"]), 2)
            self.assertTrue(all(x["status"] == "published" for x in state["episodes"].values()))


    def test_actual_views_sync_does_not_treat_missing_statistics_as_zero(self):
        with tempfile.TemporaryDirectory(prefix="pc-original-views-") as root:
            data = Path(root)
            ledger_dir = data / auto.AUTO_DIR
            auto._write_state(ledger_dir, {"episodes": {
                "old": {"status": "published", "video_id": "abc", "verified_views": 105},
                "recent": {"status": "published", "video_id": "def"},
            }})
            with patch.dict(os.environ, {
                auto.AUTO_FLAG: "1", auto.LICENSE_FLAG: "1",
                "PEAKCUT_ORIGINAL_USER_ID": "owner_q",
                "PEAKCUT_ORIGINAL_YOUTUBE_ACCOUNT": "yt_qa",
            }), patch.object(auto.config, "user_dir", return_value=data), \
                 patch.object(auto.accounts, "get", return_value={"id": "yt_qa", "platform": "youtube"}), \
                 patch.object(auto.accounts, "youtube_views", return_value={"abc": 1234}) as query:
                result = auto.refresh_views()
                self.assertEqual(result["updated"], 1)
                self.assertEqual(result["unavailable"], 1)
                query.assert_called_once()
                state = auto._read_state(ledger_dir)
                self.assertEqual(state["episodes"]["old"]["verified_views"], 1234)
                self.assertNotIn("verified_views", state["episodes"]["recent"])
                self.assertGreater(state["last_views_sync"], 0)



if __name__ == "__main__":
    unittest.main()
