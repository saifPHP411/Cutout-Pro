"""Offline tests for pinned and free Arabic voice auto-setup and tamper refusal."""
import json
import os
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch
import math
import wave
import numpy as np

sys.path.insert(0, ".")
os.environ["PEAKCUT_DATA"] = tempfile.mkdtemp(prefix="pc-voice-bootstrap-")
from studio import original_shorts, original_voice  # noqa: E402

class VoiceBootstrapTests(unittest.TestCase):
    def test_builtin_requires_fixed_repository_and_has_documented_license(self):
        self.assertTrue(original_voice.BASE.startswith("https://huggingface.co/vadimbelsky/"))
        self.assertEqual(len(original_voice.REVISION), 40)
        self.assertEqual(original_voice.FILENAME, "arabic-emirati-female-model.onnx")
        self.assertEqual(original_voice._fetch.__name__, "_fetch")
        with self.assertRaises(ValueError):
            original_voice._fetch("../../private/key")
        with patch.dict(os.environ, {"PEAKCUT_ORIGINAL_VOICE_LICENSE_APPROVED": "0"}):
            self.assertFalse(original_voice.commercially_allowed(Path("/tmp/custom-piper.onnx")))

    def test_auto_setup_is_one_time_and_detects_model_tampering(self):
        with tempfile.TemporaryDirectory(prefix="pc-piper-voice-") as root:
            model = Path(root) / original_voice.FILENAME
            onnx = b"test-synthetic-model" * 100
            conf = json.dumps({"audio": {"sample_rate": 22050}}).encode()
            calls = []
            def fake_fetch(name, **kwargs):
                calls.append(name)
                return onnx if name == original_voice.FILENAME else conf
            with patch.object(original_voice, "default_model", return_value=model), \
                 patch.object(original_voice, "_fetch", side_effect=fake_fetch), \
                 patch.object(original_voice.shutil, "which", return_value="/usr/local/bin/piper"), \
                 patch.dict(os.environ, {"PEAKCUT_ORIGINAL_BUILTIN_VOICE": "1"}):
                first = original_voice.ensure_builtin()
                self.assertEqual(first, model)
                self.assertEqual(calls, [original_voice.FILENAME, original_voice.CONFIG_FILE])
                self.assertTrue(model.is_file())
                manifest = json.loads((Path(root) / "source.json").read_text())
                self.assertEqual(manifest["license_declared"], "MIT")
                self.assertEqual(original_voice.ensure_builtin(), model)
                self.assertEqual(len(calls), 2)
                model.write_bytes(b"tampered")
                with self.assertRaisesRegex(RuntimeError, "altered"):
                    original_voice.ensure_builtin()

    def test_unavailable_or_partial_voice_never_passes(self):
        with tempfile.TemporaryDirectory(prefix="pc-piper-partial-") as root:
            model = Path(root) / original_voice.FILENAME
            model.write_bytes(b"partial")
            with patch.object(original_voice, "default_model", return_value=model), \
                 patch.object(original_voice.shutil, "which", return_value="/usr/local/bin/piper"), \
                 patch.dict(os.environ, {"PEAKCUT_ORIGINAL_BUILTIN_VOICE": "1"}):
                with self.assertRaisesRegex(RuntimeError, "Incomplete"):
                    original_voice.ensure_builtin()
            with patch.object(original_voice.shutil, "which", return_value=None):
                with self.assertRaisesRegex(RuntimeError, "unavailable"):
                    original_voice.ensure_builtin()

    def test_custom_voice_needs_approval_and_builtin_can_be_disabled(self):
        with patch.dict(os.environ, {"PEAKCUT_ORIGINAL_BUILTIN_VOICE": "0",
                                     "PEAKCUT_ORIGINAL_VOICE_LICENSE_APPROVED": "0"}):
            self.assertFalse(original_voice.commercially_allowed())
            self.assertFalse(original_voice.use_builtin())
            with self.assertRaisesRegex(RuntimeError, "disabled"):
                original_voice.ensure_builtin()
        with patch.dict(os.environ, {"PEAKCUT_ORIGINAL_BUILTIN_VOICE": "1",
                                     "PEAKCUT_ORIGINAL_VOICE_LICENSE_APPROVED": "0"}):
            self.assertTrue(original_voice.commercially_allowed(original_voice.default_model()))
            self.assertFalse(original_voice.commercially_allowed(Path("/tmp/custom.onnx")))


    def test_narration_audio_gate_rejects_silence_and_accepts_real_waveform(self):
        from types import SimpleNamespace
        with tempfile.TemporaryDirectory(prefix="pc-audio-gate-") as root:
            folder = Path(root)
            model = folder / "voice.onnx"
            model.write_bytes(b"placeholder")
            (folder / "voice.onnx.json").write_text("{}")
            destination = folder / "narration.wav"
            def silent(*_args, **_kwargs):
                with wave.open(str(destination), "wb") as f:
                    f.setnchannels(1)
                    f.setsampwidth(2)
                    f.setframerate(16000)
                    f.writeframes(b"\x00\x00" * (16000 * 4))
                return SimpleNamespace(returncode=0)
            with patch.object(original_shorts.shutil, "which", return_value="piper"), \
                 patch.object(original_shorts.subprocess, "run", side_effect=silent):
                with self.assertRaisesRegex(RuntimeError, "quality gate"):
                    original_shorts.voice_over("نص عربي للاختبار", destination, model)
                self.assertFalse(destination.exists())

            def spoken(*_args, **_kwargs):
                t = np.arange(16000 * 4, dtype=np.float64) / 16000
                audio = (2500 * np.sin(2 * math.pi * 260 * t)).astype("<i2")
                with wave.open(str(destination), "wb") as f:
                    f.setnchannels(1)
                    f.setsampwidth(2)
                    f.setframerate(16000)
                    f.writeframes(audio.tobytes())
                return SimpleNamespace(returncode=0)
            with patch.object(original_shorts.shutil, "which", return_value="piper"), \
                 patch.object(original_shorts.subprocess, "run", side_effect=spoken):
                original_shorts.voice_over("نص عربي للاختبار", destination, model)
                self.assertTrue(destination.is_file())


if __name__ == "__main__":
    unittest.main()
