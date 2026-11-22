"""Turns a time range into a vertical clip: 9:16 layout, bouncy word captions, hook title, cover."""
import os
import platform
import re
import shutil
import tempfile
import threading
from contextlib import contextmanager
from pathlib import Path

from . import captions, config, quality, reframe, roman, work_control
from .media import NO_WINDOW, run

W, H = config.OUT_W, config.OUT_H
SAFE_ZONES = {"none": (0.06, 0.08, 0.94, 0.90), "youtube": (0.09, 0.12, 0.83, 0.76),
              "tiktok": (0.10, 0.14, 0.78, 0.72), "instagram": (0.10, 0.15, 0.82, 0.70)}
ASPECTS = {"9:16": (W, H), "1:1": (1080, 1080), "16:9": (1920, 1080)}
TEMPLATES = {"gaming": "blur", "gaming-facecam": "gameplay", "reaction": "auto", "podcast": "auto",
             "interview": "split", "fullscreen-gameplay": "crop"}

# ASS colours are &HBBGGRR. `size` is the ASS font size (line height); _Font turns it into real pixels.
STYLES = {
    "bounce": {"label": "Bounce", "font": "Poppins ExtraBold", "size": 132, "base": "FFFFFF", "active": "00F0FF",
               "strong": "FFFFFF", "outline": 7, "pop": 112, "upper": True, "per": 3},
    "hype": {"label": "Hype", "font": "Anton", "size": 152, "base": "FFFFFF", "active": "3C3CFF",
             "strong": "00D7FF", "outline": 7, "pop": 114, "upper": True, "per": 2},
    "clean": {"label": "Clean", "font": "Poppins ExtraBold", "size": 100, "base": "FFFFFF", "active": "6BD7FF",
              "strong": "FFFFFF", "outline": 5, "pop": 106, "upper": False, "per": 4},
}
MODES = {
    "fast": {"label": "Fast · 540 × 960 preview", "preset": "ultrafast", "crf": 25, "scale": 0.5},
    "balanced": {"label": "Balanced · 1080 × 1920", "preset": "veryfast", "crf": 21, "scale": 1},
    "quality": {"label": "Quality · 1080 × 1920, finer detail", "preset": "medium", "crf": 18, "scale": 1},
}

HOOK = {"font": "Poppins ExtraBold", "size": 96, "ink": "14100E", "box": "FFFFFF", "pad_x": 36, "pad_y": 22, "radius": 26,
        "max_w": 900}
FONT_FILES = {"Poppins ExtraBold": "Poppins-ExtraBold.ttf", "Anton": "Anton-Regular.ttf", "Archivo Black": "ArchivoBlack-Regular.ttf"}
CAP_MAX_W = 960   # captions never run wider than this, whatever is said


class _Font:
    """Measures text exactly as the renderer will draw it, so every word can be placed by hand: the highlighted
    word can grow without pushing its neighbours, and the heading box fits its text."""
    _cache: dict = {}

    def __init__(self, name):
        import struct
        path = Path(__file__).resolve().parent / "fonts" / FONT_FILES.get(name, FONT_FILES["Poppins ExtraBold"])
        d = path.read_bytes()
        tables = {}
        for i in range(struct.unpack(">H", d[4:6])[0]):
            tag, _, off, _ = struct.unpack(">4sIII", d[12 + 16 * i: 28 + 16 * i])
            tables[tag] = off
        self.upm = struct.unpack(">H", d[tables[b"head"] + 18: tables[b"head"] + 20])[0]
        o = tables[b"OS/2"]
        self.asc, self.desc = struct.unpack(">HH", d[o + 74: o + 78])      # what the renderer sizes the font by
        self.cap = struct.unpack(">h", d[o + 88: o + 90])[0] or int(self.upm * 0.7)
        self.path = str(path)
        self._pil = {}

    @classmethod
    def get(cls, name):
        if name not in cls._cache:
            cls._cache[name] = cls(name)
        return cls._cache[name]

    def em(self, size):
        return size * self.upm / (self.asc + self.desc)

    def width(self, text, size):
        """Pixels wide at this ASS size."""
        try:
            from PIL import ImageFont
            if "f" not in self._pil:
                self._pil["f"] = ImageFont.truetype(self.path, 200)
            return self._pil["f"].getlength(text) * self.em(size) / 200
        except Exception:  # noqa: BLE001 - no measuring library: a fair estimate keeps clips rendering
            return len(text) * self.em(size) * 0.62

    def cap_shift(self, size):
        """How far below the anchor the middle of a capital letter sits when text is centred on a point."""
        return ((self.asc - self.desc) / 2 - self.cap / 2) * size / (self.asc + self.desc)


