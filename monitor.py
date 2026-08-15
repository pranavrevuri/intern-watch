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

Only US roles are wanted: any posting whose visible location names a
non-US country/city is dropped, while postings that show no location at
all are kept (many US roles never print one). See
NON_US_LOCATION_KEYWORDS for the details.

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
    r"\bcomputer\s+science\b",
    r"\bengineering\b",
    r"\bdeveloper\b",
    r"\bswe\b",
    r"\bsde\b",
]
ROLE_PATTERN = re.compile("|".join(ROLE_KEYWORDS), re.IGNORECASE)

# Role flavors not wanted even when a tech keyword is present:
# consulting, analytics/data-science, and field-science tracks. Note
# "analyst" alone is NOT vetoed - banks title their real SWE programs
# "Technology Analyst" - only data/analytics-flavored analyst roles are.
TITLE_VETO_PATTERN = re.compile(
    r"consult|geoscien|analytics|data\s+scien|business\s+intelligence|"
    r"\bdata\b.{0,40}\banalyst\b|\banalyst\b.{0,40}\bdata\b",
    re.IGNORECASE,
)
# Word-bounded so "internal", "international", "internet" don't count as
# intern mentions, while intern / interns / internship(s) all do. Without
# the boundary, matching any job description that says "internal tools" or
# "international" produced hundreds of false hits. Banks title their
# internships "Summer Analyst" with no "intern" anywhere (Goldman,
# Morgan Stanley, Citi), so that counts too. "New Analyst"/"Summer
# Associate" stay excluded - full-time new-grad and MBA programs.
INTERN_PATTERN = re.compile(r"\binterns?(?:hips?)?\b|\bsummer\s+analyst\b", re.IGNORECASE)

# Fall/spring co-op and off-cycle postings are kept (they're an early
# signal the company's summer req is coming) but tagged and sorted last
# in the email - the user can only do summer full-time.
OFFCYCLE_PATTERN = re.compile(r"\bfall\b|\bspring\b|\bco-?op\b|off.?cycle|\bwinter\b", re.IGNORECASE)

