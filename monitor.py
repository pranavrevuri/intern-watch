"""
Checks a list of companies' career pages for new SWE/tech-adjacent
intern postings and emails you only when something new shows up.

Each company in companies.json has a list of `sources` tried in order:
official ATS APIs (Greenhouse / Lever / SmartRecruiters) first when the
token is known, and a real headless-browser render as the fallback for
the many big career sites (Amazon, Meta, banks, telecoms, ...) that
build their job listings client-side and expose no public API. The
first source that responds without error wins - a wrong/guessed ATS
token fails fast and falls through to the next source.

State lives in state.json (v2 schema): every posting we've ever seen is
remembered with first/last-seen timestamps and an active flag, so a
posting is only emailed the first time it appears, disappeared postings
are kept (marked inactive) rather than deleted, and the old flat v1
state format auto-migrates without re-alerting anything already seen.

Run manually with:  python monitor.py
"""

import json
import os
import re
import sys
import urllib.request
from datetime import datetime, timezone

# playwright is imported lazily inside main() - only the live browser run
# needs it, so the pure matching/dedup/state logic (and its tests) stay
# importable without the dependency installed.

STATE_FILE = "state.json"
CONFIG_FILE = "companies.json"

# Any listing that mentions one of these terms near the word "intern"
# counts as a hit. Intentionally broad and tech-adjacent; note "quant"/
# "quantitative" is deliberately absent - pure finance/IB roles are not
# wanted even at the banks/fintechs in the company list. Edit freely -
# it doesn't need code changes elsewhere, but keep tests/test_monitor.py
# in sync since those codify what should and shouldn't match.
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
    r"\bdeveloper\b",
    r"\bswe\b",
    r"\bsde\b",
]
ROLE_PATTERN = re.compile("|".join(ROLE_KEYWORDS), re.IGNORECASE)
# Word-bounded so "internal", "international", "internet" don't count as
# intern mentions, while intern / interns / internship(s) all do. Without
# the boundary, matching any job description that says "internal tools" or
# "international" produced hundreds of false hits.
INTERN_PATTERN = re.compile(r"\binterns?(?:hips?)?\b", re.IGNORECASE)


def text_matches(text, window=150):
    """True if any 'intern' mention in text has a relevant role
    keyword nearby. Used where we already have a clean per-job
    title/url (Greenhouse, Lever, SmartRecruiters) and just need a
    yes/no answer.

    No year check: most companies don't print a cohort year in
    visible text anywhere near the actual listing (confirmed on
    Apple's page - the date shown is separate page metadata, not part
    of the flowing text), so requiring one silently filtered out
    almost everything. Freshness comes from the seen-vs-new check in
    reconcile() instead."""
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
        # Match on the structured title only - matching the full job
        # description flags every engineering role that merely mentions
        # interns/software somewhere in its body.
        if text_matches(job.get("title", "")):
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
        # Title only (Lever's "text" field is the job title) - see the
        # note in check_greenhouse on why the description is excluded.
        if text_matches(job.get("text", "")):
            hits.append({"title": job.get("text"), "url": job.get("hostedUrl")})
    return hits


def check_smartrecruiters(token):
    """Works for any company on SmartRecruiters. Token = the company
    identifier in their jobs.smartrecruiters.com/<token> URL. The public
    postings API returns titles only, which is enough to match on."""
    url = f"https://api.smartrecruiters.com/v1/companies/{token}/postings?limit=100"
    data = json.loads(fetch(url))
    jobs = data.get("content", [])
    intern_count = sum(1 for j in jobs if re.search(r"intern", j.get("name", ""), re.IGNORECASE))
    print(f"    -> {len(jobs)} total postings, {intern_count} mention 'intern' in the title")
    hits = []
    for job in jobs:
        title = job.get("name", "")
        if text_matches(title):
            job_url = f"https://jobs.smartrecruiters.com/{token}/{job.get('id')}"
            hits.append({"title": title, "url": job_url})
    return hits


