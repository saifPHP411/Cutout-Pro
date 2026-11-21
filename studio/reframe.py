"""Find a stable horizontal crop focus from motion on a small sample."""
import subprocess

import numpy as np

from . import work_control


def focus(path, start, end):
    """A normalized x-coordinate. Preserve the center when the evidence is weak."""
    frames = 12
    duration = min(6.0, end - start)
    at = start + max(0, (end - start - duration) / 2)
    try:
        result = work_control.run(
            ["ffmpeg", "-v", "error", "-ss", f"{at:.2f}", "-i", str(path.resolve()),
             "-t", f"{duration:.2f}", "-vf", f"fps={frames/max(1,duration):.4f},scale=96:54,format=gray",
             "-an", "-frames:v", str(frames), "-threads", "1", "-f", "rawvideo", "-"],
            capture_output=True, timeout=25)
        if result.returncode or len(result.stdout) < 4 * 96 * 54:
            return 0.5
        data = np.frombuffer(result.stdout, np.uint8).reshape(-1, 54, 96)
    except (OSError, ValueError, TimeoutError, subprocess.TimeoutExpired):
        return 0.5
    delta = np.abs(np.diff(data[:, 8:46, :].astype(np.int16), axis=0))
    valid = delta[delta.mean(axis=(1, 2)) < 25]  # skip transitions affecting the whole frame
    if len(valid) < 3:
        return 0.5
    movement = np.median(valid.mean(axis=1), axis=0)
    weights = np.maximum(movement - np.percentile(movement, 60), 0)
    if float(weights.sum()) < 15:
        return 0.5
    pivot = float(np.average((np.arange(96) + 0.5) / 96, weights=weights))
    return max(0.25, min(0.75, pivot))


def face_path(src, start, end, aspect, bounds=(0.12, 0.88)):
    """Sample at 2 Hz and follow a single established face. Ambiguous tracks use a wide layout."""
    try:
        import cv2
    except ImportError:
        return []
    count = min(360, max(4, int((end - start) * 2)))
    try:
        sample = work_control.run(
            ["ffmpeg", "-v", "error", "-threads", "2", "-ss", f"{start:.2f}", "-i", str(src.resolve()),
             "-t", f"{end-start:.2f}", "-an", "-filter_threads", "1",
             "-vf", f"fps={count/(end-start):.6f},scale=320:-2,format=gray", "-frames:v", str(count),
             "-threads", "1", "-c:v", "pgm", "-f", "image2pipe", "-"],
            capture_output=True, timeout=35)
    except (OSError, TimeoutError, subprocess.TimeoutExpired):
        return []
    if sample.returncode:
        return []
    detector = cv2.CascadeClassifier(cv2.data.haarcascades + "haarcascade_frontalface_default.xml")
    if detector.empty():
        return []
    return track(read_pgm(sample.stdout), detector, end - start, aspect, bounds)


def read_pgm(raw):
    """Read the bounded grayscale frames produced by FFmpeg."""
    offset = 0
    while offset < len(raw):
        magic = raw.find(b"\n", offset)
        size = raw.find(b"\n", magic + 1)
        maxval = raw.find(b"\n", size + 1)
        if magic < offset or size <= magic or maxval <= size or raw[offset:magic] != b"P5":
            return
        try:
            width, height = map(int, raw[magic + 1:size].split())
        except ValueError:
            return
        end = maxval + 1 + width * height
        if not 0 < width <= 640 or not 0 < height <= 1200 or end > len(raw):
            return
        yield np.frombuffer(raw, dtype=np.uint8, count=width * height, offset=maxval + 1).reshape(height, width)
        offset = end


def track(frames, detector, duration, aspect, bounds):
    center = None
    misses = 0
    path = []
    for index, frame in enumerate(frames):
        work_control.check()
        height, width = frame.shape
        faces = sorted(detector.detectMultiScale(frame, scaleFactor=1.13, minNeighbors=5, minSize=(27, 27)),
                       key=lambda box: int(box[2]) * int(box[3]), reverse=True)
        if center is not None:
            # A nearby detection can continue the established track. A different face cannot take it over.
            faces = [box for box in faces if abs((box[0] + box[2] / 2) / width - center) <= 0.22]
        elif len(faces) >= 2 and faces[1][2] * faces[1][3] > 0.7 * faces[0][2] * faces[0][3]:
            faces = []
        if not len(faces):
            misses += 1
            if misses > 5 and center is not None:
                return []
            path.append(None)
            continue
        x, y, w, h = faces[0]
        if len(bounds) == 4 and (y / height < bounds[2] or (y+h) / height > bounds[3]):
            return []
        observed = (x + w / 2) / width
        center = observed if center is None else center + float(np.clip((observed - center) * 0.55, -0.12, 0.12))
        crop = height * aspect / width
        if crop >= 1:
            return []
        left = float(np.clip(center - (bounds[0]+bounds[1])/2 * crop, 0, 1-crop))
        face_left, face_right = (x / width - left) / crop, ((x+w) / width - left) / crop
        if face_left < bounds[0] - 0.025 or face_right > bounds[1] + 0.025:
            return []
        path.append(left)
        misses = 0
    known = [(i, v) for i, v in enumerate(path) if v is not None]
    if len(known) < max(4, len(path) * 0.55):
        return []
    indices, points = zip(*known)
    filled = np.interp(np.arange(len(path)), indices, points)
    stride = max(1, len(path) // 24)
    sampled = list(range(0, len(path), stride))
    if sampled[-1] != len(path)-1:
        sampled.append(len(path)-1)
    return [(round(i * duration / len(path), 3), round(float(filled[i]), 5)) for i in sampled]


def crop_expr(points):
    """Build a bounded piecewise-linear crop x offset for the FFmpeg crop filter."""
    expr = f"{points[-1][1]:.5f}"
    for (a, x), (b, y) in reversed(list(zip(points, points[1:]))):
        if b <= a:
            continue
        expr = f"if(lt(t,{b:.3f}),{x:.5f}+({y-x:.5f})*clip((t-{a:.3f})/{b-a:.3f},0,1),{expr})"
    return f"iw*({expr})"