# Only US roles are wanted. Postings that show a location clearly outside
# the US (a foreign country or well-known foreign tech-hub city) are
# dropped; postings that show *no* location are kept, since many US-based
# roles simply don't print one. So this is a blocklist of non-US places,
# not a US allowlist - an unrecognized location errs on the side of
# keeping the posting. Ambiguous names shared with US places (Georgia,
# Jersey, Ontario CA, Cambridge MA, ...) are deliberately left out.
NON_US_LOCATION_KEYWORDS = [
    # East / Southeast / South Asia
    r"\btaiwan\b", r"\btaipei\b", r"\bhsinchu\b",
    r"\bchina\b", r"\bbeijing\b", r"\bshanghai\b", r"\bshenzhen\b", r"\bhangzhou\b",
    r"\bhong\s+kong\b",
    r"\bjapan\b", r"\btokyo\b", r"\bosaka\b",
    r"\bkorea\b", r"\bseoul\b",
    r"\bsingapore\b",
    r"\bindia\b", r"\bbangalore\b", r"\bbengaluru\b", r"\bhyderabad\b",
    r"\bchennai\b", r"\bmumbai\b", r"\bpune\b", r"\bnoida\b",
    r"\bgurgaon\b", r"\bgurugram\b", r"\bnew\s+delhi\b",
    r"\bvietnam\b", r"\bhanoi\b", r"\bho\s+chi\s+minh\b",
    r"\bthailand\b", r"\bbangkok\b",
    r"\bmalaysia\b", r"\bkuala\s+lumpur\b",
    r"\bindonesia\b", r"\bjakarta\b",
    r"\bphilippines\b", r"\bmanila\b",
    # Oceania
    r"\baustralia\b", r"\bsydney\b", r"\bmelbourne\b", r"\bbrisbane\b",
    r"\bnew\s+zealand\b", r"\bauckland\b",
    # Canada
    r"\bcanada\b", r"\btoronto\b", r"\bvancouver\b", r"\bmontreal\b",
    r"\bottawa\b", r"\bcalgary\b",
    # Europe
    r"\bunited\s+kingdom\b", r"\buk\b", r"\bengland\b", r"\bscotland\b",
    r"\blondon\b", r"\bireland\b", r"\bdublin\b",
    r"\bfrance\b", r"\bparis\b",
    r"\bgermany\b", r"\bberlin\b", r"\bmunich\b",
    r"\bnetherlands\b", r"\bamsterdam\b",
    r"\bbelgium\b", r"\bluxembourg\b",
    r"\bswitzerland\b", r"\bzurich\b",
    r"\baustria\b", r"\bvienna\b",
    r"\bspain\b", r"\bmadrid\b", r"\bbarcelona\b",
    r"\bportugal\b", r"\blisbon\b",
    r"\bitaly\b", r"\bmilan\b",
    r"\bpoland\b", r"\bwarsaw\b", r"\bkrakow\b", r"\bkraków\b",
    r"\bczech(?:ia)?\b", r"\bprague\b",
    r"\bslovakia\b", r"\bhungary\b", r"\bbudapest\b",
    r"\bromania\b", r"\bbucharest\b", r"\bbulgaria\b", r"\bgreece\b",
    r"\bdenmark\b", r"\bcopenhagen\b", r"\bsweden\b", r"\bstockholm\b",
    r"\bnorway\b", r"\boslo\b", r"\bfinland\b", r"\bhelsinki\b",
    r"\bestonia\b", r"\blatvia\b", r"\blithuania\b",
    r"\bukraine\b", r"\bserbia\b", r"\bcroatia\b",
    # Middle East / Africa
    r"\bisrael\b", r"\btel\s+aviv\b",
    r"\bturkey\b", r"\bunited\s+arab\s+emirates\b", r"\buae\b",
    r"\bdubai\b", r"\babu\s+dhabi\b", r"\bsaudi\s+arabia\b", r"\bqatar\b",
    r"\begypt\b", r"\bcairo\b", r"\bsouth\s+africa\b",
    r"\bnigeria\b", r"\blagos\b", r"\bkenya\b", r"\bnairobi\b",
    # Latin America ("New Mexico" must not count as Mexico)
    r"(?<!new\s)\bmexico\b", r"\bguadalajara\b", r"\bmonterrey\b",
    r"\bbrazil\b", r"\bsao\s+paulo\b", r"\bsão\s+paulo\b",
    r"\bargentina\b", r"\bbuenos\s+aires\b",
    r"\bchile\b", r"\bsantiago\b",
    r"\bcolombia\b", r"\bbogotá\b", r"\bbogota\b",
    r"\bperu\b", r"\bcosta\s+rica\b",
]
NON_US_LOCATION_PATTERN = re.compile(
    "|".join(NON_US_LOCATION_KEYWORDS), re.IGNORECASE
)


def is_non_us_location(text):
    """True only when text explicitly names a non-US place. Empty or
    unrecognized location text returns False (kept), per the blocklist
    approach described above NON_US_LOCATION_KEYWORDS."""
    return bool(text) and bool(NON_US_LOCATION_PATTERN.search(text))


def title_ok(title):
    """Filter used wherever a real per-job title exists (ATS APIs, link
    mode): the title itself must read as a software/AI intern role and
    not be a vetoed flavor. Window-mode snippets don't get the veto -
    they blend neighboring page text, which would veto good postings."""
    return text_matches(title) and not TITLE_VETO_PATTERN.search(title)


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


# Rendered career pages mix job listings with page furniture -
# pagination controls, sort menus, filter facets like "Engineering (27)
# ... Interns (0)" - which the window-based matcher was turning into
# "postings". Any snippet that looks like that chrome is noise.
UI_CHROME_PATTERN = re.compile(
    r"items per page|per page:|sort by|newest to oldest|oldest to newest|"
    r"most relevant|title a-z|cookie|\(\d+\)[^()]{0,40}\(\d+\)",
    re.IGNORECASE,
)


