"""Lightweight visual-theme contract: no browser or external service needed."""
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
PAGES = ("index", "landing", "auth", "reset", "admin", "legal")
failures = []


def check(message, passed):
    print(("PASS " if passed else "FAIL ") + message)
    if not passed:
        failures.append(message)


for name in PAGES:
    html = (ROOT / "web" / (name + ".html")).read_text(encoding="utf-8")
    head, sep, tail = html.partition("</head>")
    check(name + ": valid document shell", html.startswith("<!doctype html>") and bool(sep) and "<style>" in head)
    check(name + ": dark burgundy accent", "--brand:#E7659F" in head and "color-scheme:dark" in head)
    check(name + ": mobile viewport", 'name="viewport"' in head)
    check(name + ": dark browser toolbar", '<meta name="theme-color" content="#0E0B14">' in head)
    check(name + ": no duplicate toolbar theme", head.count('name="theme-color"') == 1)
    check(name + ": no unexpected extra inline style", head.count("<style>") == 1 and head.count("</style>") == 1)

studio = (ROOT / "web/index.html").read_text(encoding="utf-8")
check("studio: sidebar remains present", 'id="nav"' in studio and 'id="menuBtn"' in studio)
check("studio: dynamic views and actions remain present", 'id="view"' in studio and 'id="actions"' in studio)
check("studio: mobile safe area and touch targets", "safe-area-inset-bottom" in studio and "min-height:44px" in studio)
check("studio: controls still have visible focus", ".nav-item:focus-visible" in studio)
check("studio: reduced-motion handling stays enabled", "prefers-reduced-motion:reduce" in studio)
check("studio: visual triage opt-in", 'id="o_visual_triage"' in studio and 'visual_triage:$("#o_visual_triage").checked' in studio)
check("studio: clip evidence breakdown", "function clipEvidence(c)" in studio and "AI rating breakdown" in studio)
check("studio: clip browser filter & sort", 'id="clip-filter"' in studio and 'id="clip-sort"' in studio)

print("ALL PASSED" if not failures else f"{len(failures)} FAILED")
raise SystemExit(1 if failures else 0)
