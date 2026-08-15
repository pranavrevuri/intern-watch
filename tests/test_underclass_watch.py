"""Offline tests for underclass_watch's parsers - no network."""

import unittest

from underclass_watch import (
    md_table_rows,
    parse_extern_guide,
    parse_sndsh_repo,
    parse_underclass_repo,
    row_key,
)

UNDERCLASS_MD = """
## Underclassmen Internships
| Status | Company | Role | Location | Application | Date Posted |
| ------ | ------- | ---- | -------- | ----------- | ----------- |
| **[OPEN]** | Scale AI | AI Builder Intern (Open to All Undergrads) | San Francisco, CA | <a href="https://job-boards.greenhouse.io/scaleai/jobs/1"><img src="x" alt="Apply"></a> | Jun 17, 2026 |
| **[OPEN]** | Kalshi | Support Ops Intern (Ops, Not SWE) | New York, NY | <a href="https://jobs.ashbyhq.com/kalshi/2"><img src="x" alt="Apply"></a> | Jul 5, 2026 |

## Underclassmen Programs (Fellowships, Externships, etc.)
| Name | Company | Note |
| ---- | ------- | ---- |
| [Google STEP](https://buildyourfuture.withgoogle.com/programs/step/) | Google | First and second-year students |

## Scholarships
| Name | Company | Note |
| ---- | ------- | ---- |
| [Some Scholarship](https://example.com) | Org | Money |
"""

SNDSH_MD = """
## the list
| Company | Role | Location | Apply | Added |
| --- | --- | --- | --- | --- |
| Susquehanna | Quantitative Systematic Trading Intern (PhD) | New York, NY | [apply](https://careers.sig.com/jobs/1) | 2026-07-21 |
| Rippling | Software Engineer Intern | San Francisco, CA | [apply](https://rippling.com/2) | 2026-07-22 |
| Shopify | Software Engineer Intern | Toronto, Canada | [apply](https://shopify.com/3) | 2026-07-23 |

## programs open now
| org | opportunity | type | deadline |
| --- | --- | --- | --- |
| Neo | [Neo Scholars](https://neo.com/scholars) | CS undergrad fellowship | June 2026 |
"""

EXTERN_HTML = """
<table><tr><th>Program</th><th>Company</th><th>Who</th><th>Duration</th><th>App Window</th></tr>
<tr><td><a href="https://buildyourfuture.withgoogle.com/programs/step/">STEP</a></td><td>Google</td><td>Both</td><td>12 wks</td><td>Oct-Nov 2026</td></tr>
</table>
<table><tr><th>Skill</th><th>Where</th></tr><tr><td>Python</td><td>everywhere</td></tr></table>
"""


class UnderclassRepo(unittest.TestCase):
    def test_swe_intern_kept_ops_dropped(self):
        rows = parse_underclass_repo(UNDERCLASS_MD)
        names = [r["name"] for r in rows]
        self.assertTrue(any("AI Builder" in n for n in names))
        self.assertFalse(any("Support Ops" in n for n in names))

    def test_programs_kept_scholarships_excluded_from_rows_but_sections_differ(self):
        rows = parse_underclass_repo(UNDERCLASS_MD)
        self.assertTrue(any(r["name"] == "Google STEP" for r in rows))
        # Scholarships section isn't in the sheet sections at all here
        self.assertFalse(any(r["section"] == "Scholarships" for r in rows))

    def test_link_extracted_from_html_anchor(self):
        rows = parse_underclass_repo(UNDERCLASS_MD)
        scale = next(r for r in rows if "AI Builder" in r["name"])
        self.assertIn("greenhouse.io/scaleai", scale["link"])


class SndshRepo(unittest.TestCase):
    def test_tech_us_filter(self):
        rows = parse_sndsh_repo(SNDSH_MD)
        names = [r["name"] for r in rows]
        self.assertTrue(any("Software Engineer Intern" in n for n in names))
        self.assertFalse(any("Quantitative Systematic" in n for n in names))  # no tech keyword
        self.assertEqual(sum("Software Engineer Intern" in n for n in names), 1)  # Canada dropped

    def test_program_pipelines_kept(self):
        rows = parse_sndsh_repo(SNDSH_MD)
        self.assertTrue(any(r["name"] == "Neo Scholars" for r in rows))


class ExternGuide(unittest.TestCase):
    def test_program_table_parsed_skills_table_skipped(self):
        rows = parse_extern_guide(EXTERN_HTML)
        self.assertEqual(len(rows), 1)
        r = rows[0]
        self.assertEqual((r["name"], r["org"], r["location"]), ("STEP", "Google", "Oct-Nov 2026"))
        self.assertIn("withgoogle.com", r["link"])


class Keys(unittest.TestCase):
    def test_key_stable_and_distinct(self):
        rows = parse_sndsh_repo(SNDSH_MD)
        keys = [row_key(r) for r in rows]
        self.assertEqual(len(keys), len(set(keys)))


if __name__ == "__main__":
    unittest.main()