def anchor_hits(anchors, page_url):
    """Job listings almost always render as links, so when the page has
    intern-mentioning links, match each link's own text - a real job
    title - instead of fuzzy text windows. Windows turned page chrome
    into "titles" and let keywords from adjacent postings contaminate
    each other (a Strategy Consultant posting matching because a tech
    posting sat within 150 chars of it).

    Returns None when the page has no intern-mentioning links at all -
    some sites render titles as plain divs - meaning the caller should
    fall back to window matching. An empty list is authoritative: the
    page lists intern links and none are relevant."""
    candidates = [
        a for a in anchors
        if a.get("text") and len(a["text"]) < 250 and INTERN_PATTERN.search(a["text"])
    ]
    if not candidates:
        return None
    hits, seen = [], set()
    for a in candidates:
        title = re.sub(r"\s+", " ", a["text"]).strip()
        # The link's own text must justify the hit - no borrowing
        # keywords from elsewhere on the page.
        if not title_ok(title):
            continue
        if is_non_us_location(title) or UI_CHROME_PATTERN.search(title):
            continue
        key = title.lower()
        if key in seen:
            continue
        seen.add(key)
        hits.append({"title": title[:160], "url": a.get("href") or page_url})
    return hits


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
        # Window edges land mid-word ("ulting Federal Consulting...") -
        # trim to word boundaries so the emailed snippet reads sanely.
        if start > 0 and " " in snippet:
            snippet = snippet.split(" ", 1)[1]
        if end < len(text) and " " in snippet:
            snippet = snippet.rsplit(" ", 1)[0]
        # Unstructured pages print the location inline next to the title,
        # so it lands inside this same window - if that shows a non-US
        # place, drop the hit. Snippets with no location text pass.
        if is_non_us_location(snippet) or UI_CHROME_PATTERN.search(snippet):
            continue
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
        if not title_ok(job.get("title", "")):
            continue
        location = (job.get("location") or {}).get("name", "") or ""
        if is_non_us_location(f"{job.get('title', '')} {location}"):
            continue
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
        if not title_ok(job.get("text", "")):
            continue
        # Lever exposes both an ISO country code and a location label;
        # a present-but-foreign country code is authoritative, and the
        # label falls back to the keyword blocklist. Missing both = keep.
        country = (job.get("country") or "").strip()
        if country and country.upper() not in ("US", "USA"):
            continue
        location = (job.get("categories") or {}).get("location", "") or ""
        if is_non_us_location(f"{job.get('text', '')} {location}"):
            continue
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
        if not title_ok(title):
            continue
        # SmartRecruiters gives a lowercase ISO country code; foreign
        # code = skip, missing code = keep.
        country = ((job.get("location") or {}).get("country") or "").strip()
        if country and country.lower() not in ("us", "usa"):
            continue
        if is_non_us_location(title):
            continue
        job_url = f"https://jobs.smartrecruiters.com/{token}/{job.get('id')}"
        hits.append({"title": title, "url": job_url})
    return hits


def check_ashby(org):
    """Works for any company on Ashby. Org = the part of their
    jobs.ashbyhq.com/<org> URL."""
    url = f"https://api.ashbyhq.com/posting-api/job-board/{org}"
    data = json.loads(fetch(url))
    jobs = data.get("jobs", [])
    intern_count = sum(1 for j in jobs if re.search(r"intern", j.get("title", ""), re.IGNORECASE))
    print(f"    -> {len(jobs)} total postings, {intern_count} mention 'intern' in the title")
    hits = []
    for job in jobs:
        if job.get("isListed") is False:
            continue
        # Title only, same reasoning as check_greenhouse.
        if not title_ok(job.get("title", "")):
            continue
        locations = [job.get("location") or ""]
        locations += [(l or {}).get("location", "") for l in job.get("secondaryLocations") or []]
        loc_text = " ".join(l for l in locations if l)
        # US-only when every listed location is recognizably foreign,
        # mirroring the list-of-locations rule used elsewhere.
        if loc_text and all(is_non_us_location(l) for l in locations if l):
            continue
        hits.append({"title": job.get("title"), "url": job.get("jobUrl") or job.get("applyUrl")})
    return hits


