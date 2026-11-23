"""Smart Highlights: opt-in CPU visual cues, ranking evidence and safe options."""
import os
import sys
import subprocess
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

import numpy as np

os.environ["PEAKCUT_DATA"] = tempfile.mkdtemp(prefix="pc-smart-highlights-")
os.environ["DATABASE_URL"] = ""
sys.path.insert(0, ".")
from studio import brain, jobs, quality  # noqa: E402


class SmartHighlightsTests(unittest.TestCase):
    def test_option_is_explicit_boolean_and_default_off(self):
        self.assertIs(jobs.DEFAULTS["visual_triage"], False)
        self.assertEqual(jobs.clean_options({"visual_triage": True}), {"visual_triage": True})
        self.assertEqual(jobs.clean_options({"visual_triage": False}), {"visual_triage": False})
        for invalid in ("yes", 1, None):
            with self.subTest(invalid=invalid), self.assertRaises(ValueError):
                jobs.clean_options({"visual_triage": invalid})

    def test_motion_cue_measures_frame_changes_not_game_semantics(self):
        samples = [(float(t), 0.25) for t in range(50)]
        samples[24] = (24.0, 8.0)
        self.assertTrue(brain._motion_spike(samples, 10, 30))
        self.assertFalse(brain._motion_spike(samples, 35, 48))
        self.assertFalse(brain._motion_spike([], 10, 30))

    def test_spoken_candidates_get_explainable_cues_in_ai_prompt(self):
        words = [{"w": "wow!" if i % 13 == 0 else f"word{i}" + ("." if i % 9 == 8 else ""),
                  "s": i * 0.5, "e": i * 0.5 + 0.4} for i in range(120)]
        audio = np.full(62, -30.0)
        motion = [(float(t), 0.2) for t in range(60)]
        motion[29] = (29.0, 11.0)
        ratings = {k: 80 for k in brain.RANK_FIELDS if k != "visual_interest"}
        answer = [{"candidate": 1, "title": "Strong", "hook": "WOW",
                   "reason": "surprising spoken reaction", "ratings": ratings}]
        with patch.object(brain, "ask_ai", return_value=(answer, "gemini-test", 44)) as call:
            clips, info = brain.find_clips(words, audio, 60, max_clips=2, min_len=8, max_len=24,
                                           motion=motion)
        self.assertEqual(info["model"], "gemini-test")
        self.assertEqual(len(clips), 1)
        self.assertTrue(clips[0]["signals"]["motion_spike"])
        self.assertEqual(clips[0]["score"], 80)
        self.assertIn("sampled frame-change spike", call.call_args.args[0])
        self.assertIn("Never invent visual events", call.call_args.args[0])
        self.assertNotIn("visual_interest", clips[0]["ratings"])

    def test_ffmpeg_low_resolution_motion_samples(self):
        with tempfile.TemporaryDirectory(prefix="pc-motion-") as folder:
            video = Path(folder) / "test.mp4"
            # First half is still, second half has real frame movement.
            subprocess.run(["ffmpeg", "-y", "-hide_banner", "-loglevel", "error",
                "-f", "lavfi", "-i", "color=c=black:size=320x180:rate=12:d=4",
                "-f", "lavfi", "-i", "testsrc2=size=320x180:rate=12:d=5",
                "-filter_complex", "[0:v][1:v]concat=n=2:v=1:a=0[v]",
                "-map", "[v]", "-c:v", "mpeg4", str(video)], check=True, timeout=50)
            measured = quality.sampled_motion_map(video, start=0, seconds=9)
            self.assertGreaterEqual(len(measured), 5)
            quiet = [v for t, v in measured if t < 4]
            active = [v for t, v in measured if t >= 5]
            self.assertTrue(quiet and active)
            self.assertGreater(max(active), min(quiet) + 1)
            self.assertTrue(all(t >= 0 and v >= 0 for t, v in measured))
            self.assertEqual(quality.sampled_motion_map(Path(folder) / "no.mp4"), [])

    def test_bad_or_unavailable_video_does_not_break_pipeline(self):
        with tempfile.TemporaryDirectory(prefix="pc-motion-fail-") as folder:
            video = Path(folder) / "bad.mp4"
            video.write_bytes(b"bad video")
            self.assertEqual(quality.sampled_motion_map(video, 0, 8), [])
            self.assertEqual(quality.sampled_motion_map(video, -1, 8), [])
            with patch.object(quality.work_control, "run", side_effect=OSError("unavailable")):
                self.assertEqual(quality.sampled_motion_map(video, 0, 8), [])


if __name__ == "__main__":
    unittest.main()
