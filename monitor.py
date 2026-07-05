"""
Checks a list of companies' career pages for new 2027 SWE intern postings
and emails you only when something new shows up.

Most big-company career sites (Amazon, Meta, Palantir, etc.) only load
their actual job listings via JavaScript after the page loads, so this
uses a real headless browser (Playwright/Chromium) to render pages
before searching them - a plain HTTP fetch would see an empty shell on
these sites. Greenhouse/Lever companies skip the browser and hit their
public JSON APIs directly, since that's faster and more reliable when
it's available.

Run manually with:  python monitor.py
"""

import json
import os
import re
import sys
import urllib.request

from playwright.sync_api import sync_playwright

STATE_FILE = "state.json"
CONFIG_FILE = "companies.json"

# Matches common phrasings of the role
KEYWORD_PATTERN = re.compile(
    r"(software\s*engineer(?:ing)?\s*intern|swe\s*intern|"
    r"software\s*development\s*intern|sde\s*intern)",
    re.IGNORECASE,
)
# Matches "2027" and also things like "Summer 2027"
YEAR_PATTERN = re.compile(r"2027")


def fetch(url, headers=None):
    req = urllib.request.Request(url, headers=headers or {"User-Agent": "Mozilla/5.0"})
    with urllib.request.urlopen(req, timeout=20) as resp:
        return resp.read().decode("utf-8", errors="ignore")


def check_greenhouse(token):
    """Works for any company on Greenhouse. Token = the part of their
    boards.greenhouse.io/<token> URL."""
    url = f"https://boards-api.greenhouse.io/v1/boards/{token}/jobs?content=true"
    data = json.loads(fetch(url))
    hits = []
    for job in data.get("jobs", []):
        text = f"{job.get('title', '')} {job.get('content', '')}"
        if KEYWORD_PATTERN.search(text) and YEAR_PATTERN.search(text):
            hits.append({"title": job.get("title"), "url": job.get("absolute_url")})
    return hits


def check_lever(token):
    """Works for any company on Lever. Token = the part of their
    jobs.lever.co/<token> URL."""
    url = f"https://api.lever.co/v0/postings/{token}?mode=json"
    data = json.loads(fetch(url))
    hits = []
    for job in data:
        text = f"{job.get('text', '')} {job.get('descriptionPlain', '')}"
        if KEYWORD_PATTERN.search(text) and YEAR_PATTERN.search(text):
            hits.append({"title": job.get("text"), "url": job.get("hostedUrl")})
    return hits


def check_browser(browser, url):
    """Default for everything else: load the page in a real headless
    browser (in its own fresh, isolated tab) so client-side-rendered job
    listings actually appear, then keyword-match against the rendered
    text.

    Each company gets a brand new browser context rather than reusing
    one tab across companies - some career sites trigger background
    redirects that were colliding with the *next* company's page load
    when a tab was shared. A fresh context also means one company's
    cookies/session can't affect another's."""
    context = browser.new_context(
        user_agent=(
            "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
            "(KHTML, like Gecko) Chrome/124.0 Safari/537.36"
        )
    )
    page = context.new_page()
    try:
        try:
            # domcontentloaded = "the page is there", not "network is
            # completely silent" - some sites never go fully idle
            # because of analytics/chat-widget pings, so networkidle
            # would time out on them even though the page loaded fine.
            page.goto(url, wait_until="domcontentloaded", timeout=30000)
        except Exception:
            # one retry with an even looser condition before giving up
            page.goto(url, wait_until="load", timeout=30000)
        page.wait_for_timeout(4000)  # let client-side rendering settle
        text = page.inner_text("body")
    finally:
        context.close()

    hits = []
    for m in KEYWORD_PATTERN.finditer(text):
        window = text[max(0, m.start() - 150): m.end() + 150]
        if YEAR_PATTERN.search(window):
            snippet = re.sub(r"\s+", " ", window).strip()
            hits.append({"title": snippet[:160], "url": url})
    return hits


def check_company(company, browser):
    ats = company.get("ats", "browser")
    try:
        if ats == "greenhouse":
            return check_greenhouse(company["token"])
        if ats == "lever":
            return check_lever(company["token"])
        return check_browser(browser, company["url"])
    except Exception as e:
        print(f"  ! error checking {company.get('name')}: {e}", file=sys.stderr)
        return []


def load_json(path, default):
    if os.path.exists(path):
        with open(path) as f:
            return json.load(f)
    return default


def save_json(path, data):
    with open(path, "w") as f:
        json.dump(data, f, indent=2)


def send_email(subject, body):
    import smtplib
    from email.mime.text import MIMEText

    host = os.environ["SMTP_HOST"]
    port = int(os.environ.get("SMTP_PORT", "587"))
    user = os.environ["SMTP_USER"]
    pw = os.environ["SMTP_PASS"]
    to = os.environ["NOTIFY_EMAIL"]

    msg = MIMEText(body)
    msg["Subject"] = subject
    msg["From"] = user
    msg["To"] = to

    with smtplib.SMTP(host, port) as server:
        server.starttls()
        server.login(user, pw)
        server.send_message(msg)


def main():
    companies = load_json(CONFIG_FILE, [])
    if not companies:
        print(f"No companies found in {CONFIG_FILE}. Add some and re-run.")
        return

    state = load_json(STATE_FILE, {})
    new_hits = []

    with sync_playwright() as p:
        browser = p.chromium.launch(args=["--disable-http2"])

        for company in companies:
            name = company["name"]
            print(f"Checking {name}...")
            hits = check_company(company, browser)
            seen = set(state.get(name, []))
            for hit in hits:
                key = hit["url"] or hit["title"]
                if key not in seen:
                    new_hits.append((name, hit))
                    seen.add(key)
            state[name] = list(seen)

        browser.close()

    save_json(STATE_FILE, state)

    if new_hits:
        lines = [f"- {name}: {hit['title']} ({hit['url']})" for name, hit in new_hits]
        body = "New 2027 SWE intern postings found:\n\n" + "\n".join(lines)
        print(body)
        if os.environ.get("SMTP_HOST"):
            send_email("New 2027 SWE Intern Postings", body)
    else:
        print("No new postings found.")


if __name__ == "__main__":
    main()
