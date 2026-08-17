"""
Polls the community-maintained SimplifyJobs/Summer2027-Internships
listings and emails the moment a new tech intern posting appears.

This is the fast tripwire in the three-layer setup:
  - extern_watch.py  = the calendar (when windows are EXPECTED to open;
                       Extern's projections update ~monthly)
  - simplify_watch.py = crowd-sourced live postings, typically hours
                       after they go up, hundreds of companies, one
                       HTTP request per run
  - monitor.py       = direct career-page checks for the shortlist

The data is a public JSON file the Simplify community maintains; no
scraping, no LLM calls, and a run costs one HTTP fetch, so it can go
hourly for ~1 Actions minute per run.

Filtering: Summer 2027 term, active+visible, tech categories only
(TECH_CATEGORIES - edit freely), US locations, and postings that
require U.S. citizenship are dropped (CPT student - see README).
"Does Not Offer Sponsorship" is kept but annotated: sponsorship isn't
needed for a CPT internship, only for staying after graduation.

State lives in simplify_state.json: every listing id ever seen, so a
posting is emailed exactly once, on first sight. First run baselines
everything already live without emailing.

Run manually with:  python simplify_watch.py
"""

import json
import os
import sys
import time
import urllib.request
from datetime import datetime, timezone

from monitor import is_non_us_location, push_notify, send_email, title_ok

LISTINGS_URL = ("https://raw.githubusercontent.com/SimplifyJobs/"
                "Summer2027-Internships/dev/.github/scripts/listings.json")
STATE_FILE = "simplify_state.json"
SEASON = "Summer 2027"

# Simplify's category labels (they use a couple of spellings). Product,
# Quant, and Hardware exist too - add them here if wanted. Quant is
# left out to match monitor.py's "no pure-finance roles" stance;
# SWE roles at quant firms are categorized Software anyway.
TECH_CATEGORIES = {
    "Software", "Software Engineering",
    "AI/ML/Data", "Data Science, AI & Machine Learning",
}

CITIZENSHIP_REQUIRED = "U.S. Citizenship is Required"
NO_SPONSORSHIP = "Does Not Offer Sponsorship"

# Simplify's sponsorship flag is community-set and often missing, so
# known U.S.-person/ITAR shops (defense and space) are skipped by name
# too - not workable on CPT (see the Extern calendar's visa column).
COMPANY_SKIP = {
    "anduril", "bae systems", "blue origin", "booz allen hamilton",
    "general dynamics", "l3harris", "lockheed martin", "northrop grumman",
    "raytheon", "rtx", "pratt & whitney", "spacex", "rocket lab",
    "sierra space", "relativity space", "leidos", "mercury systems",
    "collins aerospace",
}


def fetch_listings(url=LISTINGS_URL, attempts=3):
    """The listings file is ~10MB from raw.githubusercontent - at a
    30-minute cadence, transient 429s/timeouts are routine, so retry
    with backoff rather than failing the run."""
    last = None
    for i in range(attempts):
        try:
            req = urllib.request.Request(url, headers={"User-Agent": "intern-watch"})
            with urllib.request.urlopen(req, timeout=120) as resp:
                return json.load(resp)
        except Exception as exc:  # noqa: BLE001 - retried, re-raised below
            last = exc
            print(f"fetch attempt {i + 1} failed: {exc}", file=sys.stderr)
            time.sleep(5 * (i + 1))
    raise last


def relevant(listing):
    """Is this a live, US, software/AI, Summer 2027 posting the user
    can actually take (no citizenship requirement)?

    Category alone is NOT enough: Simplify's AI/ML/Data bucket lumps in
    analytics, data-science, and even geoscience roles, so the title
    must independently read as a software/AI role (monitor.title_ok:
    tech keyword required, consulting/analytics/data-science vetoed)."""
    if not listing.get("active") or not listing.get("is_visible"):
        return False
    if SEASON not in (listing.get("terms") or []):
        return False
    if listing.get("category") not in TECH_CATEGORIES:
        return False
    if not title_ok(listing.get("title") or ""):
        return False
    if listing.get("sponsorship") == CITIZENSHIP_REQUIRED:
        return False
    if (listing.get("company_name") or "").strip().lower() in COMPANY_SKIP:
        return False
    locations = [l for l in (listing.get("locations") or []) if l]
    if locations and all(is_non_us_location(l) for l in locations):
        return False
    return True


def new_listings(listings, seen_ids):
    return [l for l in listings if relevant(l) and l.get("id") and l["id"] not in seen_ids]


def format_email(fresh):
    lines = []
    for l in sorted(fresh, key=lambda x: (x.get("company_name") or "", x.get("title") or "")):
        posted = datetime.fromtimestamp(l.get("date_posted") or 0, tz=timezone.utc)
        lines.append(f"{l.get('company_name')} - {l.get('title')}")
        locs = ", ".join(l.get("locations") or [])
        if locs:
            lines.append(f"    {locs}")
        if l.get("sponsorship") == NO_SPONSORSHIP:
            lines.append("    note: no visa sponsorship - fine for the CPT internship itself,"
                         " matters for post-grad conversion")
        lines.append(f"    posted {posted.strftime('%Y-%m-%d %H:%M UTC')}")
        lines.append(f"    apply: {l.get('url')}")
        lines.append("")
    lines.append("Source: github.com/SimplifyJobs/Summer2027-Internships (community-reported)")
    return "\n".join(lines)


def load_json(path, default):
    if not os.path.exists(path):
        return default
    with open(path) as fh:
        return json.load(fh)


def main():
    state = load_json(STATE_FILE, {})
    seen = set(state.get("seen_ids", []))
    first_run = not state

    try:
        listings = fetch_listings()
    except Exception as exc:  # noqa: BLE001
        # Transient network failure: skip this cycle cleanly (state
        # untouched, next run in 30 min catches up) instead of failing
        # the workflow and emailing a red X for nothing.
        print(f"Skipping cycle - listings fetch failed after retries: {exc}")
        return
    fresh = new_listings(listings, seen)

    # Every relevant id gets remembered - including ones seen while
    # inactive filters were different - so edits to TECH_CATEGORIES
    # never replay months of old postings.
    all_relevant_ids = [l["id"] for l in listings if l.get("id") and relevant(l)]
    seen.update(all_relevant_ids)
    state = {
        "checked_at": datetime.now(timezone.utc).isoformat(),
        "seen_ids": sorted(seen),
    }
    with open(STATE_FILE, "w") as fh:
        json.dump(state, fh, indent=0)

    if first_run:
        print(f"First run - baselined {len(all_relevant_ids)} live postings, no email.")
        return
    if not fresh:
        print("No new postings.")
        return

    body = format_email(fresh)
    print(f"{len(fresh)} new posting(s):\n{body}")
    companies = sorted({l.get("company_name") or "?" for l in fresh})
    preview = ", ".join(companies[:4]) + ("..." if len(companies) > 4 else "")
    if os.environ.get("SMTP_HOST"):
        send_email(f"[intern-watch] {len(fresh)} new Summer 2027 posting(s): {preview}", body)
        print("Alert email sent.")
    push_notify(f"New Summer 2027 posting(s): {preview}", body)


if __name__ == "__main__":
    sys.exit(main())