def check_browser(browser, url):
    """Default for everything else: load the page in a real headless
    browser (in its own fresh, isolated context) so client-side-rendered
    job listings actually appear, then keyword-match against the
    rendered text.

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


def check_source(source, browser):
    """Dispatch a single source dict to the right checker."""
    stype = source.get("type", "browser")
    if stype == "greenhouse":
        return check_greenhouse(source["token"])
    if stype == "lever":
        return check_lever(source["token"])
    if stype == "smartrecruiters":
        return check_smartrecruiters(source["token"])
    if stype == "static":
        return check_static(source["url"])
    return check_browser(browser, source["url"])


def check_company(company, browser):
    """Try each configured source in order; the first one that responds
    without raising wins (even if it finds zero postings - a working ATS
    API with no current internships is authoritative). Only a source
    that errors - e.g. a wrong ATS token 404ing - falls through to the
    next one."""
    name = company.get("name")
    sources = company.get("sources", [])
    for source in sources:
        label = source.get("type", "browser")
        try:
            return check_source(source, browser)
        except Exception as e:
            print(f"  ! {name} [{label}] source failed, trying next: {e}", file=sys.stderr)
            continue
    return []


def normalize_key(hit):
    """Stable dedup key for a hit: normalized (whitespace-collapsed,
    lowercased) title joined with its url. Cosmetic page re-renders that
    only change spacing or case therefore map to the same key, while a
    genuinely different title (e.g. a different cohort year) does not."""
    title = re.sub(r"\s+", " ", hit.get("title", "") or "").strip().lower()
    url = (hit.get("url", "") or "").strip()
    return f"{url}::{title}"


def load_json(path, default):
    if os.path.exists(path):
        with open(path) as f:
            return json.load(f)
    return default


def save_json(path, data):
    with open(path, "w") as f:
        json.dump(data, f, indent=2)


def load_state():
    """Load state.json as the v2 schema, migrating the old v1 format
    if needed.

    v2:  {"version": 2, "companies": {name: {"entries": {...}, "legacy": [...]}}}
    v1:  {name: ["<url>::<title>", ...]}   (flat list of raw seen-keys)

    On migration the old raw seen-keys become each company's `legacy`
    list. reconcile() checks that list so postings already alerted
    before the migration are not re-alerted, without needing to
    reconstruct their full entry metadata."""
    raw = load_json(STATE_FILE, None)
    if raw is None:
        return {"version": 2, "companies": {}}
    if isinstance(raw, dict) and raw.get("version") == 2:
        raw.setdefault("companies", {})
        return raw
    # v1 flat format -> migrate
    companies = {}
    if isinstance(raw, dict):
        for name, keys in raw.items():
            legacy = list(keys) if isinstance(keys, list) else []
            companies[name] = {"entries": {}, "legacy": legacy}
    return {"version": 2, "companies": companies}


def reconcile(cstate, hits, timestamp):
    """Reconcile one company's freshly-found hits against its remembered
    state. Returns (new_hits, cstate) where new_hits are the ones worth
    alerting on (never seen before, and not suppressed by legacy).

    - A hit whose key is already an entry is refreshed (last_seen,
      active) but not reported again.
    - A brand-new hit is recorded and reported, unless its old-format
      key is in `legacy` (already alerted pre-migration) - then it's
      recorded silently.
    - Entries not present in this run are marked inactive, not deleted,
      so history is preserved."""
    entries = cstate.get("entries", {})
    legacy = set(cstate.get("legacy", []))
    seen_this_run = set()
    new_hits = []

    for hit in hits:
        key = normalize_key(hit)
        seen_this_run.add(key)
        if key in entries:
            entries[key]["last_seen"] = timestamp
            entries[key]["active"] = True
            continue
        entries[key] = {
            "title": hit.get("title", ""),
            "url": hit.get("url", ""),
            "first_seen": timestamp,
            "last_seen": timestamp,
            "active": True,
        }
        legacy_key = f"{hit.get('url', '')}::{hit.get('title', '')}"
        if legacy_key not in legacy:
            new_hits.append(hit)

    for key, entry in entries.items():
        if key not in seen_this_run:
            entry["active"] = False

    cstate["entries"] = entries
    cstate["legacy"] = list(legacy)
    return new_hits, cstate


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


def has_2027(hit):
    """Soft priority signal only - postings that mention 2027 are sorted
    first and flagged in the email. Not a filter: most companies don't
    put a cohort year in visible text at all."""
    return "2027" in f"{hit.get('title', '')} {hit.get('url', '')}"


def main():
    companies = load_json(CONFIG_FILE, [])
    if not companies:
        print(f"No companies found in {CONFIG_FILE}. Add some and re-run.")
        return

    state = load_state()
    state.setdefault("companies", {})
    now = datetime.now(timezone.utc).isoformat()
    new_hits = []

    from playwright.sync_api import sync_playwright

    with sync_playwright() as p:
        browser = p.chromium.launch(
            args=["--disable-http2", "--disable-blink-features=AutomationControlled"]
        )

        for company in companies:
            name = company["name"]
            print(f"Checking {name}...")
            hits = check_company(company, browser)
            cstate = state["companies"].setdefault(name, {"entries": {}, "legacy": []})
            fresh, cstate = reconcile(cstate, hits, now)
            state["companies"][name] = cstate
            for hit in fresh:
                new_hits.append((name, hit))

        browser.close()

    save_json(STATE_FILE, state)

    if new_hits:
        # 2027-tagged hits first, as a soft priority signal.
        new_hits.sort(key=lambda nh: (0 if has_2027(nh[1]) else 1))
        lines = []
        for name, hit in new_hits:
            tag = "[2027] " if has_2027(hit) else ""
            lines.append(f"- {tag}{name}: {hit['title']} ({hit['url']})")
        body = "New intern postings found:\n\n" + "\n".join(lines)
        print(body)
        if os.environ.get("SMTP_HOST"):
            send_email("New Intern Postings", body)
    else:
        print("No new postings found.")


if __name__ == "__main__":
    main()