CAPTION_LEAD = 0.0  # measured: word stamps are within 0.15 s of the voice, so no shift is needed
def install_fonts():
    """Copy the bundled caption fonts next to the data, so captions look the same on every machine and server."""
    src = Path(__file__).resolve().parent / "fonts"
    for f in src.glob("*.ttf"):
        if not (config.FONTS_DIR / f.name).exists():
            shutil.copyfile(f, config.FONTS_DIR / f.name)


LAYOUTS = {"blur": "Full frame + blurred fill", "zoom": "Zoomed in + blurred fill", "crop": "Fill the screen (centre crop)",
           "left": "Fill the screen, keep the left side", "right": "Fill the screen, keep the right side",
           "fit": "Full frame on black", "split": "Split screen: left half on top, right half below (two people)",
           "gameplay": "Gameplay fills the screen, facecam in the corner",
           "auto": "Motion-aware crop"}
_FILLS = ("crop", "left", "right", "gameplay", "auto")      # layouts where the picture covers the whole frame


def _ts(t):
    t = max(0.0, t)
    h, m = int(t // 3600), int(t % 3600 // 60)
    return f"{h}:{m:02d}:{t % 60:05.2f}"


def _clean(text):
    return re.sub(r"[\x00-\x1f\x7f]+", " ", re.sub(r"[{}\\]", "", str(text))).strip()


def _key(word):
    return re.sub(r"[^a-z0-9']", "", word.lower())


def _groups(words, per):
    out, cur = [], []
    for w in words:
        if cur and (len(cur) >= per or sum(len(x["w"]) for x in cur) + len(w["w"]) > 16
                    or w["s"] - cur[-1]["e"] > 0.45 or cur[-1]["w"][-1:] in ".?!"):
            out.append(cur)
            cur = []
        cur.append(w)
    if cur:
        out.append(cur)
    return out


def geometry(src_w, src_h, layout, canvas=None):
    """Where the video sits in the 1080x1920 frame, and where hook and captions go."""
    width, height = canvas or (W, H)
    if src_h >= src_w and layout not in ("crop", "auto", "fit") and width < height:
        layout = "crop"  # already vertical or square: nothing to gain from a blurred fill
    if layout not in LAYOUTS:
        layout = "blur"
    if layout in ("blur", "fit"):
        fg_h = min(height, max(2, int(round(width * src_h / src_w / 2)) * 2))
    elif layout == "zoom":
        fg_h = round(height * 1000 / 1920 / 2) * 2
    else:
        fg_h = height
    top = (height - fg_h) // 2
    if canvas and canvas != (W, H):
        return layout, fg_h, height * 0.16, height * 0.74
    if layout in _FILLS:
        return layout, fg_h, 300, 1380
    if layout == "split":
        return layout, fg_h, 230, H // 2        # captions sit on the line between the two people
    return layout, fg_h, max(230, top - 190), min(1640, top + fg_h + 180)


def _hook_events(hook, dur, y, width=W, center=None, safe_width=None):
    """The dark heading in a white rounded box sized to its text."""
    center = width / 2 if center is None else center
    max_width = min(HOOK["max_w"], safe_width - 2 * HOOK["pad_x"]) if safe_width else HOOK["max_w"]
    f, size = _Font.get(HOOK["font"]), HOOK["size"]
    words = hook.split()
    if not words:
        return []
    lines = [" ".join(words)]
    if f.width(lines[0], size) > max_width and len(words) > 1:      # two lines, split where they come out most even
        cut = min(range(1, len(words)), key=lambda i: max(f.width(" ".join(words[:i]), size), f.width(" ".join(words[i:]), size)))
        lines = [" ".join(words[:cut]), " ".join(words[cut:])]
    widest = max(f.width(l, size) for l in lines)
    if widest > max_width:
        size = max(40, int(size * max_width / widest))
        widest = max(f.width(l, size) for l in lines)
    line_h = f.em(size) * 1.16
    bw, bh = widest + 2 * HOOK["pad_x"], line_h * len(lines) + 2 * HOOK["pad_y"]
    x0, y0, r = center - bw / 2, y - bh / 2, min(HOOK["radius"], bh / 2)
    x1, y1 = x0 + bw, y0 + bh
    n = lambda v: f"{v:.0f}"                                                         # noqa: E731
    box = (f"m {n(x0 + r)} {n(y0)} l {n(x1 - r)} {n(y0)} b {n(x1)} {n(y0)} {n(x1)} {n(y0)} {n(x1)} {n(y0 + r)} "
           f"l {n(x1)} {n(y1 - r)} b {n(x1)} {n(y1)} {n(x1)} {n(y1)} {n(x1 - r)} {n(y1)} "
           f"l {n(x0 + r)} {n(y1)} b {n(x0)} {n(y1)} {n(x0)} {n(y1)} {n(x0)} {n(y1 - r)} "
           f"l {n(x0)} {n(y0 + r)} b {n(x0)} {n(y0)} {n(x0)} {n(y0)} {n(x0 + r)} {n(y0)}")
    span = f"{_ts(0)},{_ts(dur)}"
    ev = [f"Dialogue: 3,{span},Hook,,0,0,0,,{{\\an7\\pos(0,0)\\fad(160,0)\\c&H{HOOK['box']}&\\p1}}{box}{{\\p0}}"]
    for i, line in enumerate(lines):
        cy = y - line_h * (len(lines) - 1) / 2 + i * line_h - f.cap_shift(size)
        ev.append(f"Dialogue: 4,{span},Hook,,0,0,0,,{{\\an5\\pos({round(center)},{cy:.0f})\\fad(160,0)\\fs{size}}}{line}")
    return ev


def build_ass(words, start, end, hook, style_name, highlight, hook_y, cap_y, canvas=None, appearance=None, safe_zone="none") -> str:
    width, height = canvas or (W, H)
    st = dict(STYLES.get(style_name, STYLES["bounce"]))
    if appearance:
        st["size"] = round(st["size"] * appearance.get("size", 100) / 100)
        if appearance.get("color"):
            st["base"] = st["strong"] = captions.ass_color(appearance["color"])
        if appearance.get("highlight"):
            st["active"] = captions.ass_color(appearance["highlight"])
    words = [dict(w, s=w["s"] - CAPTION_LEAD, e=w["e"] - CAPTION_LEAD) for w in words]
    strong = {_key(h) for h in highlight if _key(h)}
    head = f"""[Script Info]
ScriptType: v4.00+
PlayResX: {width}
PlayResY: {height}
WrapStyle: 2
ScaledBorderAndShadow: yes

[V4+ Styles]
Format: Name, Fontname, Fontsize, PrimaryColour, SecondaryColour, OutlineColour, BackColour, Bold, Italic, Underline, StrikeOut, ScaleX, ScaleY, Spacing, Angle, BorderStyle, Outline, Shadow, Alignment, MarginL, MarginR, MarginV, Encoding
Style: Cap,{st['font']},{st['size']},&H00{st['base']},&H00{st['base']},&H00000000,&H00000000,0,0,0,0,100,100,0,0,1,{st['outline']},0,5,0,0,0,1
Style: Hook,{HOOK['font']},{HOOK['size']},&H00{HOOK['ink']},&H00{HOOK['ink']},&H00000000,&H00000000,0,0,0,0,100,100,0,0,1,0,0,5,0,0,0,1

[Events]
Format: Layer, Start, End, Style, Name, MarginL, MarginR, MarginV, Effect, Text
"""
    dur = end - start
    safe_left, safe_top, safe_right, safe_bottom = SAFE_ZONES.get(safe_zone, SAFE_ZONES["none"])
    center = width / 2
    max_width = CAP_MAX_W
    if safe_zone != "none":
        center = width * (safe_left + safe_right) / 2
        max_width = int(width * (safe_right - safe_left) * 0.92)
        hook_y = max(hook_y, height * safe_top + min(110, height * .075))
        cap_y = min(cap_y, height * safe_bottom - min(st["size"], height * .075))
    ev = _hook_events(_clean(hook), dur, hook_y, width, center, max_width)
    f = _Font.get(st["font"])
    inside = [dict(w, w=_clean(w["w"])) for w in words if w["s"] >= start - 0.05 and w["s"] < end]
    inside = sorted((w for w in inside if w["w"]), key=lambda w: w["s"])
    for i, w in enumerate(inside[:-1]):
        w["e"] = max(w["s"], min(w["e"], inside[i + 1]["s"]))
    groups = _groups(inside, st["per"])
    for gi, g in enumerate(groups):
        g_start = max(0.0, g[0]["s"] - start)
        g_end = min(g[-1]["e"] + 0.25, groups[gi + 1][0]["s"] if gi + 1 < len(groups) else end) - start
        if g_end - g_start < 0.02:
            continue
        texts = []
        for x in g:
            t = re.sub(r"[.,;:]+$", "", x["w"])
            texts.append(t.upper() if st["upper"] else t)
        if not all(roman.drawable(t) for t in texts):
            # A script the caption font cannot draw (Japanese, Thai, Chinese...): its words cannot be measured, so
            # they are not placed one by one. The line is drawn whole and centred, in a font the system has for it.
            line = " ".join(texts)
            fs = max(44, min(int(st["size"] * 0.7), int(1500 / max(1, len(line)) * 1.6)))
            ev.append(f"Dialogue: 1,{_ts(g_start)},{_ts(g_end)},Cap,,60,60,0,,{{\\an5\\pos({round(center)},{cap_y:.0f})\\q0\\fs{fs}}}{line}")
            continue
        size = st["size"]
        gap = f.width("n n", size) - 2 * f.width("n", size) + (2 * st["outline"] + 6) * size / st["size"]   # a space, plus room for both outlines
        total = sum(f.width(t, size) for t in texts) + gap * (len(texts) - 1)
        if total > max_width:                                  # a long phrase: shrink it to fit, never run off the frame
            size = max(36, int(size * max_width / total))
            gap = f.width("n n", size) - 2 * f.width("n", size) + (2 * st["outline"] + 6) * size / st["size"]   # a space, plus room for both outlines
            total = sum(f.width(t, size) for t in texts) + gap * (len(texts) - 1)
        cy = cap_y - f.cap_shift(size)
        x = center - total / 2
        p = st["pop"]
        for i, (w, t) in enumerate(zip(g, texts)):
            wd = f.width(t, size)
            p = max(104, min(st["pop"], 100 + int(2200 / max(wd, 1))))   # long words grow less, so they never touch their neighbours
            pos = f"\\an5\\pos({x + wd / 2:.0f},{cy:.0f})\\fs{size}"
            x += wd + gap
            a = max(g_start, w["s"] - start)
            b = (g[i + 1]["s"] - start) if i + 1 < len(g) else g_end
            base = st["strong"] if _key(w["w"]) in strong else st["base"]
            if a - g_start >= 0.02:                                                   # waiting to be said
                ev.append(f"Dialogue: 1,{_ts(g_start)},{_ts(a)},Cap,,0,0,0,,{{{pos}\\c&H{base}&}}{t}")
            if b - a >= 0.02:                                                         # being said: colour and a quick pop, in front
                ev.append(f"Dialogue: 2,{_ts(a)},{_ts(b)},Cap,,0,0,0,,{{{pos}\\c&H{st['active']}&\\fscx94\\fscy94"
                          f"\\t(0,90,\\fscx{p}\\fscy{p})\\t(90,200,\\fscx{p - 5}\\fscy{p - 5})}}{t}")
            if g_end - b >= 0.02:                                                     # said
                ev.append(f"Dialogue: 1,{_ts(b)},{_ts(g_end)},Cap,,0,0,0,,{{{pos}\\c&H{base}&\\fscx{p - 5}\\fscy{p - 5}"
                          f"\\t(0,90,\\fscx100\\fscy100)}}{t}")
    return head + "\n".join(ev) + "\n"


PIP_W = 380            # how wide the facecam is in the 1080-wide frame
PIP_MARGIN = 26


def _filter(layout, fg_h, facecam=None, canvas=None, focus=0.5, points=None):
    W, H = canvas or (config.OUT_W, config.OUT_H)
    pip_w = round(W * PIP_W / config.OUT_W / 2) * 2
    if layout == "gameplay" and facecam:
        # The game fills the screen, and the webcam sits in the top corner of it, so a viewer sees
        # the play and the face at the same time.
        fx, fy, fw, fh = (int(v) for v in facecam)
        if fw > 8 and fh > 8:
            pip_h = max(2, int(round(pip_w * fh / fw / 2)) * 2)
            return (f"[0:v]fps={config.OUT_FPS},split=2[base][fc];"
                    f"[base]scale={W}:{H}:force_original_aspect_ratio=increase,crop={W}:{H},setsar=1[b];"
                    f"[fc]crop={fw}:{fh}:{fx}:{fy},scale={pip_w}:{pip_h},setsar=1[p];"
                    f"[b][p]overlay={W - pip_w - PIP_MARGIN}:{PIP_MARGIN}[v0];")
    fill = f"[0:v]fps={config.OUT_FPS},scale={W}:{H}:force_original_aspect_ratio=increase,crop={W}:{H}"
    if points:
        # Dynamic tracking operates in source pixels. Preserve the original scaler for fixed exports.
        cut = f"trunc(ih*{W}/{H}/2)*2"
        x = reframe.crop_expr(points)
        return f"[0:v]fps={config.OUT_FPS},crop={cut}:ih:'{x}':0,scale={W}:{H},setsar=1[v0];"
    if layout == "crop" or (layout == "gameplay" and not facecam):
        return fill + ",setsar=1[v0];"
    if layout == "auto":
        return fill + f":(in_w-out_w)*{focus:.4f}:(in_h-out_h)/2,setsar=1[v0];"
    if layout == "left":
        return fill + ":0:0,setsar=1[v0];"
    if layout == "right":
        return fill + ":in_w-out_w:0,setsar=1[v0];"
    if layout in ("blur", "zoom") and fg_h == H:
        # The foreground already covers every pixel, so there is no background to compose.
        return fill + ",setsar=1[v0];"
    if layout == "fit":
        return (f"[0:v]fps={config.OUT_FPS},scale={W}:{H}:force_original_aspect_ratio=decrease:force_divisible_by=2,"
                f"pad={W}:{H}:(ow-iw)/2:(oh-ih)/2:black,setsar=1[v0];")
    if layout == "split":
        half = f"scale={W}:{H // 2}:force_original_aspect_ratio=increase,crop={W}:{H // 2}"
        return (f"[0:v]fps={config.OUT_FPS},split=2[l][r];[l]crop=iw/2:ih:0:0,{half}[t];"
                f"[r]crop=iw/2:ih:iw/2:0,{half}[u];[t][u]vstack,setsar=1[v0];")
    small_w, small_h = W // 4 // 2 * 2, H // 4 // 2 * 2
    return (f"[0:v]fps={config.OUT_FPS},split=2[a][b];"
            f"[a]scale={small_w}:{small_h}:force_original_aspect_ratio=increase,crop={small_w}:{small_h},boxblur=6:2,"
            f"eq=brightness=-0.14:saturation=1.15,scale={W}:{H}[bg];"
            f"[b]scale={W}:{fg_h}:force_original_aspect_ratio=increase,crop={W}:{fg_h}[fg];"
            f"[bg][fg]overlay=0:(H-h)/2,setsar=1[v0];")


def _hook_y(layout, hook_y, facecam=None):
    """Where the heading goes. In the gameplay layout the webcam sits in the top corner, so a heading
    across the top would run under it: it goes just below the picture instead."""
    if layout == "gameplay" and facecam and len(facecam) == 4 and facecam[2] > 8:
        return PIP_MARGIN + int(round(PIP_W * facecam[3] / facecam[2])) + 32
    return hook_y


_qsv_ok = None
RENDER_SECONDS = 600      # one clip's video encode; far beyond normal, so a frozen encoder is stopped, not waited on


_ENCODERS = threading.BoundedSemaphore(config.CLIP_WORKERS)


@contextmanager
def _encoder_slot():
    while not _ENCODERS.acquire(timeout=0.15):
        work_control.check()
    try:
        work_control.check()
        yield
    finally:
        _ENCODERS.release()


def render_clip(src: Path, out_dir: Path, name: str, clip: dict, words: list, src_w: int, src_h: int) -> dict:
    """Writes <name>.mp4, <name>.jpg and <name>.ass into out_dir. Returns file names."""
    out_dir.mkdir(parents=True, exist_ok=True)
    start, end = clip["start"], clip["end"]
    canvas = ASPECTS.get(clip.get("aspect"), (W, H))
    layout, fg_h, hook_y, cap_y = geometry(src_w, src_h, clip.get("layout", "blur"), canvas)
    requested_layout = clip.get("layout")
    safe_zone = clip.get("safe_zone", "none")
    safe = SAFE_ZONES.get(safe_zone, SAFE_ZONES["none"])
    focus, points = 0.5, []
    if layout == "auto" and src_w / src_h > canvas[0] / canvas[1]:
        points = reframe.face_path(src, start, end, canvas[0]/canvas[1], (safe[0], safe[2], safe[1], safe[3]))
        if not points:
            layout, fg_h, hook_y, cap_y = geometry(src_w, src_h, "fit", canvas)
    face = clip.get("facecam")
    if layout == "gameplay" and not (face and len(face) == 4):
        # A centre crop can erase the streamer's facecam entirely. Preserve the
        # full source frame until a camera box is confidently identified.
        layout, fg_h, hook_y, cap_y = geometry(src_w, src_h, "blur", canvas)
    hook_y = min(canvas[1] * 0.45, _hook_y(layout, hook_y, face))
    ass = build_ass(words if clip.get("captions", True) else [], start, end, clip.get("hook", ""), clip.get("style", "bounce"),
                    clip.get("highlight_words", []), hook_y, cap_y, canvas, clip.get("caption_appearance"), safe_zone)
    # The clip is made in a short work folder and moved into place afterwards. The caption step of ffmpeg cannot
    # open files whose full path is longer than 260 characters on Windows, which a data folder deep inside other
    # folders easily reaches. Inside the work folder every name is short and relative (a drive colon would also
    # break the filter syntax).
    control = work_control.current.get()
    scratch = control.work_dir if control and os.name != "nt" else None
    if scratch:
        scratch.mkdir(exist_ok=True)
    work = Path(tempfile.mkdtemp(prefix="pk", dir=scratch))
    try:
        # The title and description are also written into the file itself, for any place that reads them from it.
        meta = {"title": clip.get("title") or "", "description": clip.get("description") or "", "comment": clip.get("description") or ""}
        with _encoder_slot():
            result = _render_in(work, src.resolve(), out_dir, name, start, end, layout, fg_h, ass, face=face, meta=meta,
                              mode=clip.get("mode"), canvas=canvas, focus=focus, points=points, captions_expected=clip.get("captions", True))
            if requested_layout == "auto":
                result["layout"] = "auto"
            return result
    finally:
        shutil.rmtree(work, ignore_errors=True)


def _render_in(work: Path, src: Path, out_dir: Path, name: str, start, end, layout, fg_h, ass: str, face=None, meta=None, mode=None, canvas=None, focus=0.5, captions_expected=False, points=None) -> dict:
    global _qsv_ok
    (work / f"{name}.ass").write_text(ass, encoding="utf-8")
    (work / "f").mkdir()
    for f in config.FONTS_DIR.glob("*.ttf"):
        shutil.copyfile(f, work / "f" / f.name)
    profile = MODES.get(mode, MODES["balanced"])
    scale = profile["scale"]
    width, height = canvas or (W, H)
    export_canvas = (int(width * scale), int(height * scale))
    graph = _filter(layout, max(2, round(fg_h * scale / 2) * 2), face, export_canvas, focus, points)
    canvas = "v0"
    if "Dialogue:" in ass:
        graph += f"[{canvas}]ass={name}.ass:fontsdir=f[v]"
        canvas = "v"
    fc = graph.rstrip(";")
    base = ["ffmpeg", "-y", "-v", "error", "-ss", f"{start:.2f}", "-t", f"{end - start:.2f}", "-i", str(src),
            "-filter_complex_threads", str(config.FFMPEG_THREADS), "-filter_complex", fc, "-map", f"[{canvas}]", "-map", "0:a:0", "-af", "loudnorm=I=-14:TP=-1.5:LRA=11",
            "-c:a", "aac", "-b:a", "160k", "-ar", "48000"]
    for k, v in (meta or {}).items():
        if v:
            base += ["-metadata", f"{k}={v}"]
    base += ["-movflags", "+faststart", "-pix_fmt"]
    encoders = []
    can_qsv = (platform.machine().lower() in ("x86_64", "amd64")
               and (os.name == "nt" or Path("/dev/dri/renderD128").exists()))
    if _qsv_ok is not False and can_qsv and profile is MODES["balanced"]:
        encoders.append(("qsv", ["nv12", "-c:v", "h264_qsv", "-global_quality", "23", "-preset", "fast"]))
    encoders.append(("x264", ["yuv420p", "-c:v", "libx264", "-threads", str(config.FFMPEG_THREADS),
                              "-crf", str(profile["crf"]), "-preset", profile["preset"]]))
    err = None
    for label, enc in encoders:
        p = work_control.run(base + enc + [f"{name}.mp4"], cwd=work, capture_output=True, text=True,
                           encoding="utf-8", errors="replace", creationflags=NO_WINDOW, timeout=RENDER_SECONDS)
        if p.returncode == 0:
            if label == "qsv":
                _qsv_ok = True
            break
        err = p.stderr[-1200:]
        if label == "qsv":
            _qsv_ok = False
    else:
        raise RuntimeError(f"Render failed: {err}")
    qc = quality.export_report(work / f"{name}.mp4", export_canvas, end - start, captions_expected,
                               ass, deep=mode == "quality", preview=mode == "fast")
    if qc["status"] == "failed":
        return {"qc": qc, "layout": layout}
    cover_t = min(1.2, (end - start) / 2)
    run(["ffmpeg", "-y", "-v", "error", "-ss", f"{cover_t:.2f}", "-i", f"{name}.mp4", "-frames:v", "1",
         "-q:v", "3", "-threads", "1", f"{name}.jpg"], cwd=work)
    work_control.check()
    for ext in ("mp4", "jpg", "ass"):
        shutil.move(str(work / f"{name}.{ext}"), str(out_dir / f"{name}.{ext}"))
    return {"file": f"{name}.mp4", "cover": f"{name}.jpg", "layout": layout, "qc": qc, "tracking": "face" if points else "fit" if layout == "fit" else "fixed"}
