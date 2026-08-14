"""Offline tests for extern_watch's parsing and diff logic - no
network, no openpyxl needed. Fixtures are trimmed real fragments of
Extern guide pages; if Extern restructures the Quick Facts table or
FAQ JSON-LD, these should be updated alongside the parsers."""

import unittest
from datetime import date

from extern_watch import (
    classify,
    diff_calendars,
    format_window,
    parse_guide,
    parse_quick_facts,
    parse_tracks,
    parse_window,
    tech_tracks_of,
)

QUICK_FACTS_HTML = """
<h2>Quick Facts</h2>
<div class="table-container"><table>
<thead><tr><th>Fact</th><th>Detail</th></tr></thead>
<tbody>
<tr><td>Where to apply</td><td><a href="https://www.amazon.jobs/en/teams/internships-for-students">amazon.jobs student internships</a>. Set a job alert</td></tr>
<tr><td>Application window (2027&ndash;28)</td><td>Applications for summer 2027 expected to open Aug to Oct 2026 for SDE and tech; finance earlier</td></tr>
<tr><td>Rolling?</td><td>Yes, confirmed. Apply within the first 1 to 2 weeks</td></tr>
<tr><td>Compensation</td><td>SDE Seattle $110,500 to $160,000 annualized base</td></tr>
<tr><td># Programs</td><td>10+ intern tracks</td></tr>
</tbody></table></div>
<h2>Next Section</h2>
<title>Amazon Internship 2027-2028: Deadlines &amp; How to Apply</title>
"""

NO_PROGRAM_HTML = """
<h2>Quick Facts</h2>
<table>
<tr><td>Where to apply</td><td><a href="https://jobs.ashbyhq.com/zapier">board</a></td></tr>
<tr><td>Application window (2027&ndash;28)</td><td>No cohort intern cycle exists. Full-time roles post year-round</td></tr>
<tr><td># Programs</td><td>0 formal internship programs (August 2026)</td></tr>
</table>
<title>Zapier Internship 2027-2028: How to Break In</title>
"""


class WindowParsing(unittest.TestCase):
    # Fixed reference date: the plausibility filter drops dates far
    # from "today", so tests must not depend on the wall clock.
    today = date(2026, 8, 14)

    def window(self, text):
        return parse_window(text, today=self.today)

    def test_month_range_with_shared_year(self):
        self.assertEqual(self.window("expected to open Aug to Oct 2026"),
                         ("2026-08", "2026-10"))

    def test_year_digits_not_eaten_as_day_of_month(self):
        # "Oct 2026" must parse as month+year, not day 20 + no year.
        self.assertEqual(self.window("Expected August to October 2026, rolling"),
                         ("2026-08", "2026-10"))

    def test_prior_cycle_dates_ignored(self):
        text = ("Not yet published. Based on the Summer 2026 cycle (posted ~Sep 17, 2025), "
                "applications for summer 2027 are expected to open ~Sep-Oct 2026 and run on")
        self.assertEqual(self.window(text), ("2026-09", "2026-10"))

    def test_prior_cycle_opened_clause_out_of_range(self):
        # A past "opened <date>" must lose to the expected-window clause.
        text = ("Not yet posted. The prior posting opened November 2025. Roles are "
                "expected ~September to November 2026")
        self.assertEqual(self.window(text), ("2026-09", "2026-11"))

    def test_rolling_close_date_not_window_end(self):
        text = "expected to open ~mid-August to October 2026, rolling to March 2027"
        self.assertEqual(self.window(text), ("2026-08", "2026-10"))

    def test_deadline_clause_not_window_end(self):
        text = "Expected September 2026 open, priority deadline late October to mid-November 2026"
        self.assertEqual(self.window(text), ("2026-09", "2026-09"))

    def test_single_month(self):
        self.assertEqual(self.window("Expected ~mid-October 2026, a 2-4 week burst"),
                         ("2026-10", "2026-10"))

    def test_season(self):
        self.assertEqual(self.window("Opens fall 2026"), ("2026-09", "2026-09"))

    def test_season_plus_period_range(self):
        self.assertEqual(self.window("Expected fall 2026 through early 2027 for summer 2027 roles"),
                         ("2026-09", "2027-02"))

    def test_bare_year_periods(self):
        self.assertEqual(self.window("Expected late 2026 through early 2027 for Summer 2027"),
                         ("2026-11", "2027-02"))

    def test_cohort_dates_ignored(self):
        # "for Dec 2027 start" is the internship, not the window.
        self.assertEqual(self.window("expected April to May 2027 for Dec 2027 start"),
                         ("2027-04", "2027-05"))
        self.assertIsNone(self.window("The Summer 2027 SWE internship posting"))

    def test_prior_cycle_cohort_postings_ignored(self):
        text = ("Expected August to September 2026 opening based on prior cycles "
                "(Summer 2026 postings appeared September 2025). Rolling review")
        self.assertEqual(self.window(text), ("2026-08", "2026-09"))

    def test_cross_year_borrowed_range(self):
        self.assertEqual(self.window("expected to open in September and close January"),
                         ("2026-09", "2026-09"))

    def test_no_cycle(self):
        self.assertIsNone(self.window("No cohort intern cycle exists. Roles post year-round"))
        self.assertIsNone(self.window("No fixed window. Opportunities arise ad hoc."))
        self.assertIsNone(self.window(None))


