"""Real Chromium QA on Pixel 7 and desktop, using only fake local test data.

Run: python -m playwright install chromium
     python tests/test_browser_smart.py

Screenshots are written to artifacts/browser/; no production credentials are used.
"""
import os
import subprocess
import sys
import tempfile
import time
import urllib.request
from pathlib import Path

from playwright.sync_api import expect, sync_playwright

ROOT = Path(__file__).resolve().parent.parent
PORT = int(os.environ.get("PEAKCUT_BROWSER_TEST_PORT", "8792"))
BASE = f"http://127.0.0.1:{PORT}"
ARTIFACTS = ROOT / "artifacts" / "browser"
ARTIFACTS.mkdir(parents=True, exist_ok=True)

SERVER_SCRIPT = r"""
import shutil
from pathlib import Path
from PIL import Image, ImageDraw
from studio import auth, accounts, config, ctx, jobs, server

@server.app.on_event("startup")
def seed_browser_fixtures():
    with ctx.as_user(auth.local_user().id):
        accounts.save_settings({"tour_done": True})
        job = jobs.create({"type": "file", "name": "Browser QA — synthetic footage"}, {},
                          enqueue=False)
        folder = Path(job["dir"]) / "clips"
        folder.mkdir(exist_ok=True)
        shutil.copyfile("web/brand/demo-bounce.mp4", folder / "preview.mp4")
        cover = Image.new("RGB", (360, 640), (65, 29, 65))
        paint = ImageDraw.Draw(cover)
        paint.ellipse((65, 130, 295, 360), fill=(224, 107, 164))
        paint.rectangle((45, 410, 315, 440), fill=(245, 207, 225))
        cover.save(folder / "preview.jpg")
        high = {
            "id":"clip01", "status":"done", "score":88,
            "title":"Browser QA highlight", "hook":"WHAT JUST HAPPENED",
            "reason":"Test-only highlight, not a real AI decision.",
            "start":0.0, "end":2.0, "version":0,
            "style":"clean", "layout":"blur", "captions":True,
            "aspect":"9:16", "caption_mode":"original", "safe_zone":"youtube",
            "mode":"balanced", "description":"Synthetic UI smoke test.",
            "hashtags":["test"], "file":"clips/preview.mp4", "cover":"clips/preview.jpg",
            "qc":{"status":"ready", "reasons":[]}, "reviewed":True, "posts":{},
            "ratings":{"hook_strength":91,"emotional_impact":86,"entertainment":82,
                       "context_clarity":90,"standalone_story":84,"ending_quality":88,
                       "retention":79,"originality":75},
            "signals":{"audio_peak":True, "spoken_reaction":True,
                       "chat_burst":False, "motion_spike":True}
        }
        low = {
            "id":"clip02", "status":"error", "score":54, "title":"Low-score review example",
            "hook":"LOW SCORE", "reason":"Deliberate test fixture.", "start":7, "end":12,
            "version":0, "error":"Deliberate test-only status, no export was attempted.",
            "style":"clean", "layout":"blur", "captions":False,
            "aspect":"9:16", "caption_mode":"original", "safe_zone":"none",
            "mode":"balanced", "description":"", "hashtags":[], "posts":{},
            "qc":{"status":"needs_review", "reasons":["Synthetic QA status"]},
            "signals":{"audio_peak":False}
        }
        job.update(status="done", phase="completed", stage="Synthetic QA fixtures",
                   progress=1.0, clips=[high, low], duration=12.0,
                   width=1280, height=720)
        jobs.save(job)

import uvicorn
uvicorn.run(server.app, host="127.0.0.1", port=config.PORT, log_level="warning")
"""


def assert_no_overflow(page):
    assert page.evaluate("document.documentElement.scrollWidth <= innerWidth + 2"), (
        page.url, page.evaluate("({width:innerWidth,scroll:document.documentElement.scrollWidth})")
    )


