"""Optional Chromium mobile check against the actual app and its job API."""
import os
import subprocess
import sys
import tempfile
import time
import urllib.request
from pathlib import Path

from playwright.sync_api import expect, sync_playwright

PORT = int(os.environ.get("PEAKCUT_MOBILE_TEST_PORT", "8794"))
BASE = f"http://127.0.0.1:{PORT}"
SCRIPT = """
from studio import accounts, auth, config, ctx, jobs, server, work_control

def delayed(job, pool):
    jobs._set(job, status="running", phase="transcribing", stage="Writing the transcript", progress=0.4)
    work_control.sleep(180)

jobs._process_job = delayed

@server.app.on_event("startup")
def seed():
    with ctx.as_user(auth.local_user().id):
        accounts.save_settings({"tour_done": True})
        jobs.create({"type": "file", "name": "Mobile control fixture"}, {}, enqueue=True)

import uvicorn
uvicorn.run(server.app, host="127.0.0.1", port=config.PORT, log_level="warning")
"""


def run():
    with tempfile.TemporaryDirectory(prefix="peakcut-mobile-") as storage:
        env = {**os.environ, "PEAKCUT_DATA": storage, "PEAKCUT_PORT": str(PORT), "PEAKCUT_PUBLIC_URL": BASE,
               "PEAKCUT_ACCOUNTS": "0", "PEAKCUT_FETCH_MODEL": "0", "PEAKCUT_UPDATE_CHECK": "0",
               "DATABASE_URL": "", "NO_PROXY": "127.0.0.1,localhost"}
        log = Path(storage) / "server.log"
        with log.open("w", encoding="utf-8") as output:
            server = subprocess.Popen([sys.executable, "-c", SCRIPT], env=env, stdout=output, stderr=subprocess.STDOUT)
        try:
            for _ in range(80):
                try:
                    urllib.request.urlopen(BASE + "/healthz", timeout=0.5).close()
                    break
                except OSError:
                    if server.poll() is not None:
                        raise RuntimeError(log.read_text())
                    time.sleep(0.1)
            else:
                raise RuntimeError("The test app did not start.")
            with sync_playwright() as p:
                browser = p.chromium.launch(executable_path=os.environ.get("PEAKCUT_TEST_CHROMIUM", "/usr/bin/chromium"),
                                            args=["--no-sandbox", "--disable-dev-shm-usage"])
                page = browser.new_page(**p.devices["Pixel 7"])
                errors = []
                page.on("pageerror", lambda error: errors.append(str(error)))
                page.goto(BASE + "/app/videos")
                pause = page.get_by_role("button", name="Pause", exact=True)
                expect(pause).to_be_visible()
                assert pause.bounding_box()["height"] >= 44
                pause.click()
                resume = page.get_by_role("button", name="Resume", exact=True)
                expect(resume).to_be_visible(timeout=12000)
                page.get_by_role("button", name="Add chat counts").click()
                dialog = page.get_by_role("dialog")
                dialog.get_by_label("Seconds, count").fill("0,1\n12,22")
                dialog.get_by_label("I may use these aggregate counts").check()
                dialog.get_by_role("button", name="Save", exact=True).click()
                expect(dialog).to_be_hidden()
                resume.click()
                stop = page.get_by_role("button", name="Stop", exact=True)
                expect(stop).to_be_visible(timeout=12000)
                stop.click()
                retry = page.get_by_role("button", name="Retry", exact=True)
                expect(retry).to_be_visible(timeout=12000)
                page.get_by_role("button", name="View Logs").click()
                expect(page.get_by_role("heading", name="Video logs")).to_be_visible()
                page.get_by_role("dialog").get_by_role("button", name="Close", exact=True).click()
                expect(page.get_by_role("dialog")).to_be_hidden()
                try:
                    retry.click(timeout=10000)
                except Exception as exc:
                    raise AssertionError(f"{page.url}: {page.locator('main').inner_text()}") from exc
                page.get_by_role("button", name="Stop & Delete", exact=True).click(timeout=12000)
                page.get_by_role("dialog").get_by_role("button", name="Stop & Delete", exact=True).click()
                expect(page.get_by_text("No videos yet")).to_be_visible(timeout=12000)
                page.goto(BASE + "/app/campaigns")
                page.get_by_role("button", name="New campaign").click()
                form = page.get_by_role("dialog")
                form.get_by_label("Campaign name").fill("Phone workflow")
                form.get_by_label("Creator", exact=True).fill("Demo creator")
                form.get_by_label("Authorized video URL or upload").fill("upload")
                form.get_by_label("youtube").check()
                form.get_by_role("button", name="Save", exact=True).click()
                expect(page.get_by_role("heading", name="Phone workflow")).to_be_visible(timeout=12000)
                page.goto(BASE + "/app/analytics")
                expect(page.get_by_role("heading", name="Ranking experiments")).to_be_visible()
                page.get_by_role("button", name="Enable data collection").click()
                page.get_by_role("dialog").get_by_role("button", name="Enable learning").click()
                expect(page.get_by_role("button", name="Disable data collection")).to_be_visible()
                assert page.evaluate("document.documentElement.scrollWidth === window.innerWidth")
                assert not errors, errors
                browser.close()
        finally:
            server.terminate()
            try:
                server.wait(timeout=12)
            except subprocess.TimeoutExpired:
                server.kill()
                server.wait()
    print("PASS: Pixel 7 viewport, job controls, chat import, log dialog, campaign creation and Analytics opt-in.")


if __name__ == "__main__":
    run()
