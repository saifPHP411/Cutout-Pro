"""Pinned, free, opt-in Arabic Piper voice provisioning.

Only built-in voice below is automatically downloaded (no arbitrary URLs,
credentials, runtime code, or paid API). The Hugging Face model card states MIT
license. That declaration is NOT an independent audit of the original
training corpus, so publication is still gated by the owner's choice to enable
this built-in voice and by platform rights checks.
"""
from __future__ import annotations

import hashlib
import json
import os
import shutil
import tempfile
import threading
from pathlib import Path
from urllib.request import Request, urlopen

from . import config

REPO = "vadimbelsky/arabic-emirati-female-piper"
REVISION = "cfe173969620e40be65b5dd8ed4d97d55a864325"
BASE = f"https://huggingface.co/{REPO}/resolve/{REVISION}/"
FILENAME = "arabic-emirati-female-model.onnx"
CONFIG_FILE = FILENAME + ".json"
LICENSE_URL = "https://huggingface.co/vadimbelsky/arabic-emirati-female-piper"
MAX_BYTES = {FILENAME: 85_000_000, CONFIG_FILE: 100_000}
MIN_BYTES = {FILENAME: 10_000_000, CONFIG_FILE: 100}
_lock = threading.Lock()


def builtin_enabled() -> bool:
    return config.env("PEAKCUT_ORIGINAL_BUILTIN_VOICE", "1") == "1"


def default_model() -> Path:
    return config.DATA_DIR / "original_youtube" / "voices" / FILENAME


def selected_model() -> Path:
    text = config.env("PEAKCUT_ORIGINAL_VOICE_MODEL", "").strip()
    return Path(text) if text else default_model()


def use_builtin(model: Path | None = None) -> bool:
    model = model or selected_model()
    return builtin_enabled() and model == default_model()


def commercially_allowed(model: Path | None = None) -> bool:
    # The restricted custom-voice route needs an explicit license check; the
    # pinned bundled voice's public model card declares MIT.
    return use_builtin(model) or config.env("PEAKCUT_ORIGINAL_VOICE_LICENSE_APPROVED", "0") == "1"


def _fetch(name: str, *, opener=urlopen) -> bytes:
    if name not in MAX_BYTES:
        raise ValueError("Unexpected voice-model asset.")
    request = Request(BASE + name + "?download=true",
                      headers={"User-Agent": "PeakCut-OriginalScience/1.0"})
    with opener(request, timeout=40) as response:
        if getattr(response, "status", 200) != 200:
            raise RuntimeError("Arabic voice server returned an unsuccessful response.")
        payload = response.read(MAX_BYTES[name] + 1)
    if not MIN_BYTES[name] <= len(payload) <= MAX_BYTES[name]:
        raise RuntimeError("Arabic voice file has an unexpected size.")
    return payload


def ensure_builtin(*, opener=urlopen) -> Path:
    """Download once into a private data directory; never modify the repo."""
    if not builtin_enabled():
        raise RuntimeError("Built-in voice auto-download is disabled.")
    if not shutil.which("piper"):
        raise RuntimeError("Local Piper binary is unavailable.")
    model = default_model()
    config_file = Path(str(model) + ".json")
    manifest = model.parent / "source.json"
    with _lock:
        if model.is_file() and config_file.is_file() and manifest.is_file():
            info = json.loads(manifest.read_text(encoding="utf-8"))
            if (info.get("revision") == REVISION
                and info.get("model_sha256") == hashlib.sha256(model.read_bytes()).hexdigest()
                and info.get("config_sha256") == hashlib.sha256(config_file.read_bytes()).hexdigest()):
                return model
            raise RuntimeError("Voice file was altered; manual investigation required.")
        if any(p.exists() for p in (model, config_file, manifest)):
            raise RuntimeError("Incomplete voice model; refusing a partial or silent replacement.")
        model.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
        # The allowlisted repository and immutable revision are intentionally
        # hard-coded. No user text becomes a download URL.
        model_bytes = _fetch(FILENAME, opener=opener)
        json_bytes = _fetch(CONFIG_FILE, opener=opener)
        settings = json.loads(json_bytes)
        if not isinstance(settings, dict) or not isinstance(settings.get("audio"), dict) or not settings["audio"].get("sample_rate"):
            raise RuntimeError("Unexpected Piper voice configuration.")
        # Stage both files and an integrity record, then publish them together.
        temp_paths = []
        try:
            for path, payload in ((model, model_bytes), (config_file, json_bytes)):
                fd, tmp = tempfile.mkstemp(prefix=".voice-", dir=model.parent)
                with os.fdopen(fd, "wb") as out:
                    out.write(payload)
                    out.flush()
                    os.fsync(out.fileno())
                temp_paths.append((Path(tmp), path))
            record = {"provider": REPO, "revision": REVISION, "license_declared": "MIT",
                      "model_sha256": hashlib.sha256(model_bytes).hexdigest(),
                      "config_sha256": hashlib.sha256(json_bytes).hexdigest(),
                      "source": LICENSE_URL}
            fd, tmp = tempfile.mkstemp(prefix=".voice-manifest-", dir=model.parent)
            with os.fdopen(fd, "w", encoding="utf-8") as out:
                json.dump(record, out, ensure_ascii=False)
                out.flush()
                os.fsync(out.fileno())
            temp_paths.append((Path(tmp), manifest))
            for temp, target in temp_paths:
                temp.replace(target)
        finally:
            for temporary, _ in temp_paths:
                temporary.unlink(missing_ok=True)
    return model
