"""Cache isolation, bounded AI retries and playable mode exports."""
import os
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

import httpx
import numpy as np

sys.path.insert(0, ".")
scratch = tempfile.TemporaryDirectory(prefix="peakcut-performance-")
os.environ.update(PEAKCUT_DATA=scratch.name, DATABASE_URL="")
from studio import ai, brain, cache, config, ctx, media, render, transcribe, work_control


class PerformanceTests(unittest.TestCase):
    def test_content_cache_is_versioned_and_owner_scoped(self):
        data = Path(scratch.name) / "test.wav"
        data.write_bytes(b"abc")
        with ctx.as_user("u_cacheaa"):
            key = [cache.fingerprint(data), "en", 0, 16000]
            cache.write("transcript", key, [{"w": "correct"}])
            self.assertEqual(cache.read("transcript", key), [{"w": "correct"}])
            self.assertIsNone(cache.read("transcript", [key[0], "ar", 0, 16000]))
            data.write_bytes(b"def")
            self.assertIsNone(cache.read("transcript", [cache.fingerprint(data), "en", 0, 16000]))
        with ctx.as_user("u_cachebb"):
            self.assertIsNone(cache.read("transcript", key))

    def test_asr_segments_and_decodes_in_order(self):
        rng = np.random.default_rng(100)
        signal = rng.integers(-5000, 5001, 16000 * 28, dtype=np.int16)
        signal[16000 * 17:16000 * 18] = 0
        spans = list(transcribe._chunks(signal))
        self.assertTrue(17 <= spans[0][1] / 16000 <= 18)
        calls = []

        def decode(segs, *_):
            calls.extend(map(len, segs))
            return [(["▁hello"], [0.1]) for _ in segs]
        with patch.object(transcribe, "COPIES", 1), patch.object(transcribe, "REMOTE", ""), \
                patch.object(transcribe, "_take_now", return_value=object()), \
                patch.object(transcribe, "decode_pieces", side_effect=decode):
            words = transcribe.transcribe(signal)
        self.assertEqual(calls, [b - a for a, b in spans])
        self.assertEqual([w["s"] for w in words], [round(a / 16000 + 0.1, 2) for a, b in spans])

    def test_gemini_retries_transient_connection_but_not_inflight_timeout(self):
        with patch.object(config, "ai_ready", return_value=True), patch.object(ai, "models", return_value=["gemini"]), \
                patch.object(work_control, "sleep") as pause:
            with patch.object(ai, "call", side_effect=[httpx.ConnectError("offline"), ([], 5, 2)]) as call, \
                    patch.object(brain.usage, "record"):
                self.assertEqual(brain.ask_ai("transcript")[2], 7)
                self.assertEqual(call.call_count, 2)
                pause.assert_called_once_with(1)
            with patch.object(ai, "call", side_effect=httpx.ReadTimeout("delayed")) as call:
                with self.assertRaisesRegex(RuntimeError, "time limit"):
                    brain.ask_ai("transcript")
                self.assertEqual(call.call_count, 1)

    def test_cpu_exports_have_valid_audio_caption_and_video_streams(self):
        render.install_fonts()
        src = Path("web/brand/demo-bounce.mp4")
        meta = media.probe(src)
        with tempfile.TemporaryDirectory(dir=scratch.name) as out:
            for mode, resolution in (("fast", (540, 960)), ("balanced", (1080, 1920)), ("quality", (1080, 1920))):
                with self.subTest(mode=mode):
                    clip = {"start": 0.0, "end": 1.0, "style": "clean", "mode": mode, "layout": "crop"}
                    word = [{"w": "Caption", "s": 0.1, "e": 0.5}]
                    result = render.render_clip(src, Path(out), mode, clip, word, meta["width"], meta["height"])
                    mp4 = Path(out) / result["file"]
                    probe = media.probe(mp4)
                    self.assertEqual((probe["width"], probe["height"]), resolution)
                    self.assertAlmostEqual(probe["duration"], 1.0, delta=0.1)
                    self.assertIn("Caption", (Path(out) / f"{mode}.ass").read_text())
                    subprocess.run(["ffmpeg", "-v", "error", "-xerror", "-i", str(mp4), "-f", "null", "-"], check=True)


if __name__ == "__main__":
    unittest.main()
