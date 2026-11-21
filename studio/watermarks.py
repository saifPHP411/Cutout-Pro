"""Verify owner-supplied logo references against several frames of a finished clip."""
import io
import subprocess

from PIL import Image, UnidentifiedImageError

from . import work_control


def normalize(raw):
    if len(raw) > 250_000:
        raise ValueError("Logo image must be smaller than 250 KB.")
    try:
        with Image.open(io.BytesIO(raw)) as image:
            if image.format not in ("PNG", "JPEG") or max(image.size) > 2048 or min(image.size) < 12:
                raise ValueError("Use a PNG or JPEG image between 12 and 2,048 pixels per side.")
            image = image.convert("L")
            image.thumbnail((140, 140))
            if image.getextrema()[1] - image.getextrema()[0] < 25:
                raise ValueError("Use a reference with more contrast.")
            out = io.BytesIO()
            image.save(out, "PNG")
            return out.getvalue()
    except (OSError, UnidentifiedImageError, Image.DecompressionBombError) as exc:
        raise ValueError("Could not read the logo reference.") from exc


def scan(path, duration, reference):
    import cv2
    import numpy as np

    template = cv2.imdecode(np.frombuffer(reference, np.uint8), cv2.IMREAD_GRAYSCALE)
    images = []
    for fraction in (0.2, 0.5, 0.8):
        work_control.check()
        command = ["ffmpeg", "-v", "error", "-ss", f"{duration*fraction:.2f}", "-i", str(path),
                   "-vf", "scale=360:-2", "-frames:v", "1", "-threads", "1", "-vcodec", "mjpeg", "-f", "image2pipe", "-"]
        try:
            result = work_control.run(command, capture_output=True, timeout=15)
        except (OSError, subprocess.TimeoutExpired):
            return {"status": "uncertain", "matches": 0}
        if result.returncode or not result.stdout:
            return {"status": "uncertain", "matches": 0}
        image = cv2.imdecode(np.frombuffer(result.stdout, np.uint8), cv2.IMREAD_GRAYSCALE)
        if image is None:
            return {"status": "uncertain", "matches": 0}
        images.append(image)

    matches = 0
    weak = 0
    positions = []
    for img in images:
        high, where = 0.0, (0, 0)
        for width in (16, 24, 36, 52, 76, 106, 140):
            height = round(width * template.shape[0] / template.shape[1])
            if not 9 <= height <= img.shape[0] * .45:
                continue
            mark = cv2.resize(template, (width, height), interpolation=cv2.INTER_AREA)
            scores = cv2.matchTemplate(img, mark, cv2.TM_CCOEFF_NORMED)
            _, score, _, point = cv2.minMaxLoc(scores)
            if score > high:
                high, where = score, point
        if high >= .90:
            matches += 1
            positions.append(where)
        elif high >= .72:
            weak += 1
    repeated = any(abs(a[0]-b[0]) < 18 and abs(a[1]-b[1]) < 22
                   for i,a in enumerate(positions) for b in positions[i+1:])
    return {"status": "detected" if repeated else "uncertain" if matches or weak else "not_matched", "matches": matches}
