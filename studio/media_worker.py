"""Isolate a blocking download and its encoder in the video worker's process group."""
import contextlib
import json
import sys
from pathlib import Path

from . import media


def main():
    method, args, progress = json.load(sys.stdin)
    media._isolated_worker = True

    def update(value):
        Path(progress).write_text(f"{value:.4f}", encoding="ascii")
    stdout = sys.stdout
    with contextlib.redirect_stdout(sys.stderr):
        try:
            if method in ("download", "download_audio", "download_section"):
                args[1] = Path(args[1])
            value = getattr(media, method)(*args, **({"on_progress": update} if progress else {}))
            result = {"value": str(value) if isinstance(value, Path) else value}
        except media.YouTubeBlocked:
            result = {"blocked": True}
        except Exception as exc:
            from .safe import friendly

            result = {"error": friendly(exc)}
    json.dump(result, stdout)


if __name__ == "__main__":
    main()