WORKDAY_URL_PATTERN = re.compile(
    r"https://([\w-]+)\.(wd\d+)\.myworkdayjobs\.com/(?:[a-z]{2}-[A-Z]{2}/)?([\w-]+)"
)


def parse_workday_url(url):
    """(tenant, wdN, site) from any myworkdayjobs.com board URL, e.g.
    https://nvidia.wd5.myworkdayjobs.com/NVIDIAExternalCareerSite or
    https://ag.wd3.myworkdayjobs.com/en-US/Airbus?q=intern."""
    m = WORKDAY_URL_PATTERN.search(url)
    if not m:
        raise ValueError(f"not a workday board url: {url}")
    return m.groups()


def check_workday(url):
    """Works for any company on Workday: the public 'cxs' JSON endpoint
    that backs the board page, queried for 'intern'. Paginates because
    big companies routinely have more than one page of intern roles."""
    tenant, wd, site = parse_workday_url(url)
    api = f"https://{tenant}.{wd}.myworkdayjobs.com/wday/cxs/{tenant}/{site}/jobs"
    base = f"https://{tenant}.{wd}.myworkdayjobs.com/{site}"
    hits, offset, total = [], 0, None
    while offset < (100 if total is None else min(total, 100)):
        body = json.dumps({"searchText": "intern", "limit": 20,
                           "offset": offset, "appliedFacets": {}}).encode()
        req = urllib.request.Request(api, data=body, headers={
            "Content-Type": "application/json",
            "Accept": "application/json",
            "User-Agent": "Mozilla/5.0",
        })
        with urllib.request.urlopen(req, timeout=20) as resp:
            data = json.loads(resp.read().decode("utf-8", errors="ignore"))
        postings = data.get("jobPostings", [])
        if total is None:
            total = data.get("total", 0)
            print(f"    -> {total} postings match 'intern' on the board")
        for p in postings:
            title = p.get("title") or ""
            if not title_ok(title):
                continue
            if is_non_us_location(f"{title} {p.get('locationsText') or ''}"):
                continue
            hits.append({"title": title, "url": base + (p.get("externalPath") or "")})
        if not postings:
            break
        offset += len(postings)
    return hits


def check_browser(browser, url, slow=False):
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
    # No user-agent override: with the real-Chrome channel the browser's
    # own UA is correct, and a spoofed version that mismatches the
    # binary's TLS/JS fingerprint is exactly what bot walls look for
    # (Goldman stalled on it).
    context = browser.new_context()
    context.add_init_script(
        "Object.defineProperty(navigator, 'webdriver', {get: () => undefined})"
    )
    page = context.new_page()
    try:
        loaded = False
        last_error = None
        # Timeouts sized so a full ~180-company run fits GitHub's free
        # minutes: a career page that can't paint in 15s rarely succeeds
        # at 30. Exception: sites whose bot-check interstitial needs
        # patience (Goldman's Akamai takes ~10s+) are marked slow: true
        # in companies.json and get one generous attempt.
        attempts = ([("domcontentloaded", 45000)] if slow else
                    [("domcontentloaded", 15000), ("load", 10000), ("commit", 8000)])
        for wait_until, timeout in attempts:
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

        page.wait_for_timeout(6000 if slow else 3000)  # let rendering settle
        text = page.inner_text("body")
        anchors = page.eval_on_selector_all(
            "a[href]",
            "els => els.map(el => ({text: (el.innerText || '').trim(), href: el.href}))",
        )
    finally:
        context.close()

    intern_count = len(INTERN_PATTERN.findall(text))
    print(f"    -> {len(text)} chars of page text extracted, 'intern' appears {intern_count}x")

    hits = anchor_hits(anchors, url)
    if hits is not None:
        print(f"    -> link mode: {len(hits)} matching job link(s)")
        return hits
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
    if stype == "ashby":
        return check_ashby(source["token"])
    if stype == "workday":
        return check_workday(source["url"])
    if stype == "static":
        return check_static(source["url"])
    return check_browser(browser, source["url"], slow=bool(source.get("slow")))


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


