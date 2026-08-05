#!/usr/bin/env -S uv run
# /// script
# dependencies = ["pydantic", "python-dotenv", "pyyaml", "rich", "psycopg[binary]"]
# ///
"""ADW Leadgen — source and qualify leads from the web.

The factory's near-zero build cost flips the sales sequence: you can build the
MVP before they pay. But you still need leads — people with real, software-able
problems. This workflow automates the sourcing + qualification; the human does
the outreach.

    request -> fetch(code, Brave Search API / HN Algolia fallback)
            -> [optional depth-fetch(code, Browserbase for full page text)]
            -> harvest(agent: leadgen, classify + score)
            -> emit(code, insert qualified leads into lead_backlog)

Sources (tried in order):
  1. BRAVE SEARCH (primary, if BRAVE_API_KEY set) — searches the whole web for
     pain signals. Pass a niche and it generates pain-signal queries
     ("is there a tool that {niche}", "{niche} spreadsheet nightmare", ...).
  2. HN ALGOLIA (fallback, no key needed) — Ask HN + story search, last 30 days.
  3. BROWSERBASE (optional depth, if BROWSERBASE_API_KEY set) — renders the top
     results' full pages (Reddit 403s plain HTTP; Browserbase bypasses that) so
     the leadgen agent sees the post body + comments, not just the search snippet.

The lead_backlog is the queue. The human reviews it (`just leads`) and picks
which to build MVPs for (`just build-lead <id>`).

Usage:
    just leadgen "small business inventory"           # niche -> auto-generated queries
    just leadgen "indie hacker email automation"      # any niche
    just leadgen --sources reddit/smallbusiness       # legacy direct-fetch (HN works)
"""

import argparse
import json
import os
import subprocess
import sys
import urllib.parse
import urllib.request

from adw_modules import agents, gates, session, utils
from adw_modules.data_types import (AgentCall, LeadHarvestOutput, PhaseParams)

REQUIRED_AGENTS = ["leadgen"]
UA = "Mozilla/5.0 (X11; Linux x86_64; rv:128.0) Gecko/20100101 Firefox/128.0"

# Pain-signal query templates. {n} = the niche. These target people actively
# describing a problem, not people selling solutions.
PAIN_TEMPLATES = [
    # Brave honors natural-language phrasing better than exact-phrase quotes
    # (Google-style "is there a tool" returns 0 on Brave). Pain signals are
    # evergreen, not time-sensitive, so no freshness filter by default.
    'is there a tool for {n}',
    'how do I automate {n}',
    'I wish there was an app for {n}',
    '{n} spreadsheet nightmare',
    '{n} by hand better way',
    'I would pay for {n} tool',
    '{n} there has to be a better way',
]

# No-niche defaults — broad pain-signal searches across communities.
DEFAULT_QUERIES = [
    'is there a tool that site:reddit.com',
    'I wish there was an app site:reddit.com',
    'how do I automate small business',
    'spreadsheet nightmare better way',
    'I would pay for tool site:news.ycombinator.com',
]


def _generate_queries(niche: str) -> list[str]:
    niche = niche.strip()
    if not niche:
        return DEFAULT_QUERIES
    return [t.format(n=niche) for t in PAIN_TEMPLATES]


# ── phase directive ───────────────────────────────────────────────────────────

HARVEST_DIRECTIVE = """

--- PHASE: HARVEST ---
Read the file of web/community posts at the path given below. For EACH post
that describes a software-able problem (a pain a small web app, CLI, scraper,
integration, dashboard, or automation could solve), produce a LeadCandidate.

Score each 0-4 (one point each): software-able, MVP-sized (one build, not a
6-month project), reachable (public username you could DM), budget-signal (a
business, or already paying for something clunky, or says "I'd pay for this").

Drop: advice-seeking, emotional venting, questions with obvious off-the-shelf
answers, and anything not software-able. An honest empty harvest is valid.

For each lead scoring >= 2, include it. Dedup by problem (keep the highest-
scoring post if several describe the same pain). Write a human-readable summary
to <context_handoff_dir>/lead_findings.md too.

RAW POSTS FILE: {posts_file}

Emit ONLY valid JSON matching LeadHarvestOutput. The response MUST start
with `{` and end with `}`. A YAML list starting with `-` is WRONG. The `leads`
field is a JSON array `[...]` of objects, NOT a YAML list of `- id:` entries:

{
  "status": "success",
  "summary": "one line: N leads from M posts across K sources",
  "artifacts": ["<context_handoff_dir>/lead_findings.md"],
  "leads": [
    {
      "id": "stable-kebab-slug",
      "source": "brave/...",
      "source_url": "https://...",
      "who": "username-or-empty",
      "problem": "the pain in one-two sentences",
      "mvp_scope": "one sentence: the MVP you'd build",
      "qual_score": 3,
      "qual_notes": "3/4: software-able, MVP-sized, reachable; no budget signal"
    }
  ],
  "sources_scanned": ["brave/...", ...],
  "posts_seen": 87
}

INHERITED FIELDS (every envelope requires these from EnvelopeBase):
- status: "success"
- summary: one-line plain-text description
- artifacts: file paths you wrote

Emit the JSON only.
"""


