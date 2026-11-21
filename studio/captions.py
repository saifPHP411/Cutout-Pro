"""Validate caption edits while keeping original speech separate from translations."""
import math

MODES = {"original": "Original speech", "english": "Translated to English"}


def edits(rows, start, end):
    if rows is None:
        return None
    if not isinstance(rows, list) or len(rows) > 2000:
        raise ValueError("Send up to 2,000 timed caption entries.")
    out = []
    for row in rows:
        if not isinstance(row, dict):
            raise ValueError("Each caption requires a start, end and text.")
        try:
            a, b = round(float(row["s"]), 2), round(float(row["e"]), 2)
        except (KeyError, TypeError, ValueError, OverflowError):
            raise ValueError("Each caption needs valid start and end times.") from None
        raw = row.get("w", "")
        text = " ".join(raw.split()) if isinstance(raw, str) else ""
        if not text or len(text) > 160 or not (math.isfinite(a) and math.isfinite(b) and start <= a < b <= end):
            raise ValueError("Caption text and times must fit inside the clip.")
        out.append({"w": text, "s": a, "e": b})
    out.sort(key=lambda row: row["s"])
    for row, following in zip(out, out[1:]):
        row["e"] = min(row["e"], following["s"])
    return [row for row in out if row["e"] - row["s"] >= 0.02]


def appearance(settings):
    if not isinstance(settings, dict):
        raise ValueError("Invalid caption appearance.")
    size = float(settings.get("size", 100))
    if not math.isfinite(size) or not 65 <= size <= 130:
        raise ValueError("Caption size must be 65–130%.")
    out = {"size": int(size)}
    for key in ("color", "highlight"):
        value = str(settings.get(key) or "")
        if value:
            if len(value) != 7 or value[0] != "#" or any(c not in "0123456789abcdefABCDEF" for c in value[1:]):
                raise ValueError("Choose a valid caption color.")
            out[key] = value.upper()
    return out


def ass_color(rgb):
    return rgb[5:7] + rgb[3:5] + rgb[1:3]


def dictionary(value):
    if not isinstance(value, str) or len(value) > 8000:
        raise ValueError("Use one spelling correction per line, up to 8,000 characters.")
    entries = {}
    for line in value.splitlines():
        if not line.strip():
            continue
        term, separator, spelling = line.partition("=")
        term, spelling = term.strip(), spelling.strip()
        if not separator or not term or not spelling or len(term) > 60 or len(spelling) > 60 or any(c.isspace() for c in term + spelling):
            raise ValueError("Each spelling correction must be a single word in the form heard=corrected.")
        entries[term] = spelling
    return entries


def apply_dictionary(words, entries):
    replacements = {name.casefold(): value for name, value in entries.items()}
    result = []
    for word in words:
        name = word["w"].rstrip(".,!?،؛؟")
        spelling = replacements.get(name.casefold())
        result.append(dict(word, w=spelling + word["w"][len(name):]) if spelling else word)
    return result
