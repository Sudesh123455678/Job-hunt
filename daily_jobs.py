#!/usr/bin/env python3
"""Daily relocation job-hunt pipeline.

Fetches Senior Product Designer roles with relocation from the two boards that expose
real, structured APIs (Arbeitnow, VisaSponsor.jobs), keeps only target regions (Europe,
UAE, Australia), dedupes against seen.json so you can tell what's new since yesterday,
ranks each role against PROFILE, and renders jobs.html — a dashboard you bookmark and
check each morning.

Zero dependencies: standard library only. Optionally uses the Anthropic API for sharper
ranking if ANTHROPIC_API_KEY is set in the environment.

Usage:
    python3 daily_jobs.py
"""

import json
import os
import re
import urllib.error
import urllib.request
from datetime import datetime, timezone

# ---------------------------------------------------------------------------
# Tuning — edit these to change what the pipeline looks for and how it ranks.
# ---------------------------------------------------------------------------

REGIONS = {
    "Europe": (
        "europe", "eu remote", "remote europe",
        "germany", "berlin", "munich", "hamburg", "cologne", "frankfurt", "stuttgart",
        "netherlands", "amsterdam", "rotterdam", "utrecht",
        "france", "paris",
        "spain", "madrid", "barcelona",
        "portugal", "lisbon", "porto",
        "ireland", "dublin",
        "united kingdom", "uk", "london", "manchester",
        "switzerland", "zurich", "geneva", "basel",
        "sweden", "stockholm",
        "denmark", "copenhagen",
        "norway", "oslo",
        "finland", "helsinki",
        "austria", "vienna",
        "belgium", "brussels",
        "poland", "warsaw", "krakow",
        "italy", "milan", "rome",
        "estonia", "tallinn",
        "czech", "prague",
    ),
    "UAE": (
        "uae", "u.a.e", "united arab emirates", "dubai", "abu dhabi", "sharjah",
    ),
    "Australia": (
        "australia", "sydney", "melbourne", "brisbane", "perth", "adelaide", "canberra",
    ),
}

TITLE_HINTS = (
    "product designer", "product design", "ux designer", "ux/ui designer",
    "design lead", "product design lead", "designer, design systems", "design systems",
)
SENIOR_HINTS = ("senior", "sr.", "sr ", "lead", "staff", "principal")
JUNIOR_EXCLUDE = ("junior", "jr.", "intern", "internship", "graduate")

RELOCATION_HINTS = (
    "relocation", "relocate", "visa sponsor", "visa-sponsor", "sponsorship",
    "work permit", "blue card", "relocation package", "relocation assistance",
    "visa support",
)
# Strip negated mentions ("no relocation", "does not sponsor visas") before hint
# matching, so a listing that explicitly rules relocation out isn't kept by mistake.
RELOCATION_NEGATION_RE = re.compile(
    r"\b(no|not|without|doesn't|don't|isn't|won't|unable to|can't|cannot)\b"
    r"(\s+\w+){0,4}\s+(relocation|relocate|visa\s*sponsor\w*|sponsorship|"
    r"visa\s*support|work\s*permit|blue\s*card)",
    re.IGNORECASE,
)

HEALTH_KW = (
    "health", "healthcare", "medical", "clinical", "hospital", "patient",
    "pharma", "biotech", "care", "rx", "life sciences",
)
ENTERPRISE_KW = (
    "enterprise", "saas", "b2b", "fintech", "bank", "banking", "payments",
    "platform", "data", "crypto", "insurance",
)

PROFILE = """
Sudesh Harish Shetty — Senior Product Designer, 7+ years experience, based in India,
open to relocating abroad (Indian passport, needs visa sponsorship in Europe/UAE; has an
active Australian PR application in progress so does not need sponsorship there).

Career: currently Senior Product Designer at Motive (pre-IPO fleet-management SaaS,
remote). Previously Innovaccer (healthcare RCM/UX — prior authorization, utilization
management, Rx PA workflows — his strongest domain moat) and Atlassian (Jira: bulk
operations, navigation). Earlier consumer/edtech (Wonderslate, Toppr).

Strengths: healthcare/healthtech product design (best fit), complex enterprise SaaS,
fintech and data-heavy B2B tools, design systems and platform design. Comfortable coding
functional prototypes (React/Tailwind, Cursor, Claude Code), not just producing mockups.

Target titles: Senior Product Designer (primary), also Lead/Staff Product Designer,
Senior UX Designer, Product Design Lead, Senior Product Designer (Design Systems).
Not interested in: junior/mid roles, pure visual/graphic/brand-only roles, 3D/industrial
design.
""".strip()

