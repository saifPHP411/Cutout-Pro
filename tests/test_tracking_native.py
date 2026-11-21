"""Optional test with a real face in footage, then reframe and decode the final MP4."""
import os
import subprocess
import sys
import tempfile
from pathlib import Path

import numpy as np
from PIL import Image


def main():
    portrait = Path(sys.argv[1])
    with tempfile.TemporaryDirectory(prefix="peakcut-track-real-") as work:
        os.environ["PEAKCUT_DATA"] = work
        sys.path.insert(0, ".")
        from studio import media, reframe, render

        root = Path(work)
        source = root / "moving-face.mp4"
        crop = Image.open(portrait).convert("RGB").crop((155, 35, 295, 195)).resize((140, 160))
        program = ["ffmpeg", "-v", "error", "-f", "rawvideo", "-pix_fmt", "rgb24", "-s", "640x360", "-r", "12",
                   "-i", "-", "-f", "lavfi", "-i", "sine=f=440:r=48000", "-t", "8",
                   "-c:v", "libx264", "-threads", "2", "-pix_fmt", "yuv420p", "-c:a", "aac", str(source)]
        with subprocess.Popen(program, stdin=subprocess.PIPE) as ffmpeg:
            for i in range(96):
                frame = Image.new("RGB", (640, 360), (58, 72, 86))
                frame.paste(crop, (170 + i*2, 70))
                ffmpeg.stdin.write(np.asarray(frame).tobytes())
            ffmpeg.stdin.close()
            assert ffmpeg.wait() == 0
        points = reframe.face_path(source, 0, 8, 9/16, (.09, .83))
        assert points and points[-1][1] - points[0][1] > 0.1, points
        render.install_fonts()
        clip = {"start": 0, "end": 8, "layout": "auto", "mode": "balanced", "style": "clean", "safe_zone": "youtube"}
        result = render.render_clip(source, root, "tracked", clip, [{"w":"Tracking", "s":1, "e":2}], 640, 360)
        assert result["tracking"] == "face" and result["qc"]["status"] == "ready", result
        out = root / "tracked.mp4"
        m = media.probe(out)
        assert (m["width"], m["height"]) == (1080, 1920), m
        subprocess.run(["ffmpeg", "-v", "error", "-xerror", "-i", str(out), "-f", "null", "-"], check=True)
        print(f"PASS Haar detector, {len(points)} crop points, matched face moved {points[-1][1]-points[0][1]:.3f} of source width, real 1080x1920 export")


if __name__ == "__main__":
    main()