def visit_and_check(browser, context_options, device):
    page = browser.new_page(**context_options)
    errors = []
    page.on("pageerror", lambda error: errors.append(str(error)))
    try:
        page.goto(BASE + "/app/videos", wait_until="domcontentloaded")
        expect(page.locator("#addform")).to_be_visible()
        page.locator('[data-act="toggleopts"]').first.click()
        checkbox = page.locator("#o_visual_triage")
        expect(checkbox).to_be_visible()
        assert not checkbox.is_checked(), "Visual Highlights must be disabled by default"
        checkbox.check()
        assert page.evaluate("jobOptions().visual_triage") is True
        # The opt-in description wraps to multiple lines on narrow Android screens.
        toggle_row = page.locator(".visual-triage-toggle")
        expect(toggle_row).to_be_visible()
        assert toggle_row.evaluate("(el) => el.scrollHeight <= el.clientHeight + 2"), (
            "Visual Highlights description overflowed its own toggle label"
        )
        # Desktop uses a short grid-height transition; capture only after it settles.
        page.wait_for_timeout(400)
        assert_no_overflow(page)
        page.screenshot(path=str(ARTIFACTS / (device + "-create.png")), full_page=True)

        page.goto(BASE + "/app/clips", wait_until="domcontentloaded")
        expect(page.locator("#clip-filter")).to_be_visible()
        expect(page.locator(".clip")).to_have_count(2)
        page.locator("#clip-filter").select_option("high")
        expect(page.locator(".clip")).to_have_count(1)
        expect(page.get_by_text("Browser QA highlight", exact=True)).to_be_visible()
        page.locator("#clip-sort").select_option("shortest")
        assert_no_overflow(page)
        page.screenshot(path=str(ARTIFACTS / (device + "-clips.png")), full_page=True)

        page.locator('[data-act="openclip"]').first.click()
        modal = page.locator("#clipdlg")
        expect(modal).to_be_visible()
        expect(modal.get_by_text("Sampled motion spike", exact=True)).to_be_visible()
        expect(modal.get_by_text("Spoken reaction", exact=True)).to_be_visible()
        expect(modal.get_by_text("AI rating breakdown")).to_be_visible()
        modal.locator(".clip-score-details summary").click()
        expect(modal.get_by_text("Opening hook")).to_be_visible()
        expect(modal.get_by_text("Editorial estimates")).to_be_visible()
        assert_no_overflow(page)
        page.screenshot(path=str(ARTIFACTS / (device + "-editor.png")), full_page=True)
        # The modal must remain navigable past the video and long score explanation.
        remake = modal.locator('[data-act="remake"]')
        remake.scroll_into_view_if_needed(timeout=6000)
        expect(remake).to_be_visible()
        page.screenshot(path=str(ARTIFACTS / (device + "-editor-bottom.png")), full_page=True)
        page.keyboard.press("Escape")
        page.locator("#clip-filter").select_option("review")
        expect(page.locator(".clip")).to_have_count(1)
        expect(page.get_by_text("Low-score review example", exact=True)).to_be_visible()
        assert_no_overflow(page)

        page.goto(BASE + "/app/settings/apps", wait_until="domcontentloaded")
        expect(page.get_by_role("heading", name="Original YouTube Autopilot")).to_be_visible()
        expect(page.get_by_text("Off until enabled")).to_be_visible()
        assert_no_overflow(page)
        page.screenshot(path=str(ARTIFACTS / (device + "-original-autopilot.png")), full_page=True)
        assert not errors, errors
        print(f"PASS {device}: dark studio, option state, score filter, review filter, explanation and viewport")
    except BaseException:
        page.screenshot(path=str(ARTIFACTS / (device + "-failure.png")), full_page=True)
        raise
    finally:
        page.close()


def main():
    with tempfile.TemporaryDirectory(prefix="peakcut-browser-") as storage:
        env = {**os.environ, "PEAKCUT_DATA": storage, "PEAKCUT_PORT": str(PORT),
               "PEAKCUT_PUBLIC_URL": BASE, "PEAKCUT_ACCOUNTS": "0",
               "PEAKCUT_FETCH_MODEL": "0", "PEAKCUT_UPDATE_CHECK": "0",
               "PEAKCUT_AI_MODELS": "", "PEAKCUT_AI_KEY": "", "DATABASE_URL": "",
               "NO_PROXY": "127.0.0.1,localhost"}
        log = Path(storage) / "server.log"
        with log.open("w", encoding="utf-8") as output:
            process = subprocess.Popen([sys.executable, "-c", SERVER_SCRIPT], cwd=ROOT,
                                       env=env, stdout=output, stderr=subprocess.STDOUT)
        try:
            for _ in range(100):
                try:
                    urllib.request.urlopen(BASE + "/healthz", timeout=1).close()
                    break
                except OSError:
                    if process.poll() is not None:
                        raise RuntimeError(log.read_text(encoding="utf-8", errors="replace")[-5000:])
                    time.sleep(0.2)
            else:
                raise RuntimeError("Browser QA server did not become ready.\n" + log.read_text()[-3000:])
            with sync_playwright() as playwright:
                binary = os.environ.get("PEAKCUT_TEST_CHROMIUM") or playwright.chromium.executable_path
                browser = playwright.chromium.launch(executable_path=binary, headless=True,
                                                     args=["--no-sandbox", "--disable-dev-shm-usage"])
                try:
                    visit_and_check(browser, playwright.devices["Pixel 7"], "pixel7")
                    visit_and_check(browser, {"viewport":{"width":1440,"height":900},
                                              "device_scale_factor":1}, "desktop")
                finally:
                    browser.close()
        finally:
            process.terminate()
            try:
                process.wait(timeout=12)
            except subprocess.TimeoutExpired:
                process.kill()
                process.wait()


if __name__ == "__main__":
    main()