SEEN_FILE = "seen.json"
OUTPUT_FILE = "jobs.html"
TIMEOUT = 20

ANTHROPIC_MODEL = "claude-haiku-4-5"

# ---------------------------------------------------------------------------
# Fetching
# ---------------------------------------------------------------------------


def _get_json(url):
    req = urllib.request.Request(url, headers={"User-Agent": "daily-job-hunt/1.0"})
    with urllib.request.urlopen(req, timeout=TIMEOUT) as resp:
        return json.loads(resp.read().decode("utf-8", "replace"))


def fetch_arbeitnow():
    """arbeitnow.com public job-board API — EU-heavy, has a visa_sponsorship flag."""
    out = []
    try:
        data = _get_json("https://www.arbeitnow.com/api/job-board-api")
        for job in data.get("data", []):
            title = job.get("title", "") or ""
            tags = " ".join(job.get("tags", []) or [])
            desc = job.get("description", "") or ""
            out.append({
                "source": "Arbeitnow",
                "title": title,
                "company": job.get("company_name", "") or "",
                "location": job.get("location", "") or "",
                "url": job.get("url", "") or "",
                "relocation_flag": bool(job.get("visa_sponsorship")),
                "blob": f"{title} {tags} {desc}",
            })
    except (urllib.error.URLError, ValueError, KeyError, TimeoutError) as e:
        print(f"[arbeitnow] skipped: {e}")
    return out


def fetch_visasponsor():
    """visasponsor.jobs Design & Arts feed — structured, sponsorship-scoped by definition."""
    out = []
    try:
        data = _get_json("https://visasponsor.jobs/api/jobs?classification=Design-and-Arts")
        rows = data if isinstance(data, list) else data.get("jobs", data.get("data", []))
        for job in rows:
            title = job.get("title", "") or job.get("role", "") or ""
            out.append({
                "source": "VisaSponsor.jobs",
                "title": title,
                "company": job.get("company", "") or job.get("employer", "") or "",
                "location": job.get("location", "") or job.get("country", "") or "",
                "url": job.get("url", "") or job.get("link", "") or "",
                "relocation_flag": True,  # board is sponsorship-scoped by definition
                "blob": f"{title} {job.get('description', '') or ''}",
            })
    except (urllib.error.URLError, ValueError, KeyError, TimeoutError) as e:
        print(f"[visasponsor.jobs] skipped: {e}")
    return out


# ---------------------------------------------------------------------------
# Filtering
# ---------------------------------------------------------------------------


def match_title(title):
    t = title.lower()
    if any(j in t for j in JUNIOR_EXCLUDE):
        return False
    if not any(h in t for h in TITLE_HINTS):
        return False
    return any(s in t for s in SENIOR_HINTS)


def match_region(location):
    loc = (location or "").lower()
    for region, keywords in REGIONS.items():
        if any(k in loc for k in keywords):
            return region
    return None


def looks_relocation_friendly(job):
    if job.get("relocation_flag"):
        return True
    blob = f"{job.get('title', '')} {job.get('location', '')} {job.get('blob', '')}"
    cleaned = RELOCATION_NEGATION_RE.sub(" ", blob).lower()
    return any(h in cleaned for h in RELOCATION_HINTS)


def filter_jobs(raw_jobs):
    """Keep Senior/Lead/Staff Product/UX Designer roles in Europe, UAE, or Australia.

    Australia keeps roles even when relocation is unconfirmed (PR track means no
    sponsorship needed there); other regions must look relocation-friendly.
    """
    kept = []
    for job in raw_jobs:
        if not match_title(job.get("title", "")):
            continue
        region = match_region(job.get("location", ""))
        if not region:
            continue
        relocation_ok = looks_relocation_friendly(job)
        if region != "Australia" and not relocation_ok:
            continue
        job["region"] = region
        job["relocation_confirmed"] = relocation_ok
        kept.append(job)
    return kept


# ---------------------------------------------------------------------------
# Ranking
# ---------------------------------------------------------------------------