# ── source fetching ───────────────────────────────────────────────────────────

def _fetch_url(url: str, headers: dict | None = None, timeout: int = 20) -> bytes:
    h = {"User-Agent": UA}
    if headers:
        h.update(headers)
    req = urllib.request.Request(url, headers=h)
    with urllib.request.urlopen(req, timeout=timeout) as r:
        return r.read()


def _fetch_brave(queries: list[str], count: int = 20, freshness: str = "") -> list[dict]:
    """Brave Search API. Returns posts with title/url/body(snippet). Harvests
    BOTH web.results AND discussions.results (Reddit/forum threads - gold for
    lead-gen). No key -> []. Pain signals are evergreen so freshness defaults
    to all-time (niche queries are too rare for a time filter)."""
    key = os.environ.get("BRAVE_API_KEY")
    if not key:
        return []
    posts = []
    for q in queries:
        params = {"q": q, "count": str(count)}
        if freshness:
            params["freshness"] = freshness
        full = "https://api.search.brave.com/res/v1/web/search?" + urllib.parse.urlencode(params)
        try:
            data = json.loads(_fetch_url(full, headers={
                "Accept": "application/json",
                "X-Subscription-Token": key,
            }))
            # web results (articles, blog posts, landing pages)
            web = data.get("web", {}).get("results", [])
            for r in web:
                body = r.get("description", "")
                extra = r.get("extra_snippets") or []
                if extra:
                    body += "\n" + "\n".join(extra[:3])
                posts.append({
                    "source": "brave/web",
                    "title": r.get("title", ""),
                    "author": "",
                    "url": r.get("url", ""),
                    "body": body[:2000],
                })
            # discussions (Reddit, forum threads - where pain signals live)
            disc = data.get("discussions", {}).get("results", [])
            for r in disc:
                posts.append({
                    "source": "brave/discussions",
                    "title": r.get("title", ""),
                    "author": "",
                    "url": r.get("url", ""),
                    "body": r.get("description", "")[:2000],
                })
            print(f"  [brave] {q[:50]!r} -> web={len(web)} discussions={len(disc)}")
        except Exception as e:
            print(f"  [brave] {q[:50]!r}: {e}")
    return posts


def _fetch_hn(query: str, limit: int = 30) -> list[dict]:
    """HN Algolia story search, newest first (search_by_date), last 30 days."""
    import time as _t
    cutoff = int(_t.time()) - 30 * 86400
    base = "https://hn.algolia.com/api/v1/search_by_date"
    if query == "ask" or query == "":
        url = (f"{base}?tags=story&query=Ask+HN&hitsPerPage={limit}"
               f"&numericFilters=created_at_i>{cutoff}")
    else:
        q = urllib.parse.quote(query)
        url = (f"{base}?tags=story&query={q}&hitsPerPage={limit}"
               f"&numericFilters=created_at_i>{cutoff}")
    try:
        data = json.loads(_fetch_url(url))
        posts = []
        for hit in data.get("hits", []):
            if not hit.get("title"):
                continue
            oid = hit.get("objectID", "")
            posts.append({
                "source": "hn",
                "title": hit["title"],
                "author": hit.get("author", ""),
                "url": f"https://news.ycombinator.com/item?id={oid}" if oid else hit.get("url", ""),
                "body": (hit.get("story_text") or "")[:1500],
            })
        return posts
    except Exception as e:
        print(f"  [hn] {query}: {e}")
        return []


def _depth_fetch_browserbase(posts: list[dict], top_n: int = 5) -> list[dict]:
    """Optional: use Browserbase (cloud headless browser) to render the full page
    for the top N posts (by snippet length), enriching the body with the actual
    post text + comments. Skips gracefully if BROWSERBASE_API_KEY not set."""
    if not os.environ.get("BROWSERBASE_API_KEY"):
        return posts
    helper = os.path.join(os.path.dirname(__file__), "..", "tools", "browserbase_fetch.py")
    if not os.path.exists(helper):
        print("  [depth] browserbase_fetch.py not found — skipping depth fetch")
        return posts
    # Pick the top N by body length (the richest snippets get depth-fetched first).
    ranked = sorted(posts, key=lambda p: len(p.get("body", "")), reverse=True)[:top_n]
    enriched = 0
    for p in ranked:
        url = p.get("url", "")
        if not url or "news.ycombinator.com" in url:
            continue  # HN already has body; skip
        try:
            out = subprocess.run(
                ["uv", "run", helper, url],
                capture_output=True, text=True, timeout=60, cwd=os.getcwd())
            if out.returncode == 0 and out.stdout.strip():
                p["body"] = (p.get("body", "") + "\n\n--- FULL PAGE ---\n" + out.stdout)[:4000]
                enriched += 1
        except Exception as e:
            print(f"  [depth] {url[:60]}: {e}")
    print(f"  [depth] enriched {enriched}/{len(ranked)} posts via Browserbase")
    return posts


