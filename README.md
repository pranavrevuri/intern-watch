# Intern Watch

Checks ~170 companies' job boards daily and emails you only when a new
2027 SWE intern posting shows up. Runs for free on GitHub Actions - no
server, no computer that has to stay on.

Most companies are checked through their ATS's public JSON API
(Greenhouse, Lever, Ashby, Workday, SmartRecruiters, plus Amazon's own
search JSON and Eightfold boards like Netflix's - real titles, direct
application links, ~1s each). Custom career sites fall back to a
headless real-Chrome browser, where hits come from actual job *links*
on the page ("link mode") rather than fuzzy text windows - window
matching used to turn pagination controls and filter sidebars into
"postings" and let a tech posting's keywords justify a non-tech
neighbor.

**Note:** your doc had 21 companies, not 25 - Amazon, Microsoft, Meta,
Apple, Google, Nvidia, Databricks, Stripe, Palantir, Uber, Airbnb,
Salesforce, Adobe, Netflix, Oracle, LinkedIn, Roblox, Coinbase, Spotify,
Snap, TikTok. If there are 4 more you meant to include, just add them to
`companies.json` the same way (see Step 2).

## How this version is different from a plain "fetch the page" script

I checked a few of your companies' pages directly first. None of them
run on Greenhouse or Lever (the two ATS platforms with a clean public
API) - they're all custom-built career sites, and several (Palantir in
particular) only load their actual job listings via JavaScript after
the page opens in a browser. A plain HTTP fetch sees an empty shell on
those.

So `monitor.py` now uses a real headless browser (Playwright +
Chromium) to load each page the way a person actually would, then
searches the rendered text. It's still just a script - no LLM calls, no
per-run cost beyond GitHub's free compute minutes.

## Step 1: Create the GitHub repo

1. Go to github.com, click **New repository**. Name it something like
   `intern-watch`. Keep it **Private**. Skip adding a README (you
   already have one here).
2. Upload this folder's files via **Add file → Upload files** on the
   repo page (drag-and-drop works, including the `.github` folder), or
   push via git if you're comfortable with that.

## Step 2: `companies.json` is already filled in

All 21 companies from your doc are already in there, pointed at the
URLs you gave me, set to `"ats": "browser"` (the headless-browser mode).
You don't have to do anything here unless you want to add more
companies or swap in a more specific URL - see "If a company never
finds anything" below.

## Step 3: Set up email notifications

1. If using Gmail: turn on 2-Step Verification on your Google account,
   then create an **App Password** at
   myaccount.google.com/apppasswords. Use that (not your normal
   password) below.
2. In your GitHub repo: **Settings → Secrets and variables → Actions →
   New repository secret**. Add these:
   - `SMTP_HOST` → `smtp.gmail.com`
   - `SMTP_PORT` → `587`
   - `SMTP_USER` → your Gmail address
   - `SMTP_PASS` → the app password from step 1
   - `NOTIFY_EMAIL` → where you want alerts sent (can be the same address)

Prefer Slack? Say so and I'll swap the email step for a one-line Slack
webhook call.

## Step 4: Test it before trusting it

1. Go to the **Actions** tab → **Check Intern Postings** → **Run
   workflow** (manual trigger).
2. Click into the run and watch the log - it prints which company it's
   checking as it goes, and any errors per-company.
3. The **first run treats every existing 2027 posting as "new"** since
   there's nothing to compare against yet - expect one slightly noisy
   email the first time, then only genuinely new postings after that.

## Important: how to tell "nothing posted yet" from "broken check"

