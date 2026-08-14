"""Offline tests for simplify_watch's filtering - no network."""

import unittest

from simplify_watch import new_listings, relevant


def listing(**kw):
    base = {
        "id": "abc-123",
        "company_name": "TestCo",
        "title": "Software Engineer Intern",
        "category": "Software",
        "terms": ["Summer 2027"],
        "active": True,
        "is_visible": True,
        "sponsorship": "Other",
        "locations": ["New York, NY"],
        "url": "https://example.com/apply",
        "date_posted": 1755200000,
    }
    base.update(kw)
    return base


class Relevance(unittest.TestCase):
    def test_live_us_swe_is_relevant(self):
        self.assertTrue(relevant(listing()))

    def test_inactive_or_hidden_dropped(self):
        self.assertFalse(relevant(listing(active=False)))
        self.assertFalse(relevant(listing(is_visible=False)))

    def test_wrong_season_dropped(self):
        self.assertFalse(relevant(listing(terms=["Summer 2026"])))
        self.assertFalse(relevant(listing(terms=[])))

    def test_non_tech_category_dropped(self):
        self.assertFalse(relevant(listing(category="Product")))
        self.assertFalse(relevant(listing(category="Quant")))

    def test_non_swe_titles_dropped_despite_tech_category(self):
        # Simplify's AI/ML/Data category lumps in analytics, data
        # science, and even geoscience - the title must stand alone.
        for title in [
            "Geoscience Intern - Geoscientist",
            "Consulting Intern - Healthcare Data Management and Strategy",
            "Enterprise Analytics Intern",
            "Data & AI Intern - Analyst",
        ]:
            self.assertFalse(relevant(listing(category="AI/ML/Data", title=title)),
                             f"should drop: {title}")

    def test_swe_and_ml_titles_kept(self):
        for title in ["Android Platform Software Engineer Intern",
                      "Machine Learning Engineer Intern - Summer 2027"]:
            self.assertTrue(relevant(listing(category="AI/ML/Data", title=title)),
                            f"should keep: {title}")

    def test_both_category_spellings_kept(self):
        self.assertTrue(relevant(listing(category="Software Engineering")))
        self.assertTrue(relevant(listing(category="AI/ML/Data")))

    def test_citizenship_required_dropped(self):
        self.assertFalse(relevant(listing(sponsorship="U.S. Citizenship is Required")))

    def test_us_person_defense_companies_skipped(self):
        # Simplify's sponsorship flag is often missing for these.
        self.assertFalse(relevant(listing(company_name="Anduril")))
        self.assertFalse(relevant(listing(company_name="SpaceX")))

    def test_no_sponsorship_kept(self):
        # CPT doesn't need sponsorship for the internship itself.
        self.assertTrue(relevant(listing(sponsorship="Does Not Offer Sponsorship")))

    def test_all_non_us_locations_dropped(self):
        self.assertFalse(relevant(listing(locations=["London, UK", "Toronto, Canada"])))

    def test_mixed_or_missing_locations_kept(self):
        self.assertTrue(relevant(listing(locations=["London, UK", "Austin, TX"])))
        self.assertTrue(relevant(listing(locations=[])))


class Diffing(unittest.TestCase):
    def test_only_unseen_relevant_ids_are_new(self):
        ls = [listing(id="a"), listing(id="b"), listing(id="c", active=False)]
        fresh = new_listings(ls, seen_ids={"a"})
        self.assertEqual([l["id"] for l in fresh], ["b"])


if __name__ == "__main__":
    unittest.main()
