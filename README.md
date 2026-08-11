# Daily relocation job-hunt app

A small, unattended pipeline that fetches Senior Product Designer roles with relocation,
keeps only your target regions, flags what's new since yesterday, ranks each role against
your profile, and writes `jobs.html` — a dashboard you bookmark and check each morning.
Zero dependencies (Python standard library only).

## What it does each run

1. Fetch from the two boards with real feeds worth automating (Arbeitnow, VisaSponsor.jobs).
2. Filter to Senior/Lead/Staff Product/UX Designer roles in Europe, UAE, or Australia.
   (Australia keeps roles even when relocation is "unconfirmed" — your PR track means you
   don't need sponsorship there; other regions must look relocation-friendly.)
3. Dedupe against `seen.json` so you only see roles that are genuinely new.
4. Rank each role by fit (healthcare > enterprise/SaaS/fintech > general, plus a
   relocation/seniority boost).
5. Render `jobs.html` with a NEW badge on fresh roles.

## Run it now

```bash
python3 daily_jobs.py
open jobs.html   # macOS; use xdg-open on Linux, start on Windows
```

## Make it a real daily "app" — pick one

**A. GitHub Actions (recommended — runs in the cloud, laptop can be off).** Push this
folder to a GitHub repo. The workflow in `.github/workflows/daily-job-hunt.yml` runs every
morning, commits the refreshed `jobs.html`, and (if you enable Settings → Pages → deploy
from branch) publishes it at a URL you can bookmark on your phone. Free for public repos.

**B. Local schedule (simplest, but only runs when your machine is on).**

- macOS/Linux cron — `crontab -e`, then:
  `30 9 * * * cd /path/to/job-hunt-app && /usr/bin/python3 daily_jobs.py`
- Windows — Task Scheduler → daily trigger → action `python daily_jobs.py`.

## Better ranking (optional)

The heuristic works with no setup. To upgrade ranking to Claude's judgement, set an API
key and the script uses it automatically:

```bash
export ANTHROPIC_API_KEY=sk-ant-...
```

In GitHub Actions, add it under Settings → Secrets → Actions as `ANTHROPIC_API_KEY`. Daily
runs use the cheap `claude-haiku-4-5` model by default (swap to `claude-sonnet-5` in
`daily_jobs.py` for sharper calls). Cost is a fraction of a cent per day at this volume.

## The two build paths (and why this is path 1)

- **Path 1 — this app (deterministic).** Reliable, free, unattended, but limited to
  boards with real feeds. Great for a daily cadence.
- **Path 2 — Claude Code headless (deeper discovery).** Reuse the `job-hunt` skill for
  real open-web search + per-role relocation verification (the smart, agentic part). Run
  it on a schedule with print mode, e.g.:

  ```bash
  claude -p "Run my job-hunt skill across Europe, UAE and Australia. Output only roles \
    not already in seen.json as an HTML table appended to jobs.html." \
    --allowedTools "WebSearch,WebFetch,Read,Write"
  ```

  This costs more per run and is less deterministic, so it's best as a weekly deep sweep
  that complements the daily deterministic run — not a replacement for it.

## Why not scrape LinkedIn/Indeed here

Their terms forbid automated access and they block it aggressively; a scraper would make
the app less reliable and risks your own account. Use LinkedIn manually: search there,
then paste a promising public listing URL into Claude to verify + rank it (see the
`job-hunt` skill's "Using LinkedIn manually" section).

## Files

- `daily_jobs.py` — the whole pipeline.
- `.github/workflows/daily-job-hunt.yml` — the daily cloud schedule.
- `seen.json` — auto-created; the memory of what you've already been shown.
- `jobs.html` — auto-created; your dashboard.

## Tuning

Edit the constants at the top of `daily_jobs.py`: `REGIONS` (locations), `TITLE_HINTS`
(role titles), `HEALTH_KW`/`ENTERPRISE_KW` (ranking), and `PROFILE` (the text Claude ranks
against). Adding a new feed source = one more `fetch_*` function returning the same dict
shape.
