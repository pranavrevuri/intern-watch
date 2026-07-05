"""
Checks a list of companies' career pages for new SWE/tech-adjacent
intern postings and emails you only when something new shows up.

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

# Any listing that mentions one of these terms near the word "intern"
# counts as a hit. Edit this list any time to widen or narrow what
# counts as relevant - it doesn't need code changes elsewhere.
ROLE_KEYWORDS = [
    r"\bsoftware\b",
    r"\bmachine\s+learning\b",
    r"\bartificial\s+intelligence\b",
    r"\bai\b",
    r"\bml\b",
    r"\btech\b",
    r"\btechnology\b",
    r"\bdata\s+science\b",
    r"\bcomputer\s+science\b",
    r"\bengineering\b",
]
ROLE_PATTERN = re.compile("|".join(ROLE_KEYWORDS), re.IGNORECASE)
INTERN_PATTERN = re.compile(r"intern", re.IGNORECASE)


def text_matches(text, window=150):
    """True if any 'intern' mention in text has a relevant role
    keyword nearby. Used where we already have a clean per-job
    title/url (Greenhouse, Lever) and just need a yes/no answer.

    No year check: most companies don't print a cohort year in
    visible text anywhere near the actual listing (confirmed on
    Apple's page - the date shown is separate page metadata, not part
    of the flowing text), so requiring one silently filtered out
    almost everything except companies that happen to write years
    into prose. Freshness comes from the new-vs-seen check in main()
    instead - only postings not seen on a previous run get flagged."""
    for m in INTERN_PATTERN.finditer(text):
        window_text = text[max(0, m.start() - window): m.end() + window]
        if ROLE_PATTERN.search(window_text):
            return True
    return False


def find_hits(text, url, window=150):
    """For pages without per-job structure (a flat page of rendered
    text or HTML): return one hit per distinct matching posting, using
    a text snippet as the title since there's no structured job title
    to point to.

    A page often has several close-together mentions of "intern" that
    all belong to the same one or two actual postings (e.g. one in the
    title, one in surrounding boilerplate). Matching on every mention
    independently produced multiple near-duplicate hits for a single
    real posting, so overlapping windows get merged into one hit
    before snippets are generated."""
    ranges = []
    for m in INTERN_PATTERN.finditer(text):
        start, end = max(0, m.start() - window), m.end() + window
        window_text = text[start:end]
        if ROLE_PATTERN.search(window_text):
            ranges.append([start, end])

    if not ranges:
        return []

    ranges.sort()
    merged = [ranges[0]]
    for start, end in ranges[1:]:
        if start <= merged[-1][1]:  # overlaps the previous range - same posting
            merged[-1][1] = max(merged[-1][1], end)
        else:
            merged.append([start, end])

    hits = []
    for start, end in merged:
        snippet = re.sub(r"<[^>]+>", " ", text[start:end])  # strip any leftover HTML tags
        snippet = re.sub(r"\s+", " ", snippet).strip()
        hits.append({"title": snippet[:160], "url": url})
    return hits


def fetch(url, headers=None):
    req = urllib.request.Request(url, headers=headers or {"User-Agent": "Mozilla/5.0"})
    with urllib.request.urlopen(req, timeout=20) as resp:
        return resp.read().decode("utf-8", errors="ignore")


def check_greenhouse(token):
    """Works for any company on Greenhouse. Token = the part of their
    boards.greenhouse.io/<token> URL."""
    url = f"https://boards-api.greenhouse.io/v1/boards/{token}/jobs?content=true"
    data = json.loads(fetch(url))
    jobs = data.get("jobs", [])
    intern_count = sum(1 for j in jobs if re.search(r"intern", j.get("title", ""), re.IGNORECASE))
    print(f"    -> {len(jobs)} total postings, {intern_count} mention 'intern' in the title")
    hits = []
    for job in jobs:
        text = f"{job.get('title', '')} {job.get('content', '')}"
        if text_matches(text):
            hits.append({"title": job.get("title"), "url": job.get("absolute_url")})
    return hits


def check_lever(token):
    """Works for any company on Lever. Token = the part of their
    jobs.lever.co/<token> URL."""
    url = f"https://api.lever.co/v0/postings/{token}?mode=json"
    data = json.loads(fetch(url))
    intern_count = sum(1 for j in data if re.search(r"intern", j.get("text", ""), re.IGNORECASE))
    print(f"    -> {len(data)} total postings, {intern_count} mention 'intern' in the title")
    hits = []
    for job in data:
        text = f"{job.get('text', '')} {job.get('descriptionPlain', '')}"
        if text_matches(text):
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
    cookies/session can't affect another's.

    Some sites (seen on Nvidia, Roblox) run bot-detection that stalls
    or blocks obviously-automated browsers, so this also patches the
    most common automation tell (navigator.webdriver) and tries three
    increasingly lenient load conditions before giving up."""
    context = browser.new_context(
        user_agent=(
            "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
            "(KHTML, like Gecko) Chrome/124.0 Safari/537.36"
        )
    )
    context.add_init_script(
        "Object.defineProperty(navigator, 'webdriver', {get: () => undefined})"
    )
    page = context.new_page()
    try:
        loaded = False
        last_error = None
        for wait_until, timeout in [
            ("domcontentloaded", 30000),
            ("load", 30000),
            ("commit", 20000),  # last resort: just wait for a response to start
        ]:
            try:
                page.goto(url, wait_until=wait_until, timeout=timeout)
                loaded = True
                break
            except Exception as e:
                last_error = e
                continue
        if not loaded:
            raise last_error

        # Google and Salesforce came back nearly empty (~250 chars) -
        # almost certainly a cookie-consent overlay sitting in front of
        # the real content. Best-effort: try clicking anything that
        # looks like an "accept" button before giving up on it.
        for accept_text in ["Accept all", "Accept All", "Accept", "I agree", "I Agree", "Got it", "Allow all"]:
            try:
                page.get_by_role("button", name=accept_text, exact=False).click(timeout=2000)
                page.wait_for_timeout(1000)
                break
            except Exception:
                continue

        page.wait_for_timeout(5000)  # let client-side rendering settle
        text = page.inner_text("body")
    finally:
        context.close()

    intern_count = len(INTERN_PATTERN.findall(text))
    print(f"    -> {len(text)} chars of page text extracted, 'intern' appears {intern_count}x")

    return find_hits(text, url)


