"""Original, source-linked Arabic astronomy Shorts rendered without stock video or paid AI.

Deliberately separate from the user-upload/creator/campaign clipping path. A
human-curated fact bank is the input. This avoids silently republishing other
creators' clips or asking a language model to invent scientific claims.

No uploads happen in this module. The CLI handles that with explicit gating.
"""
from __future__ import annotations

import json
import math
import os
import shutil
import subprocess
import wave
from pathlib import Path

from PIL import Image, ImageDraw, ImageFont, features

from . import config

WIDTH, HEIGHT, FPS = 1080, 1920, 24
ROOT = config.ROOT
CATALOG = ROOT / "content" / "original_space_episodes.json"
THEMES = {"sunlight", "moon", "venus", "mars", "asteroid"}
BG = (21, 12, 27)
PINK = (234, 116, 170)
WHITE = (255, 242, 249)
DIM = (204, 170, 198)


def catalog(path: Path = CATALOG) -> list[dict]:
    """Read only editorially prepared episodes with explicit scientific sources."""
    data = json.loads(Path(path).read_text(encoding="utf-8"))
    if data.get("reviewed") is not True or not isinstance(data.get("episodes"), list):
        raise ValueError("Episodes must be reviewed before the autopilot uses them.")
    ids = set()
    episodes = []
    for e in data["episodes"]:
        if not isinstance(e, dict) or not isinstance(e.get("id"), str) or e["id"] in ids:
            raise ValueError("Duplicate or malformed episode ID.")
        if e.get("theme") not in THEMES or not str(e.get("source", "")).startswith("https://science.nasa.gov/"):
            raise ValueError("An original episode needs a supported illustration and NASA fact source.")
        if not (80 <= len(e.get("narration", "")) <= 1000) or len(e.get("scenes", [])) != 3:
            raise ValueError("Each episode needs narration and three distinct explanatory scenes.")
        for scene in e["scenes"]:
            if not all(str(scene.get(k, "")).strip() for k in ("headline", "detail", "stat")):
                raise ValueError("Incomplete diagram/story scene.")
        if len(set(scene["headline"] for scene in e["scenes"])) != 3:
            raise ValueError("Each scene needs a different narrative purpose.")
        if not e.get("title") or len(e["title"]) > 100:
            raise ValueError("Invalid YouTube title.")
        ids.add(e["id"])
        episodes.append(e)
    return episodes


def font(size: int, bold=True) -> ImageFont.FreeTypeFont:
    """Use an explicit Arabic-capable font; never render disconnected glyphs."""
    if not features.check("raqm"):
        raise RuntimeError("Arabic shaping (libraqm) is missing; refusing a broken export.")
    filename = "NotoSansArabic-Bold.ttf" if bold else "NotoSansArabic-Regular.ttf"
    places = [Path("/usr/share/fonts/truetype/noto") / filename,
              Path("/usr/local/share/fonts") / filename]
    for f in places:
        if f.is_file():
            return ImageFont.truetype(str(f), size, layout_engine=ImageFont.Layout.RAQM)
    raise RuntimeError("Install fonts-noto-core for connected Arabic lettering.")


def _lines(draw: ImageDraw.ImageDraw, value: str, style, max_width: int, limit: int = 4) -> list[str]:
    result, current = [], ""
    for word in value.split():
        trial = (current + " " + word).strip()
        if current and draw.textlength(trial, font=style, direction="rtl") > max_width:
            result.append(current)
            current = word
        else:
            current = trial
    if current:
        result.append(current)
    if len(result) > limit:
        raise ValueError("The Arabic scene text does not fit its visual layout.")
    return result


def _rtl(draw, value, x, y, size, fill=WHITE, bold=True, max_width=880, spacing=1.45):
    style = font(size, bold)
    lines = _lines(draw, value, style, max_width)
    step = int(size * spacing)
    for i, line in enumerate(lines):
        draw.text((x, y + i * step), line, font=style, anchor="ra",
                  direction="rtl", fill=fill, stroke_width=0)
    return y + len(lines) * step