It's July 2026 - most of these companies haven't opened Summer 2027
intern applications yet, so a first run turning up **zero hits across
the board is expected**, not necessarily a bug. To sanity-check that
the rendering itself is actually working (as opposed to just finding
nothing because nothing's posted):

1. Temporarily edit `YEAR_PATTERN` in `monitor.py` to match `2026`
   instead of `2027`, and run the workflow manually.
2. If that turns up real 2026 intern postings for at least some
   companies, the pipeline itself is working - change it back to `2027`
   and let it run on schedule.
3. If it turns up nothing even for 2026, that company's page needs a
   different URL or extra handling (see below).

## If a company never finds anything

A few of your URLs are program-overview pages rather than the actual
job search/listing page (for example, a "why intern with us" page
versus the page with actual role titles). Those will often render fine
but just won't contain any job listings to match against. If a specific
company keeps coming up empty even after the 2026 sanity check above,
look for a "See open roles" / "View all jobs" link on that page and
swap in that URL instead - or send it to me and I'll find the right one
and adjust the entry.

A couple of others may load results only after you type into a search
box or click a filter, which a plain page load won't trigger. If that
turns out to be the case for a given company, flag it and I'll add
site-specific handling (Playwright can click and type, it just needs a
few extra lines per site).

## The Extern calendar (summer 2027 release dates)

A second, separate watcher: `extern_watch.py` scrapes extern.com's
per-company internship guides (the "Quick Facts" table each one
carries) for every company with a tech program - Extern's own tech
category plus tech tracks at non-tech companies (banks, retail,
hospitality: think Wells Fargo Technology Summer Analyst) - and
maintains:

- **`InternWatch.xlsx`** - the human-readable watchlist. Sheet 1 is
  sorted so whatever Extern expects to open soonest sits at the top
  (green = window open now, yellow = opening within ~2 months), with
  each company's career page link, Extern guide link, rolling policy,
  and pay. Sheet 2 holds companies with no formal intern program.
  Regenerated every run - don't edit it by hand.
- **`extern_calendar.json`** - the machine-readable state the diff
  runs against.

It runs daily (`.github/workflows/extern-calendar.yml`, ~9 AM ET).
You only get an email when something actually changed: a window
moved, a career link changed, a company gained/lost a formal program,
or Extern published brand-new guides (those are listed for review,
never auto-added - some are non-tech). Add or remove companies in
`extern_companies.json`.

## The Simplify live watch (fastest signal)

`simplify_watch.py` polls the community-maintained
[SimplifyJobs/Summer2027-Internships](https://github.com/SimplifyJobs/Summer2027-Internships)
listings hourly (`.github/workflows/simplify-watch.yml`) and emails
the moment a new posting appears - the community typically reports
postings within hours of them going live. One HTTP request per run,
stdlib only.

Filters (edit `TECH_CATEGORIES` etc. in the script): Summer 2027,
Software + AI/ML/Data categories, US locations, and postings
requiring U.S. citizenship are dropped (CPT). "No sponsorship"
postings are kept with a note - CPT internships don't need
sponsorship; conversion later would.

## The underclassman watch

`underclass_watch.py` (runs with the Simplify watch, every 30 min)
watches freshman/sophomore-specific sources - the
underclassmen-opportunities and summer-2027-internships GitHub repos
plus Extern's verified underclassmen program directory (STEP, Explore,
Ignite, Propel, TEIP, ...) - and alerts on new rows. The full dataset
renders as the "Underclassman programs" sheet in InternWatch.xlsx.
Apply to these with the Class of 2029 graduation date.

## How the layers fit together

| Layer | Source | Lag | Job |
|---|---|---|---|
| Calendar | extern.com guides | days-weeks | when to *expect* each window, tracks, visa stance |
| Live watch | Simplify community list | hours | "a posting just went up" across hundreds of companies |
| Direct checks | company career pages | your cron interval | authoritative check on the shortlist in `companies.json` |

Schedules run in two tiers. The BASE tier fits a private repo's free
2,000 Actions minutes/month: daily full sweep + fast lane every 2h +
Simplify every 2h + daily Extern (~1,850 total). The MAX tier -
fast lane every 20 minutes 24/7, Simplify every 30 minutes, three
full sweeps a day - is scheduled too but its jobs only run when the
repository is PUBLIC (unlimited free minutes); on a private repo
GitHub skips them at zero cost. Making the repo public upgrades the
cadence automatically, no config change needed.

## Adjusting things later

- **Schedule**: edit the `cron:` line in
  `.github/workflows/check-intern-postings.yml`. crontab.guru helps
  build the expression.
- **Keyword matching**: edit `KEYWORD_PATTERN` in `monitor.py` if a
  company phrases the role unusually.
- **Add/remove companies**: edit `companies.json` and push.

## Costs

Free. Loading 21 pages in a headless browser plus a bit of setup time
comes to a few minutes per run - even running daily for a full month
stays well within GitHub Actions' free 2,000 minutes/month.
