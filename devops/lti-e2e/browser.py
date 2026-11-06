"""Plays the browser through a real Moodle 5.2 LTI 1.3 launch: sign in to Moodle, open the
activity, submit each auto-posting form as the page's script would, stop at the frontend page."""
import json
import sys
from urllib.parse import parse_qs, urljoin, urlparse

import httpx
from bs4 import BeautifulSoup

MOODLE, TOOL = "http://localhost:58081", "http://localhost:58000"
with open(sys.argv[1]) as config_file:
    cfg = json.load(config_file)
username = sys.argv[2]


def form(html, base):
    f = BeautifulSoup(html, "html.parser").find("form")
    assert f is not None, html[:1500]
    data = {i.get("name"): i.get("value", "") for i in f.find_all("input") if i.get("name")}
    return (f.get("method") or "get").lower(), urljoin(str(base), f.get("action") or str(base)), data


with httpx.Client(follow_redirects=False, timeout=30) as b:
    page = b.get(f"{MOODLE}/login/index.php")
    token = BeautifulSoup(page.text, "html.parser").find("input", {"name": "logintoken"})["value"]
    signed_in = b.post(f"{MOODLE}/login/index.php", data={"username": username, "password": "E2e-user-pass-1!", "logintoken": token})
    assert signed_in.status_code == 303 and "testsession" in signed_in.headers["location"], signed_in.headers

    launch = b.get(f"{MOODLE}/mod/lti/launch.php", params={"id": cfg["cmid"]})
    method, action, data = form(launch.text, launch.url)
    print("1. Moodle initiates login:", method.upper(), action, sorted(data))
    r = b.request(method.upper(), action, **({"data": data} if method == "post" else {"params": data}))
    print("2. tool answers", r.status_code, "->", r.headers.get("location", "")[:80])
    if r.status_code != 302:
        print(r.text[:500]); sys.exit(1)
    auth = b.get(r.headers["location"])
    method, action, data = form(auth.text, auth.url)
    print("3. Moodle posts back:", method.upper(), action, sorted(data))
    r = b.post(action, data=data)
    print("4. tool answers", r.status_code)
    if r.status_code != 303:
        print(BeautifulSoup(r.text, "html.parser").get_text(" ", strip=True)[:300]); sys.exit(2)
    location = urlparse(r.headers["location"])
    print("   ->", f"{location.scheme}://{location.netloc}{location.path}", "fragment keys", sorted(parse_qs(location.fragment)))
    code = parse_qs(location.fragment)["code"][0]
    session = b.post(f"{TOOL}/api/lti/session", json={"code": code})
    print("5. session", session.status_code)
    body = session.json()
    bearer = {"Authorization": f"Bearer {body['access_token']}"}
    courses = b.get(f"{TOOL}/api/courses", headers=bearer).json()
    chat = b.get(f"{TOOL}/api/courses/{body['course_id']}/chat/latest", headers=bearer)
    documents = b.get(f"{TOOL}/api/documents", params={"course_id": body["course_id"]}, headers=bearer)
    print(json.dumps({"user": body["user"], "course_id": body["course_id"], "course_role": body["course_role"],
                      "courses": [{"id": c["id"], "name": c["name"]} for c in courses],
                      "chat_latest": chat.status_code, "documents": documents.status_code}, ensure_ascii=False))
