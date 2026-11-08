"""Plays one user in a real Chromium against the stack of up.sh. Each call is one step:

  drive.py launch USER          sign in to Moodle as USER, open the LTI activity, land in the tool
  drive.py upload USER FILE     on the course page (teacher), upload FILE, wait until ready or failed
  drive.py retry USER           click the first "Thử lại" of a failed job, wait the same way
  drive.py ask USER QUESTION    in the chat of USER's last page, ask and wait for the answer
  drive.py visit USER PATH      open PATH on the tool, print where it ends up and the chat log

Run with: uv run --no-project --with playwright python -I devops/browser-e2e/drive.py ...
Users from devops/lti-e2e/setup.php: e2eteacher, e2estudent. The browser session and last page of
each user are kept in $BE2E_WORK, so later steps go on from there. Console errors and HTTP >= 400
are printed after each step; screenshots go to $BE2E_WORK/shots."""
import contextlib
import json
import os
import re
import sys
import tempfile
import time
from collections import Counter
from pathlib import Path

from playwright.sync_api import TimeoutError as PlaywrightTimeout
from playwright.sync_api import sync_playwright

WORK = Path(os.environ.get("BE2E_WORK") or Path(tempfile.gettempdir()) / "browser-e2e")
TOOL_PORT = os.environ.get("TOOL_PORT", "58010")
MOODLE, TOOL = "http://localhost:58081", f"http://localhost:{TOOL_PORT}"
PASSWORD = "E2e-user-pass-1!"

step, user, *rest = sys.argv[1:]
cfg = json.loads((WORK / "moodle.json").read_text())
state, last = WORK / f"state-{user}.json", WORK / f"url-{user}.txt"
shots = WORK / "shots"
shots.mkdir(exist_ok=True)
problems: list[str] = []


def shot(page, name):
    page.screenshot(path=str(shots / f"{user}-{name}.png"), full_page=True)


def chat_log(page):
    log = page.get_by_role("log", name="Hội thoại")
    return log.inner_text() if log.count() else "(no chat log)"


def wait_for_documents(page):
    """Until no document is being processed and one shows ready or failed."""
    started = time.time()
    while time.time() - started < 600:
        body = page.locator("main").inner_text()
        if time.time() - started > 5 and "Đang xử lý tài liệu" not in body and ("Sẵn sàng" in body or "Lỗi" in body):
            break
        time.sleep(3)
    print(f"{step} finished after {time.time() - started:.0f}s")
    print(page.locator("main").inner_text()[:2500])


with sync_playwright() as p:
    browser = p.chromium.launch()
    ctx = browser.new_context(storage_state=str(state) if state.exists() and step != "launch" else None,
                              viewport={"width": 1280, "height": 900}, locale="vi-VN")
    page = ctx.new_page()
    page.on("console", lambda m: m.type == "error" and problems.append(f"console: {m.text[:300]}"))
    page.on("pageerror", lambda e: problems.append(f"pageerror: {e}"))
    page.on("response", lambda r: r.status >= 400 and problems.append(f"HTTP {r.status} {r.request.method} {r.url}"))

    if step in ("upload", "retry", "ask"):
        page.goto(last.read_text())
        page.wait_for_load_state("networkidle")

    if step == "launch":
        # Settled first: the login token belongs to the session, and a form sent while the page's own
        # requests still set the session cookie is refused as "Your login info may be incorrect".
        page.goto(f"{MOODLE}/login/index.php", wait_until="networkidle")
        page.fill("#username", user)
        page.fill("#password", PASSWORD)
        page.click("#loginbtn")
        # Not networkidle: it can settle on the login page before Moodle's redirects begin, and the
        # launch then lands back on the login form.
        try:
            page.wait_for_url(lambda url: "/login/" not in url, timeout=30_000)
        except PlaywrightTimeout:
            shot(page, "login-failed")
            alerts = page.locator(".alert, .loginerrors, [role=alert]").all_inner_texts()
            sys.exit(f"Moodle sign-in did not finish; at {page.url}; alerts: {alerts}")
        page.goto(f"{MOODLE}/mod/lti/launch.php?id={cfg['cmid']}")
        try:
            page.wait_for_url(re.compile(rf"localhost:{TOOL_PORT}/(courses|learn)/"), timeout=60_000)
        except PlaywrightTimeout:
            shot(page, "launch-failed")
            sys.exit(f"did not reach the tool; stopped at {page.url}:\n{page.locator('body').inner_text()[:1500]}")
        page.wait_for_load_state("networkidle")
        print("landed:", page.url)
        shot(page, "launch")
    elif step == "upload":
        # The three requests of an upload (app/api/documents.py), in the order the page sends them.
        sent: list[str] = []
        page.on("requestfinished", lambda r: re.search(r"/documents/(uploads|[0-9a-f-]{36}/(content|complete))", r.url)
                and sent.append(f"{r.method} {r.url.split('?')[0]} -> {r.response().status}"))
        page.locator("input[type=file]").set_input_files(rest[0])
        wait_for_documents(page)
        print("upload requests:", *sent, sep="\n  ")
        shot(page, "upload")
    elif step == "retry":
        page.get_by_role("button", name="Thử lại").first.click()
        wait_for_documents(page)
        shot(page, "retry")
    elif step == "ask":
        box = page.locator("#course-chat-message")
        box.wait_for(timeout=30_000)
        box.fill(rest[0])
        started = time.time()
        page.get_by_role("button", name="Gửi câu hỏi").click()
        with contextlib.suppress(PlaywrightTimeout):  # an answer faster than the poll is fine too
            page.get_by_role("button", name="Đang trả lời...").wait_for(timeout=5_000)
        page.get_by_role("button", name="Gửi câu hỏi").wait_for(timeout=180_000)
        print(f"answered after {time.time() - started:.1f}s")
        print(chat_log(page)[-2500:])
        shot(page, "ask")
    elif step == "visit":
        page.goto(f"{TOOL}{rest[0]}")
        page.wait_for_load_state("networkidle")
        time.sleep(1)
        print("ended at:", page.url)
        print(chat_log(page)[-2500:])
        shot(page, "visit-" + (rest[0].strip("/").replace("/", "_") or "root"))
    else:
        sys.exit(f"unknown step {step}")

    ctx.storage_state(path=str(state))
    last.write_text(page.url)
    browser.close()

print("problems:", "none" if not problems else "")
for text, count in Counter(problems).items():
    print(f"  {count}x {text}")