class GuideParsing(unittest.TestCase):
    def test_quick_facts_table(self):
        facts = parse_quick_facts(QUICK_FACTS_HTML)
        self.assertIn("where to apply", facts)
        self.assertEqual(facts["where to apply"][1],
                         ["https://www.amazon.jobs/en/teams/internships-for-students"])

    def test_full_guide(self):
        e = parse_guide(QUICK_FACTS_HTML, "Amazon", "amazon")
        self.assertEqual(e["window"], ("2026-08", "2026-10"))
        self.assertFalse(e["no_formal_program"])
        self.assertTrue(e["career_urls"])
        self.assertTrue((e["rolling"] or "").startswith("Yes"))

    def test_no_formal_program_flag(self):
        e = parse_guide(NO_PROGRAM_HTML, "Zapier", "zapier")
        self.assertTrue(e["no_formal_program"])
        self.assertIsNone(e["window"])


class Classification(unittest.TestCase):
    today = date(2026, 8, 14)

    def entry(self, **kw):
        base = {"name": "X", "window_text": "text", "no_formal_program": False, "window": None}
        base.update(kw)
        return base

    def test_in_window(self):
        self.assertEqual(classify(self.entry(window=("2026-08", "2026-10")), self.today), "in_window")

    def test_upcoming_within_two_months(self):
        self.assertEqual(classify(self.entry(window=("2026-09", "2026-11")), self.today), "upcoming")

    def test_later(self):
        self.assertEqual(classify(self.entry(window=("2027-01", "2027-02")), self.today), "later")

    def test_passed(self):
        self.assertEqual(classify(self.entry(window=("2026-01", "2026-03")), self.today), "passed")

    def test_grace_month_still_in_window(self):
        # Ended last month + rolling reviews = still worth checking.
        self.assertEqual(classify(self.entry(window=("2026-06", "2026-07")), self.today), "in_window")

    def test_open_until_filled_is_open(self):
        e = self.entry(window=("2026-07", "2026-07"),
                       window_text="Posted July 20, 2026; open until filled")
        self.assertEqual(classify(e, self.today), "in_window")

    def test_no_program_wins(self):
        self.assertEqual(classify(self.entry(no_formal_program=True), self.today), "no_program")

    def test_continuous(self):
        self.assertEqual(classify(self.entry(window_text="No fixed window"), self.today), "continuous")


TRACKS_HTML = """
<h2>Which Tracks Should You Target?</h2>
<table>
<tr><th>Track</th><th>What it is</th></tr>
<tr><td>Corporate &amp; Investment Banking (CIB)</td><td>...</td></tr>
<tr><td>Technology</td><td>...</td></tr>
<tr><td>Digital Marketing</td><td>...</td></tr>
<tr><td>STEP</td><td>...</td></tr>
</table>
<table>
<tr><th>Skill (from real JDs)</th><th>Where</th></tr>
<tr><td>Financial modeling</td><td>...</td></tr>
</table>
"""


class TrackParsing(unittest.TestCase):
    def test_tracks_table_found_skills_table_skipped(self):
        self.assertEqual(parse_tracks(TRACKS_HTML),
                         ["Corporate & Investment Banking (CIB)", "Technology",
                          "Digital Marketing", "STEP"])

    def test_tech_filter(self):
        tech = tech_tracks_of(parse_tracks(TRACKS_HTML))
        self.assertIn("Technology", tech)
        self.assertIn("STEP", tech)
        self.assertNotIn("Corporate & Investment Banking (CIB)", tech)
        self.assertNotIn("Digital Marketing", tech)


class WindowFormatting(unittest.TestCase):
    def test_single_month(self):
        self.assertEqual(format_window(("2026-08", "2026-08")), "August 2026")

    def test_same_year_range(self):
        self.assertEqual(format_window(("2026-08", "2026-10")), "August–October 2026")

    def test_cross_year_range(self):
        self.assertEqual(format_window(("2026-11", "2027-02")), "November 2026 – February 2027")

    def test_none(self):
        self.assertEqual(format_window(None), "-")


class Diffing(unittest.TestCase):
    def test_no_changes(self):
        a = [{"slug": "x", "name": "X", "window_text": "w", "career_urls": [], "no_formal_program": False}]
        self.assertEqual(diff_calendars(a, a), [])

    def test_window_change_and_new_company(self):
        old = [{"slug": "x", "name": "X", "window_text": "Aug 2026", "career_urls": [], "no_formal_program": False}]
        new = [
            {"slug": "x", "name": "X", "window_text": "Sep 2026", "career_urls": [], "no_formal_program": False},
            {"slug": "y", "name": "Y", "window_text": "Oct 2026", "career_urls": [], "no_formal_program": False},
        ]
        changes = diff_calendars(old, new)
        self.assertEqual(len(changes), 2)
        self.assertTrue(any("window changed" in c for c in changes))
        self.assertTrue(any(c.startswith("NEW COMPANY: Y") for c in changes))

    def test_fetch_errors_are_not_changes(self):
        old = [{"slug": "x", "name": "X", "window_text": "Aug 2026", "career_urls": [], "no_formal_program": False}]
        new = [{"slug": "x", "name": "X", "window_text": None, "career_urls": [], "no_formal_program": False,
                "fetch_error": "HTTP 503"}]
        self.assertEqual(diff_calendars(old, new), [])


if __name__ == "__main__":
    unittest.main()
