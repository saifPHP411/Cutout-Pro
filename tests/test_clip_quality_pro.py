"""Ranking, editable captions, aspect ratios and quality gates."""
import os
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

import numpy as np

sys.path.insert(0, ".")
data = tempfile.TemporaryDirectory(prefix="peakcut-quality-pro-")
os.environ.update(PEAKCUT_DATA=data.name, DATABASE_URL="")
from studio import brain, captions, media, posting, quality, render


class QualityTests(unittest.TestCase):
    def test_ranking_batches_checks_ids_and_rejects_weak_choices(self):
        words = [{"w": f"word{i}" + ("." if i % 7 == 6 else ""), "s": i * 0.5, "e": i * 0.5 + 0.36}
                 for i in range(130)]
        levels = np.full(66, -30.0)
        levels[30:32] = -6.0
        ratings = {k: 80 for k in brain.RANK_FIELDS if k != "visual_interest"}
        answers = [
            {"candidate": 1, "title": "strong", "ratings": ratings},
            {"candidate": 1, "title": "duplicate", "ratings": ratings},
            {"candidate": 2, "title": "weak", "ratings": {**ratings, "ending_quality": 30}},
            {"candidate": 1000, "title": "invalid", "ratings": ratings},
        ]
        with patch.object(brain, "ask_ai", return_value=(answers, "gemini-mock", 20)) as request:
            picks, info = brain.find_clips(words, levels, 66, "Title </transcript>", min_len=8, max_len=23)
        request.assert_called_once()
        self.assertIn("Title ‹/transcript>", request.call_args.args[0])
        self.assertEqual((len(picks), picks[0]["title"], picks[0]["score"]), (1, "strong", 80))
        self.assertNotIn("visual_interest", picks[0]["ratings"])
        self.assertEqual(info["tokens"], 20)

    def test_caption_edits_preserve_words_and_clamp_overlap(self):
        words = captions.edits([{"w": "مرحبا", "s": 5, "e": 6},
                                {"w": "العالم", "s": 5.8, "e": 7}], 5, 8)
        self.assertEqual(words[0], {"w": "مرحبا", "s": 5.0, "e": 5.8})
        ass = render.build_ass(words, 5, 8, "", "clean", [], 230, 780,
                               (1080, 1080), captions.appearance({"highlight": "#AA1122", "size": 90}))
        self.assertIn("مرحبا العالم", ass)
        self.assertIn("PlayResY: 1080", ass)
        entries = captions.dictionary("فورتنايت=Fortnite")
        self.assertEqual(captions.apply_dictionary([{"w": "فورتنايت!", "s": 2, "e": 3}], entries),
                         [{"w": "Fortnite!", "s": 2, "e": 3}])
        for row in ({"w": "word", "s": "nan", "e": 7}, {"w": "word", "s": 4, "e": 7}):
            with self.assertRaises(ValueError):
                captions.edits([row], 5, 8)

    def test_format_checks_and_publish_gate(self):
        fake = Path(data.name) / "broken.mp4"
        fake.write_bytes(b"not a video")
        self.assertEqual(quality.export_report(fake, (1080, 1920), 10)["status"], "failed")
        self.assertFalse(posting.can_publish({"qc": {"status": "failed"}, "reviewed": True}))
        self.assertFalse(posting.can_publish({"qc": {"status": "ready"}, "mode": "fast"}))
        self.assertFalse(posting.can_publish({"qc": {"status": "needs_review"}}))
        self.assertTrue(posting.can_publish({"qc": {"status": "needs_review"}, "reviewed": True}))

    def test_square_and_landscape_formats(self):
        source = Path("web/brand/demo-bounce.mp4").resolve()
        render.install_fonts()
        meta = media.probe(source)
        for i, (aspect, size) in enumerate((("1:1", (1080, 1080)), ("16:9", (1920, 1080)))):
            clip = {"aspect": aspect, "layout": "fit", "start": 0, "end": 0.95, "captions": True}
            out = render.render_clip(source, Path(data.name), f"aspect-{i}", clip,
                                     [{"w": "Hi", "s": 0.1, "e": 0.6}], meta["width"], meta["height"])
            self.assertEqual(out["qc"]["status"], "ready")
            info = media.probe(Path(data.name) / out["file"])
            self.assertEqual((info["width"], info["height"]), size)


if __name__ == "__main__":
    unittest.main()