def check_static(url):
    """For pages that are fully rendered server-side already (no JS
    needed) - just a plain HTTP request, no browser. Useful both
    because it's cheaper/faster, and because some sites specifically
    block headless-browser traffic (bot detection) while letting
    normal HTTP requests through untouched."""
    html = fetch(url)
    intern_count = len(INTERN_PATTERN.findall(html))
    print(f"    -> {len(html)} chars of page HTML fetched, 'intern' appears {intern_count}x")

    return find_hits(html, url, window=200)


def check_company(company, browser):
    ats = company.get("ats", "browser")
    try:
        if ats == "greenhouse":
            return check_greenhouse(company["token"])
        if ats == "lever":
            return check_lever(company["token"])
        if ats == "static":
            return check_static(company["url"])
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
        browser = p.chromium.launch(
            args=["--disable-http2", "--disable-blink-features=AutomationControlled"]
        )

        for company in companies:
            name = company["name"]
            print(f"Checking {name}...")
            hits = check_company(company, browser)
            seen = set(state.get(name, []))
            for hit in hits:
                # Combine url+title rather than url alone: for
                # greenhouse/lever each hit has its own real per-job
                # url, but for browser/static hits every match on a
                # page shares the same page url - using url alone would
                # treat every posting after the first as "already
                # seen" forever, even genuinely new ones.
                key = f"{hit.get('url', '')}::{hit.get('title', '')}"
                if key not in seen:
                    new_hits.append((name, hit))
                    seen.add(key)
            state[name] = list(seen)

        browser.close()

    save_json(STATE_FILE, state)

    if new_hits:
        lines = [f"- {name}: {hit['title']} ({hit['url']})" for name, hit in new_hits]
        body = "New intern postings found:\n\n" + "\n".join(lines)
        print(body)
        if os.environ.get("SMTP_HOST"):
            send_email("New Intern Postings", body)
    else:
        print("No new postings found.")


if __name__ == "__main__":
    main()