def _graphic(draw, theme: str, index: int):
    """Distinct, self-drawn educational diagrams, not NASA image reuploads."""
    cx = 540
    if theme == "sunlight":
        draw.ellipse((95, 760, 460, 1125), fill=(255, 165, 67), outline=(255, 205, 91), width=12)
        draw.ellipse((780, 850, 970, 1040), fill=(56, 134, 217), outline=(137, 204, 246), width=9)
        for j in range(7):
            left = 480 + j * 40
            draw.rounded_rectangle((left, 936, left + 22, 951), radius=7, fill=PINK)
        draw.line((890, 1035, 840, 1095), fill=(141, 207, 255), width=5)
    elif theme == "moon":
        draw.ellipse((90, 775, 500, 1185), fill=(49, 138, 214), outline=(135, 205, 246), width=11)
        draw.ellipse((795, 865, 1030, 1100), fill=(171, 165, 174), outline=(229, 225, 227), width=7)
        for p in ((851, 912, 885, 945), (922, 1000, 973, 1045)):
            draw.ellipse(p, fill=(130, 130, 144))
        for j in range(5):
            left = 520 + j * 54
            draw.rounded_rectangle((left, 965, left + 28, 977), radius=6, fill=PINK)
    elif theme == "venus":
        draw.ellipse((260, 745, 825, 1310), fill=(225, 144, 86), outline=(255, 199, 129), width=14)
        for j in range(4):
            y = 840 + j * 85
            draw.arc((280, y - 50, 800, y + 110), start=8, end=172, fill=(255, 211, 153), width=10)
        draw.arc((175, 665, 905, 1395), start=35, end=318,
                 fill=PINK if index % 2 == 0 else (255, 189, 220), width=9)
    elif theme == "asteroid":
        # Original schematic, never a representation of a real asteroid image.
        # Three stages illustrate a measured approach vs mean lunar distance.
        draw.ellipse((105, 855, 455, 1205), fill=(38, 130, 211), outline=(127, 208, 251), width=12)
        draw.arc((125, 770, 1060, 1460), 200, 348, fill=(146, 92, 164), width=7)
        if index == 0:
            draw.ellipse((790, 815, 985, 1010), fill=(165, 129, 106), outline=(218, 184, 143), width=9)
            draw.ellipse((806, 846, 843, 884), fill=(118, 94, 81))
            draw.ellipse((885, 925, 939, 968), fill=(114, 93, 82))
        elif index == 1:
            draw.ellipse((500, 888, 600, 988), fill=(177, 171, 183), outline=(235, 229, 236), width=5)
            for i in range(6):
                x = 615 + i * 47
                draw.rounded_rectangle((x, 930, x + 26, 948), radius=6, fill=PINK)
            draw.ellipse((920, 868, 1024, 972), fill=(170, 124, 93), outline=(238, 197, 135), width=7)
        else:
            draw.ellipse((764, 814, 1000, 1050), fill=(170, 126, 89), outline=(228, 187, 144), width=8)
            for x in range(520, 744, 44):
                draw.polygon([(x, 920), (x + 19, 935), (x, 950)], fill=PINK)
            draw.line((500, 942, 752, 942), fill=PINK, width=8)
    elif theme == "mars":
        draw.ellipse((125, 835, 495, 1205), fill=(48, 143, 217), outline=(155, 214, 249), width=9)
        draw.ellipse((600, 900, 920, 1220), fill=(205, 97, 78), outline=(255, 160, 125), width=10)
        for x, top, bottom in ((300, 650, 825), (760, 710, 875)):
            draw.line((x, top, x, bottom), fill=PINK, width=12)
            draw.polygon([(x - 24, bottom - 22), (x + 24, bottom - 22), (x, bottom + 20)], fill=PINK)
    draw.rounded_rectangle((105, 1365, 975, 1390), radius=12, fill=(62, 35, 70))
    draw.rounded_rectangle((105, 1365, 260 + index * 280, 1390), radius=12, fill=PINK)


def _scene(episode: dict, index: int, destination: Path):
    scene = episode["scenes"][index]
    canvas = Image.new("RGB", (WIDTH, HEIGHT), BG)
    d = ImageDraw.Draw(canvas)
    d.ellipse((-310, -340, 690, 660), fill=(43, 22, 54))
    d.ellipse((570, 1330, 1450, 2230), fill=(39, 18, 48))
    d.rounded_rectangle((66, 80, 1014, 1780), radius=68, outline=(105, 55, 94), width=5)
    d.rounded_rectangle((92, 96, 590, 180), radius=42, fill=(73, 37, 76))
    _rtl(d, "مختبر الفضاء", 570, 106, 40, WHITE)
    _rtl(d, episode["hook"], 980, 295, 78, WHITE, max_width=882)
    _graphic(d, episode["theme"], index)
    _rtl(d, scene["headline"], 969, 1420, 67, PINK, max_width=865)
    _rtl(d, scene["detail"], 960, 1522, 44, WHITE, bold=False, max_width=865)
    _rtl(d, scene["stat"], 950, 1717, 41, (255, 220, 134), max_width=860)
    d.text((105, 1820), f"{index + 1}/3   ORIGINAL SPACE LAB",
           font=ImageFont.truetype("/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf", 27)
           if Path("/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf").is_file() else font(26),
           fill=DIM)
    canvas.save(destination, optimize=True)