def heuristic_score(job):
    blob = f"{job.get('title', '')} {job.get('company', '')} {job.get('blob', '')}".lower()
    score = 10  # general product design baseline
    if any(k in blob for k in HEALTH_KW):
        score = 50
    elif any(k in blob for k in ENTERPRISE_KW):
        score = 30

    title = job.get("title", "").lower()
    if any(s in title for s in ("staff", "lead", "principal")):
        score += 10
    elif "senior" in title:
        score += 5

    if job.get("relocation_confirmed"):
        score += 15

    return score


def rank_heuristic(jobs):
    for job in jobs:
        job["score"] = heuristic_score(job)
    jobs.sort(key=lambda j: j["score"], reverse=True)
    return jobs


def rank_with_claude(jobs, api_key):
    """Ask Claude to score fit 0-100 for each job against PROFILE. Falls back on any error."""
    if not jobs:
        return jobs

    listing = "\n".join(
        f"{i}. {j['title']} — {j['company']} ({j['location']}, {j['region']}, "
        f"relocation {'confirmed' if j['relocation_confirmed'] else 'unconfirmed'})"
        for i, j in enumerate(jobs)
    )
    prompt = (
        "Score how well each job listing fits this candidate's profile, 0-100 "
        "(100 = ideal fit). Weigh healthcare/healthtech domain highest, then complex "
        "enterprise SaaS/fintech, then general product design. Favor confirmed "
        "relocation and closer seniority match (Senior/Lead/Staff).\n\n"
        f"CANDIDATE PROFILE:\n{PROFILE}\n\nJOB LISTINGS:\n{listing}\n\n"
        "Respond with ONLY a JSON array of integers, one score per listing, in the same "
        "order, e.g. [72, 40, 88, ...]. No other text."
    )

    body = json.dumps({
        "model": ANTHROPIC_MODEL,
        "max_tokens": 1024,
        "messages": [{"role": "user", "content": prompt}],
    }).encode("utf-8")

    req = urllib.request.Request(
        "https://api.anthropic.com/v1/messages",
        data=body,
        headers={
            "Content-Type": "application/json",
            "x-api-key": api_key,
            "anthropic-version": "2023-06-01",
        },
    )
    try:
        with urllib.request.urlopen(req, timeout=30) as resp:
            data = json.loads(resp.read().decode("utf-8", "replace"))
        text = "".join(block.get("text", "") for block in data.get("content", []))
        match = re.search(r"\[[\d,\s]+\]", text)
        scores = json.loads(match.group(0)) if match else None
        if not scores or len(scores) != len(jobs):
            raise ValueError("score count mismatch")
        for job, score in zip(jobs, scores):
            job["score"] = int(score)
        jobs.sort(key=lambda j: j["score"], reverse=True)
        print(f"Ranked {len(jobs)} role(s) with {ANTHROPIC_MODEL}.")
        return jobs
    except Exception as e:
        print(f"[claude ranking] falling back to heuristic: {e}")
        return rank_heuristic(jobs)


def rank_jobs(jobs):
    api_key = os.environ.get("ANTHROPIC_API_KEY")
    if api_key:
        return rank_with_claude(jobs, api_key)
    return rank_heuristic(jobs)


# ---------------------------------------------------------------------------
# Dedupe / memory
# ---------------------------------------------------------------------------


def job_key(job):
    return job.get("url") or f"{job.get('title')}|{job.get('company')}|{job.get('location')}"


def load_seen():
    if not os.path.exists(SEEN_FILE):
        return {}
    try:
        with open(SEEN_FILE, "r", encoding="utf-8") as f:
            return json.load(f)
    except (json.JSONDecodeError, OSError):
        return {}


def apply_seen(jobs, seen):
    today = datetime.now(timezone.utc).date().isoformat()
    for job in jobs:
        key = job_key(job)
        job["is_new"] = key not in seen
        if key not in seen:
            seen[key] = today
    return jobs, seen


def save_seen(seen):
    with open(SEEN_FILE, "w", encoding="utf-8") as f:
        json.dump(seen, f, indent=2, sort_keys=True)


# ---------------------------------------------------------------------------
# Rendering
# ---------------------------------------------------------------------------


def esc(text):
    return (
        (text or "")
        .replace("&", "&amp;")
        .replace("<", "&lt;")
        .replace(">", "&gt;")
        .replace('"', "&quot;")
    )


