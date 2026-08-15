"""
Watches underclassman (freshman/sophomore) opportunity sources and
emails/pushes when something new appears. These programs matter to a
student who can present as Class of 2029: STEP/Explore/Ignite-style
programs have tiny windows (NVIDIA Ignite: 13 days) and community
lists catch one-off underclassman internships the main watchers skip
because they're gated to juniors elsewhere.

Sources (all public, one HTTP fetch each):
  1. github.com/Jose-Gael-Cruz-Lopez/underclassmen-opportunities -
     community README, sections for internships / programs / research.
  2. github.com/sndsh404/summer-2027-internships - a second community
     posting list plus "programs open now" pipelines.
  3. extern.com's 2027 underclassmen CS internships guide - the
     verified program directory with application windows.

State lives in underclass_state.json: seen row keys (alert once) plus
the current parsed rows, which extern_watch.build_xlsx renders as the
"Underclassman programs" sheet in InternWatch.xlsx.

Run manually with:  python underclass_watch.py
"""

import json
import os
import re
import sys
from datetime import datetime, timezone

from monitor import fetch, is_non_us_location, push_notify, send_email, title_ok
from extern_watch import strip_tags

STATE_FILE = "underclass_state.json"

UNDERCLASS_README = ("https://raw.githubusercontent.com/Jose-Gael-Cruz-Lopez/"
                     "underclassmen-opportunities/main/README.md")
SNDSH_README = ("https://raw.githubusercontent.com/sndsh404/"
                "summer-2027-internships/main/README.md")
EXTERN_GUIDE = "https://www.extern.com/post/2027-underclassmen-cs-internships-guide"

# README sections worth alerting on (roles/programs), matched by
# prefix because headings carry suffixes like "(Fellowships,
# Externships, etc.)". Scholarships and state grants are
# money-not-roles: never alerted, not on the sheet.
UNDERCLASS_ALERT_PREFIXES = (
    "Underclassmen Internships", "Underclassmen Programs",
    "Underclassmen Research Programs", "Rising Freshmen",
)
UNDERCLASS_SHEET_PREFIXES = UNDERCLASS_ALERT_PREFIXES + ("Ambassador Programs",)


def md_table_rows(markdown):
    """(section, cells, first_link) for every markdown/HTML table row,
    tracking the current '## ' heading. Header and separator rows are
    skipped."""
    section = ""
    for line in markdown.splitlines():
        if line.startswith("## "):
            section = re.sub(r"[#*_]|[\U0001F300-\U0001FAFF]", "", line[3:]).strip()
            continue
        if not line.startswith("|"):
            continue
        raw_cells = [c.strip() for c in line.strip("|").split("|")]
        if not raw_cells or set(raw_cells[0]) <= {"-", " ", ":"}:
            continue
        link = None
        m = re.search(r'\]\((https?://[^)]+)\)', line) or re.search(r'href="(https?://[^"]+)"', line)
        if m:
            link = m.group(1)
        cells = [re.sub(r"<[^>]+>|\[|\]\([^)]*\)|\*\*", "", c).strip() for c in raw_cells]
        if cells and cells[0].lower() in ("status", "company", "org", "name", "program"):
            continue  # header row
        yield section, cells, link


def parse_underclass_repo(markdown):
    """Rows from the underclassmen-opportunities README, normalized to
    {section, name, org, detail, location, link, posted}."""
    rows = []
    for section, cells, link in md_table_rows(markdown):
        if not section.startswith(UNDERCLASS_SHEET_PREFIXES) or len(cells) < 3:
            continue
        if section.startswith("Underclassmen Internships") and len(cells) >= 6:
            status, company, role, location = cells[0], cells[1], cells[2], cells[3]
            # SWE/AI filter, same rules as every other watcher.
            if not title_ok(role):
                continue
            rows.append({"section": section, "name": role[:120], "org": company,
                         "detail": status, "location": location, "link": link,
                         "posted": cells[5]})
        else:
            rows.append({"section": section, "name": cells[0][:120],
                         "org": cells[1] if len(cells) > 1 else "",
                         "detail": (cells[2] if len(cells) > 2 else "")[:160],
                         "location": "", "link": link, "posted": ""})
    return rows


