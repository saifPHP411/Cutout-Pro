"""Optional real browser download, then verify that same encrypted backup."""
import os
import subprocess
import sys
import tempfile
import time
import urllib.request
from pathlib import Path

from playwright.sync_api import expect, sync_playwright

PORT = int(os.environ.get("PEAKCUT_BACKUP_TEST_PORT", "8784"))
URL = f"http://127.0.0.1:{PORT}"
APP = """
from pathlib import Path
from studio import auth, config, db, server
db.init()
owner, _ = auth._create("mobile-restore@example.invalid", "Admin", password_hash="fixture")
with db.engine.begin() as con:
    con.execute(db.users.update().where(db.users.c.id == owner.id).values(is_admin=True))
(config.DATA_DIR / "fixture-session").write_text(auth.start_session(owner.id))
import uvicorn
uvicorn.run(server.app, host="127.0.0.1", port=config.PORT, log_level="warning")
"""


def main():
    with tempfile.TemporaryDirectory(prefix="pc-mobile-recovery-") as directory:
        root = Path(directory)
        env = {**os.environ, "PEAKCUT_DATA": directory, "PEAKCUT_PORT": str(PORT), "PEAKCUT_PUBLIC_URL": URL,
               "PEAKCUT_ACCOUNTS": "1", "PEAKCUT_BACKUP_MAINTENANCE": "1", "DATABASE_URL": "", "NO_PROXY": "localhost,127.0.0.1"}
        with (root / "server-errors").open("wb") as log:
            server = subprocess.Popen([sys.executable, "-c", APP], env=env, stdout=log, stderr=log)
        try:
            for _ in range(80):
                try:
                    urllib.request.urlopen(URL + "/healthz", timeout=.5).close()
                    break
                except OSError:
                    if server.poll() is not None:
                        raise RuntimeError("Fixture server failed to start.")
                    time.sleep(.1)
            else:
                raise RuntimeError("Fixture server did not become healthy.")
            cookie = (root / "fixture-session").read_text()
            (root / "fixture-session").unlink()
            with sync_playwright() as playwright:
                browser = playwright.chromium.launch(executable_path=os.environ.get("PEAKCUT_TEST_CHROMIUM", "/usr/bin/chromium"),
                                                      args=["--no-sandbox", "--disable-dev-shm-usage"])
                context = browser.new_context(**playwright.devices["Pixel 7"], accept_downloads=True)
                context.add_cookies([{"name": "peakcut_session", "value": cookie, "url": URL}])
                page = context.new_page()
                page.goto(URL + "/admin/backup")
                expect(page.get_by_role("heading", name="Encrypted backup")).to_be_visible()
                field = page.get_by_label("Passphrase")
                field.fill("staging recovery mobile passphrase")
                path = root / "download.pcb"
                with page.expect_download() as download:
                    page.get_by_role("button", name="Download encrypted backup").click()
                download.value.save_as(path)
                expect(field).to_have_value("")
                field.fill("staging recovery mobile passphrase")
                page.get_by_label("Encrypted backup").set_input_files(path)
                page.get_by_role("button", name="Verify backup").click()
                expect(page.locator("#backup-result")).to_contain_text("integrity_check")
                browser.close()
        finally:
            server.terminate()
            try:
                server.wait(timeout=8)
            except subprocess.TimeoutExpired:
                server.kill()
                server.wait()
    print("PASS encrypted admin backup round trip in a Pixel 7 viewport")


if __name__ == "__main__":
    main()