def render_html(jobs, generated_at):
    new_count = sum(1 for j in jobs if j["is_new"])
    rows = []
    for job in jobs:
        badge = '<span class="badge new">NEW</span>' if job["is_new"] else ""
        relo = (
            '<span class="badge relo-yes">relocation confirmed</span>'
            if job["relocation_confirmed"]
            else '<span class="badge relo-unconfirmed">relocation unconfirmed</span>'
        )
        rows.append(f"""
        <div class="card">
          <div class="card-head">
            <h3><a href="{esc(job['url'])}" target="_blank" rel="noopener">{esc(job['title'])}</a></h3>
            {badge}
          </div>
          <div class="meta">{esc(job['company'])} &middot; {esc(job['location'])} &middot; {esc(job['region'])}</div>
          <div class="badges">
            {relo}
            <span class="badge source">{esc(job['source'])}</span>
            <span class="badge score">fit {job['score']}</span>
          </div>
        </div>""")

    rows_html = "\n".join(rows) if rows else '<p class="empty">No matching roles today. Check back tomorrow.</p>'

    return f"""<!doctype html>
<html lang="en">
<head>
<meta charset="utf-8">
<title>Daily Job Hunt</title>
<meta name="viewport" content="width=device-width, initial-scale=1">
<style>
  :root {{ color-scheme: light dark; }}
  body {{ font-family: -apple-system, BlinkMacSystemFont, "Segoe UI", sans-serif;
         max-width: 900px; margin: 0 auto; padding: 24px 16px 64px;
         background: #fafafa; color: #1a1a1a; }}
  @media (prefers-color-scheme: dark) {{
    body {{ background: #111; color: #eee; }}
    .card {{ background: #1c1c1c; border-color: #2c2c2c; }}
    .meta {{ color: #aaa; }}
  }}
  h1 {{ font-size: 1.5rem; margin-bottom: 4px; }}
  .sub {{ color: #666; margin-bottom: 24px; }}
  .card {{ background: #fff; border: 1px solid #e5e5e5; border-radius: 10px;
          padding: 16px 18px; margin-bottom: 12px; }}
  .card-head {{ display: flex; align-items: center; justify-content: space-between; gap: 8px; }}
  .card h3 {{ margin: 0; font-size: 1.05rem; }}
  .card a {{ color: inherit; text-decoration: none; }}
  .card a:hover {{ text-decoration: underline; }}
  .meta {{ color: #666; font-size: 0.9rem; margin: 4px 0 10px; }}
  .badges {{ display: flex; flex-wrap: wrap; gap: 6px; }}
  .badge {{ display: inline-block; font-size: 0.72rem; font-weight: 600; padding: 3px 8px;
           border-radius: 999px; text-transform: uppercase; letter-spacing: 0.02em; }}
  .badge.new {{ background: #ffe082; color: #6b4c00; }}
  .badge.relo-yes {{ background: #c8f7d4; color: #1a5c2e; }}
  .badge.relo-unconfirmed {{ background: #f0f0f0; color: #666; }}
  .badge.source {{ background: #e0ecff; color: #1a3d8f; }}
  .badge.score {{ background: #f3e5ff; color: #5a1a8f; }}
  .empty {{ color: #888; }}
</style>
</head>
<body>
  <h1>Daily Job Hunt</h1>
  <div class="sub">Generated {esc(generated_at)} &middot; {len(jobs)} role(s) &middot; {new_count} new since last run</div>
  {rows_html}
</body>
</html>
"""


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------


def main():
    raw_jobs = fetch_arbeitnow() + fetch_visasponsor()
    print(f"Fetched {len(raw_jobs)} raw listing(s).")

    jobs = filter_jobs(raw_jobs)
    print(f"{len(jobs)} match title/region/relocation filters.")

    jobs = rank_jobs(jobs)

    seen = load_seen()
    jobs, seen = apply_seen(jobs, seen)
    save_seen(seen)

    generated_at = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M UTC")
    html = render_html(jobs, generated_at)
    with open(OUTPUT_FILE, "w", encoding="utf-8") as f:
        f.write(html)

    new_count = sum(1 for j in jobs if j["is_new"])
    print(f"Wrote {OUTPUT_FILE}: {len(jobs)} role(s), {new_count} new.")


if __name__ == "__main__":
    main()