def parse_sndsh_repo(markdown):
    """Rows from sndsh404/summer-2027-internships: the posting list
    (tech+US filtered) and the curated program pipelines."""
    rows = []
    for section, cells, link in md_table_rows(markdown):
        if section == "the list" and len(cells) >= 4:
            company, role, location = cells[0], cells[1], cells[2]
            if not title_ok(role) or is_non_us_location(location):
                continue
            rows.append({"section": "Postings (community list)", "name": role[:120],
                         "org": company, "detail": "", "location": location,
                         "link": link, "posted": cells[4] if len(cells) > 4 else ""})
        elif section.startswith("programs") and len(cells) >= 3:
            rows.append({"section": f"Pipelines ({section})", "name": cells[1][:120],
                         "org": cells[0], "detail": cells[2][:160], "location": "",
                         "link": link, "posted": cells[3] if len(cells) > 3 else ""})
    return rows


def parse_extern_guide(raw):
    """The guide's program directory tables: Program | Company | Who |
    Duration | App Window (or Location)."""
    rows = []
    for t in re.findall(r"<table>(.*?)</table>", raw, re.S):
        trs = re.findall(r"<tr[^>]*>(.*?)</tr>", t, re.S)
        if not trs:
            continue
        header = [strip_tags(c).lower() for c in re.findall(r"<t[dh][^>]*>(.*?)</t[dh]>", trs[0], re.S)]
        if not header or header[0] != "program":
            continue
        for tr in trs[1:]:
            cells_raw = re.findall(r"<t[dh][^>]*>(.*?)</t[dh]>", tr, re.S)
            cells = [strip_tags(c) for c in cells_raw]
            if len(cells) < 3:
                continue
            link_m = re.search(r'href="(https?://[^"]+)"', tr)
            row = dict(zip(header, cells))
            rows.append({"section": "Verified directory (Extern)",
                         "name": row.get("program", "")[:120],
                         "org": row.get("company", ""),
                         "detail": f"{row.get('who', '')} · {row.get('duration', '')}".strip(" ·"),
                         "location": row.get("app window") or row.get("location") or "",
                         "link": link_m.group(1) if link_m else None,
                         "posted": ""})
    return rows


def row_key(row):
    return f"{row['org']}::{row['name']}".lower()


def load_json(path, default):
    if not os.path.exists(path):
        return default
    with open(path) as fh:
        return json.load(fh)


def main():
    state = load_json(STATE_FILE, {})
    seen = set(state.get("seen_keys", []))
    first_run = not state

    rows, errors = [], []
    for label, getter in [
        ("underclassmen-opportunities", lambda: parse_underclass_repo(fetch(UNDERCLASS_README))),
        ("summer-2027-internships", lambda: parse_sndsh_repo(fetch(SNDSH_README))),
        ("extern guide", lambda: parse_extern_guide(fetch(EXTERN_GUIDE))),
    ]:
        try:
            got = getter()
            print(f"{label}: {len(got)} rows")
            rows.extend(got)
        except Exception as exc:  # noqa: BLE001 - one dead source must not kill the rest
            errors.append(f"{label}: {exc}")
            print(f"  ! {label} failed: {exc}", file=sys.stderr)

    fresh = [r for r in rows
             if row_key(r) not in seen
             and r["section"].startswith(UNDERCLASS_ALERT_PREFIXES + ("Postings", "Pipelines", "Verified"))]
    seen.update(row_key(r) for r in rows)

    state = {
        "checked_at": datetime.now(timezone.utc).isoformat(),
        "seen_keys": sorted(seen),
        "rows": rows,
    }
    with open(STATE_FILE, "w") as fh:
        json.dump(state, fh, indent=0)

    if first_run:
        print(f"First run - baselined {len(rows)} rows, no alert.")
        return
    if not fresh:
        print("No new underclassman opportunities.")
        return

    lines = []
    for r in fresh:
        lines.append(f"{r['org']} - {r['name']}  [{r['section']}]")
        if r.get("detail"):
            lines.append(f"    {r['detail']}")
        if r.get("location"):
            lines.append(f"    {r['location']}")
        if r.get("link"):
            lines.append(f"    apply: {r['link']}")
        lines.append("")
    lines.append("Reminder: apply to freshman/sophomore programs with the Class of 2029 grad date.")
    body = "\n".join(lines)
    print(f"{len(fresh)} new underclassman opportunity(ies):\n{body}")
    if os.environ.get("SMTP_HOST"):
        send_email(f"[intern-watch] {len(fresh)} new underclassman opportunity(ies)", body)
        print("Alert email sent.")
    push_notify(f"{len(fresh)} new underclassman opportunity(ies)", body)


if __name__ == "__main__":
    sys.exit(main())