def push_notify(title, body):
    """Optional instant phone push via ntfy.sh: set the NTFY_TOPIC
    secret to any hard-to-guess string and subscribe to that topic in
    the ntfy app. No-op when unset. Email is the paper trail; this is
    the 'grab your phone now' channel - the difference between reading
    an alert at lunch and applying within the hour."""
    topic = os.environ.get("NTFY_TOPIC")
    if not topic:
        return
    req = urllib.request.Request(
        f"https://ntfy.sh/{topic}",
        data=body.encode("utf-8")[:4000],
        headers={"Title": title[:200], "Priority": "high", "Tags": "rotating_light"},
    )
    try:
        urllib.request.urlopen(req, timeout=10).read()
        print("Phone push sent.")
    except Exception as exc:  # noqa: BLE001 - push failure must not kill the run
        print(f"ntfy push failed: {exc}", file=sys.stderr)


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

    # Fast lane (MONITOR_FAST=1): HTTP-only sources, no browser, done in
    # a couple of minutes - scheduled between full sweeps so ATS-hosted
    # postings alert within hours of going live, not the next morning.
    fast = os.environ.get("MONITOR_FAST") == "1"
    if fast:
        companies = [
            {**c, "sources": [s for s in c["sources"] if s.get("type", "browser") != "browser"]}
            for c in companies
        ]
        companies = [c for c in companies if c["sources"]]
        print(f"Fast lane: {len(companies)} companies with HTTP-checkable sources.")

    state = load_state()
    state.setdefault("companies", {})
    now = datetime.now(timezone.utc).isoformat()
    new_hits = []

    def run_checks(browser):
        for company in companies:
            name = company["name"]
            print(f"Checking {name}...")
            hits = check_company(company, browser)
            cstate = state["companies"].setdefault(name, {"entries": {}, "legacy": []})
            fresh, cstate = reconcile(cstate, hits, now)
            state["companies"][name] = cstate
            for hit in fresh:
                new_hits.append((name, hit))

    if fast:
        run_checks(None)
    else:
        from playwright.sync_api import sync_playwright

        with sync_playwright() as p:
            # Real Chrome (channel) has a normal TLS/JS fingerprint, so
            # bot-walled sites (Goldman, banks) that stonewall the
            # bundled chromium-headless-shell actually load. No
            # --disable-http2 here: real Chrome always speaks h2, and
            # an http/1.1-only "Chrome" is a bot tell (Goldman stalled
            # on it). GitHub's runners ship Chrome; the bundled-chromium
            # fallback keeps the old flags that some sites needed.
            try:
                browser = p.chromium.launch(
                    channel="chrome",
                    args=["--disable-blink-features=AutomationControlled"])
            except Exception:
                browser = p.chromium.launch(
                    args=["--disable-http2", "--disable-blink-features=AutomationControlled"])
            run_checks(browser)
            browser.close()

    save_json(STATE_FILE, state)

    if new_hits:
        # 2027-tagged hits first; fall/spring/co-op postings last.
        def is_offcycle(hit):
            return bool(OFFCYCLE_PATTERN.search(hit.get("title", "")))

        new_hits.sort(key=lambda nh: (1 if is_offcycle(nh[1]) else 0,
                                      0 if has_2027(nh[1]) else 1))
        lines = []
        for name, hit in new_hits:
            tag = "[2027] " if has_2027(hit) else ""
            if is_offcycle(hit):
                tag = "[FALL/SPRING - early signal, not summer] " + tag
            lines.append(f"- {tag}{name}: {hit['title']} ({hit['url']})")
        body = "New intern postings found:\n\n" + "\n".join(lines)
        print(body)
        if os.environ.get("SMTP_HOST"):
            send_email("New Intern Postings", body)
        push_notify(f"{len(new_hits)} new intern posting(s)", body)
    else:
        print("No new postings found.")


if __name__ == "__main__":
    main()