def _fetch_sources(niche: str, limit: int = 20) -> tuple[list[dict], list[str]]:
    """Fetch posts. Returns (posts, sources_scanned). Brave if key set; else HN."""
    posts, sources = [], []
    if os.environ.get("BRAVE_API_KEY"):
        queries = _generate_queries(niche)
        posts = _fetch_brave(queries, count=limit)
        sources.append("brave")
        if posts:
            posts = _depth_fetch_browserbase(posts)
            return posts, sources
    # Fallback: HN Algolia (no key needed)
    print("  [fetch] no BRAVE_API_KEY (or Brave returned nothing) — falling back to HN")
    posts = _fetch_hn("ask", limit=limit)
    sources.append("hn")
    return posts, sources


def _format_posts(posts: list[dict]) -> str:
    out = []
    for p in posts:
        out.append(f"[{p['source']}] {p['title']}")
        who = p.get("author") or "(unknown author)"
        out.append(f"by {who} | {p['url']}")
        if p.get("body"):
            out.append(p["body"][:1000])
        out.append("---")
    return "\n".join(out)


# ── the ADW ───────────────────────────────────────────────────────────────────

def main(prompt: str, config: str = "adws/adw_sssf_config/sssf.config.yaml",
         adw_id: str | None = None, limit: int = 20) -> int:
    cfg = agents.load_config(config)
    agents.validate(cfg, REQUIRED_AGENTS)
    run = session.ensure(cfg, adw_id)

    niche = prompt.strip()  # the prompt IS the niche (or empty for defaults)

    with run.phase(PhaseParams(name="request", kind="engineer", owner=run.engineer,
                               description="Capture the niche / search focus")) as ph:
        ph.log(input=niche, queries=_generate_queries(niche))

    # 1. Fetch — Brave Search (or HN fallback), write posts to a file the agent reads.
    posts_path = run.session_dir / "context_handoff" / "raw_posts.txt"
    posts_path.parent.mkdir(parents=True, exist_ok=True)
    with run.phase(PhaseParams(name="fetch", kind="code", owner="leadgen",
                               description="Web search for pain signals")) as ph:
        posts, sources = _fetch_sources(niche, limit=limit)
        posts_path.write_text(_format_posts(posts))
        ph.log(sources=sources, posts_fetched=len(posts), file=str(posts_path))
        print(f"[adw_leadgen] fetched {len(posts)} posts from {sources} -> {posts_path}")

    # 2. Harvest — the leadgen agent classifies + scores.
    with run.phase(PhaseParams(name="harvest", kind="agent", owner="leadgen", retries=1,
                               description="Classify posts for software-able problems; score 0-4")) as ph:
        harvest = ph.call(AgentCall(
            output_type=LeadHarvestOutput,
            prompt=(niche + HARVEST_DIRECTIVE.replace("{posts_file}", str(posts_path)))))

    # 3. Emit — insert qualified leads (score >= 2) into the lead_backlog.
    with run.phase(PhaseParams(name="emit", kind="code", owner="backlog",
                               description="Insert qualified leads into the lead_backlog (dedup by id)")) as ph:
        emitted, skipped = [], []
        for lead in harvest.leads:
            if lead.qual_score < 2:
                skipped.append(f"{lead.id}:score={lead.qual_score}")
                continue
            try:
                if run.tracer.lead_add(lead):
                    emitted.append(f"{lead.id}({lead.qual_score}/4)")
                else:
                    skipped.append(f"{lead.id}:dup")
            except Exception as e:  # noqa: BLE001
                skipped.append(f"{lead.id}:!{e}")
        ph.log(emitted=emitted, skipped=skipped,
               candidates=len(harvest.leads), posts_seen=harvest.posts_seen)
        print(f"[adw_leadgen] emitted {len(emitted)} new leads, skipped {len(skipped)} "
              f"(of {len(harvest.leads)} candidates from {harvest.posts_seen} posts)")
        if emitted:
            print("  new leads:")
            for e in emitted:
                print(f"    + {e}")

    return run.finish(accepted=True)


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("prompt", nargs="?", default="",
                        help="niche (e.g. 'small business inventory') or empty for defaults")
    parser.add_argument("--config", default="adws/adw_sssf_config/sssf.config.yaml")
    parser.add_argument("--adw-id", default=None, help="join or pin an existing session")
    parser.add_argument("--limit", type=int, default=20, help="results per query")
    args = parser.parse_args()
    sys.exit(main(utils.resolve_prompt(args.prompt) if args.prompt else "",
                  args.config, args.adw_id, args.limit))