def voice_over(text: str, wav: Path, model: Path, binary: str = "piper") -> None:
    """Local Piper output, no API. Voice model and .json are operator-provided."""
    if not model.is_file() or not Path(str(model) + ".json").is_file():
        raise RuntimeError("A licensed Piper .onnx model and its .onnx.json are required.")
    if not shutil.which(binary):
        raise RuntimeError("Install the free Piper TTS binary before generating narrated videos.")
    env = {**os.environ, "OMP_NUM_THREADS": "2"}
    p = subprocess.run([binary, "--model", str(model), "--output_file", str(wav)],
                       input=text + "\n", capture_output=True, text=True, timeout=210, env=env)
    if p.returncode or not wav.is_file() or wav.stat().st_size < 1024:
        raise RuntimeError("Offline Arabic narration failed; no unvoiced video will be published.")
    # A successfully written WAV is not evidence of audible Arabic speech.
    # Refuse silence, near-silence and severely clipped output before export.
    import numpy as np
    try:
        with wave.open(str(wav), "rb") as audio:
            if audio.getsampwidth() != 2 or audio.getnchannels() not in (1, 2):
                raise ValueError("Unsupported PCM output")
            duration = audio.getnframes() / audio.getframerate()
            samples = np.frombuffer(audio.readframes(audio.getnframes()), dtype="<i2")
        if not 3 <= duration <= 170 or not samples.size:
            raise ValueError("Narration length is outside Shorts bounds")
        absolute = np.abs(samples.astype(np.int32))
        rms = float(np.sqrt(np.mean(samples.astype(np.float64) ** 2)))
        clipped = float(np.mean(absolute >= 32750))
        if rms < 70 or int(absolute.max()) < 500 or clipped > 0.15:
            raise ValueError("Silent, too quiet or badly clipped voice")
    except (OSError, EOFError, wave.Error, ValueError) as exc:
        wav.unlink(missing_ok=True)
        raise RuntimeError("Narration quality gate failed; no silent Short will be uploaded.") from exc


def _duration(wav: Path) -> float:
    with wave.open(str(wav), "rb") as audio:
        return audio.getnframes() / audio.getframerate()


def render(episode: dict, destination: Path, wav: Path, test_size: tuple[int, int] | None = None) -> Path:
    """Create source-original 1080x1920 H264/AAC video from three authored diagrams.

    An injected WAV is accepted for deterministic CI. A production caller must
    use voice_over to generate that WAV from the original Arabic narration.
    """
    destination = Path(destination)
    wav = Path(wav)
    if not wav.is_file():
        raise FileNotFoundError(wav)
    seconds = _duration(wav)
    if not 3 <= seconds <= 170:
        raise ValueError("Narration duration must be 3–170 seconds to make a Short.")
    target = destination / (episode["id"] + ".mp4")
    destination.mkdir(parents=True, exist_ok=True)
    images = []
    for i in range(3):
        p = destination / f"{episode['id']}-scene-{i}.png"
        _scene(episode, i, p)
        images.append(p)
    import fractions
    # Frames are apportioned by word count, with no duplicated or discarded
    # scripted material. "shortest" trims tiny audio/video timing differences.
    words = [max(1, len(s["detail"].split()) + len(s["headline"].split())) for s in episode["scenes"]]
    nframes = max(3, round(seconds * FPS))
    counts = [max(1, int(nframes * w / sum(words))) for w in words[:2]]
    counts.append(nframes - sum(counts))
    if counts[-1] < 1:
        raise ValueError("Audio is too short for three meaningful scenes.")
    cmd = ["ffmpeg", "-hide_banner", "-loglevel", "error", "-y"]
    for image, frames in zip(images, counts):
        cmd += ["-loop", "1", "-framerate", str(FPS), "-t", str(frames / FPS), "-i", str(image)]
    cmd += ["-i", str(wav)]
    # A gentle authored camera move on each illustration avoids static slideshow
    # exports while retaining full control of imagery and CPU cost.
    panels = []
    for i, frames in enumerate(counts):
        panels.append(f"[{i}:v]zoompan=z='min(zoom+0.00027,1.065)':d=1:"
                      f"s={WIDTH}x{HEIGHT}:fps={FPS},trim=end_frame={frames},"
                      f"setpts=PTS-STARTPTS[v{i}]")
    filt = ";".join(panels + ["".join(f"[v{i}]" for i in range(3))
                                + "concat=n=3:v=1:a=0,format=yuv420p[out]"])
    cmd += ["-filter_complex", filt, "-map", "[out]", "-map", "3:a:0",
            "-c:v", "libx264", "-preset", "veryfast", "-crf", "24",
            "-threads", "2", "-r", str(FPS), "-pix_fmt", "yuv420p",
            "-c:a", "aac", "-b:a", "128k", "-movflags", "+faststart",
            "-shortest", str(target)]
    subprocess.run(cmd, check=True, timeout=900)
    probe = subprocess.run(["ffprobe", "-v", "error", "-of", "json", "-show_streams",
                            str(target)], capture_output=True, text=True, check=True, timeout=30)
    streams = json.loads(probe.stdout)["streams"]
    v = next((s for s in streams if s["codec_type"] == "video"), None)
    a = next((s for s in streams if s["codec_type"] == "audio"), None)
    if not v or (v["width"], v["height"], v["codec_name"]) != (WIDTH, HEIGHT, "h264") or not a or a["codec_name"] != "aac":
        target.unlink(missing_ok=True)
        raise RuntimeError("Video failed the 1080x1920 H264/AAC output gate.")
    return target
