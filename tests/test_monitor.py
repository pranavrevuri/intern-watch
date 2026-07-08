"""Tests for intern-watch matching, dedup, and state logic.

Run with:  python -m unittest discover tests
No network access needed - everything uses inline fixtures.
"""

import sys
import os
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import monitor


class TestMatching(unittest.TestCase):
    def test_title_variations_match(self):
        for title in [
            "Software Engineer Intern",
            "SWE Intern - Summer 2027",
            "Software Undergrad Engineering Internships",
            "Machine Learning Intern",
            "2027 Applied Science Intern (Machine Learning)",
            "Technology Analyst Internship Program",
            "Quantitative Developer Intern",
            "Data Science Intern",
        ]:
            self.assertTrue(monitor.text_matches(title), f"should match: {title}")

    def test_irrelevant_roles_do_not_match(self):
        for title in [
            "Marketing Intern",
            "HR Internship Program",
            "Senior Software Engineer",   # not an intern role
            "Legal Intern",
        ]:
            self.assertFalse(monitor.text_matches(title), f"should NOT match: {title}")


class TestLocationFilter(unittest.TestCase):
    def test_non_us_locations_detected(self):
        for loc in [
            "Taipei, Taiwan",
            "New Taipei City, Taiwan",
            "London, United Kingdom",
            "Bengaluru, India",
            "Remote - Canada",
            "Tokyo, Japan",
            "Dublin, Ireland",
            "Mexico City, Mexico",
        ]:
            self.assertTrue(monitor.is_non_us_location(loc), f"should flag: {loc}")

    def test_us_or_missing_locations_kept(self):
        for loc in [
            "",                       # no location shown - keep
            None,
            "New York, NY",
            "Seattle, WA, United States",
            "Remote",
            "Albuquerque, New Mexico",  # must not count as Mexico
            "Atlanta, Georgia",         # US state, not the country
            "Jersey City, NJ",
        ]:
            self.assertFalse(monitor.is_non_us_location(loc), f"should keep: {loc}")

    def test_find_hits_drops_foreign_postings(self):
        text = "Software Engineer Intern - Taipei, Taiwan - apply now"
        self.assertEqual(monitor.find_hits(text, "http://x.com"), [])

    def test_find_hits_keeps_us_and_locationless_postings(self):
        filler = "x" * 500
        text = ("Software Engineer Intern - Seattle, WA" + filler +
                "Machine Learning Intern, software team")  # no location shown
        hits = monitor.find_hits(text, "http://x.com")
        self.assertEqual(len(hits), 2)


class TestFindHits(unittest.TestCase):
    def test_overlapping_windows_merge_into_one_hit(self):
        text = ("internship program details internship overview "
                "Software Dev Engineer Intern apply today")
        hits = monitor.find_hits(text, "http://x.com")
        self.assertEqual(len(hits), 1)

    def test_distant_postings_stay_separate(self):
        filler = "x" * 500
        text = ("2027 Software Dev Engineer Intern - Seattle" + filler +
                "Machine Learning Intern - New York, software team")
        hits = monitor.find_hits(text, "http://x.com")
        self.assertEqual(len(hits), 2)

    def test_no_match_returns_empty(self):
        self.assertEqual(monitor.find_hits("nothing relevant here", "http://x.com"), [])


class TestDedupKey(unittest.TestCase):
    def test_key_stable_across_cosmetic_changes(self):
        a = {"title": "Software  Engineer\nIntern", "url": "http://x.com/1"}
        b = {"title": "software engineer intern", "url": "http://x.com/1"}
        self.assertEqual(monitor.normalize_key(a), monitor.normalize_key(b))

    def test_different_jobs_get_different_keys(self):
        a = {"title": "SWE Intern 2026", "url": "http://x.com/1"}
        b = {"title": "SWE Intern 2027", "url": "http://x.com/1"}
        self.assertNotEqual(monitor.normalize_key(a), monitor.normalize_key(b))


class TestReconcile(unittest.TestCase):
    def test_new_posting_reported_once_then_remembered(self):
        state = {"entries": {}}
        hit = {"title": "SWE Intern", "url": "http://x.com/1"}
        new1, state = monitor.reconcile(state, [hit], "2026-07-06T00:00:00")
        self.assertEqual(len(new1), 1)
        new2, state = monitor.reconcile(state, [hit], "2026-07-07T00:00:00")
        self.assertEqual(len(new2), 0)  # idempotent - rerun doesn't re-alert

    def test_disappeared_posting_marked_inactive_not_deleted(self):
        state = {"entries": {}}
        hit = {"title": "SWE Intern", "url": "http://x.com/1"}
        _, state = monitor.reconcile(state, [hit], "2026-07-06T00:00:00")
        _, state = monitor.reconcile(state, [], "2026-07-08T00:00:00")
        key = monitor.normalize_key(hit)
        self.assertIn(key, state["entries"])            # history preserved
        self.assertFalse(state["entries"][key]["active"])

    def test_legacy_keys_suppress_re_alerting_after_migration(self):
        hit = {"title": "SWE Intern", "url": "http://x.com/page"}
        state = {"entries": {}, "legacy": ["http://x.com/page::SWE Intern"]}
        new, state = monitor.reconcile(state, [hit], "2026-07-06T00:00:00")
        self.assertEqual(len(new), 0)  # was alerted pre-migration


class TestStateMigration(unittest.TestCase):
    def test_v1_state_migrates_without_losing_keys(self):
        import json, tempfile
        v1 = {"Amazon": ["http://a.com::Some Intern Posting"]}
        with tempfile.TemporaryDirectory() as d:
            path = os.path.join(d, "state.json")
            with open(path, "w") as f:
                json.dump(v1, f)
            old = monitor.STATE_FILE
            monitor.STATE_FILE = path
            try:
                state = monitor.load_state()
            finally:
                monitor.STATE_FILE = old
        self.assertEqual(state["version"], 2)
        self.assertIn("Amazon", state["companies"])
        self.assertIn("http://a.com::Some Intern Posting",
                      state["companies"]["Amazon"]["legacy"])


if __name__ == "__main__":
    unittest.main()
