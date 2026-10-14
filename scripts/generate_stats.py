#!/usr/bin/env python3
"""Generate GitHub profile stats cards as static SVGs.

Standard library only: no third-party code runs with the token.

Reads GITHUB_TOKEN (and optionally GITHUB_LOGIN) from the environment and
writes stats/overview.svg and stats/languages.svg. With a token that can read
private repositories, private work is included as *aggregate numbers only*:
no private repository name is ever written to the output.

Env:
  GITHUB_TOKEN        required
  GITHUB_LOGIN        default: the token's own login
  EXCLUDE_LANGUAGES   comma-separated, e.g. "HTML,CSS" (default: none)
  OUT_DIR             default: stats
"""

from __future__ import annotations

import datetime as dt
import html
import json
import os
import sys
import time
import urllib.error
import urllib.request
from pathlib import Path

API = "https://api.github.com/graphql"


def gql(token: str, query: str, variables: dict | None = None) -> dict:
    body = json.dumps({"query": query, "variables": variables or {}}).encode()
    req = urllib.request.Request(
        API,
        data=body,
        headers={
            "Authorization": f"bearer {token}",
            "Content-Type": "application/json",
            "User-Agent": "profile-stats-generator",
        },
    )
    for attempt in range(4):
        try:
            with urllib.request.urlopen(req, timeout=60) as resp:  # noqa: S310 (fixed https URL)
                payload = json.load(resp)
            break
        except urllib.error.HTTPError as exc:
            # GitHub returns 502/503/504 when an expensive query times out.
            if exc.code not in (502, 503, 504) or attempt == 3:
                raise
            time.sleep(2 * (attempt + 1))
    if payload.get("errors"):
        raise RuntimeError(f"GraphQL error: {payload['errors']}")
    return payload["data"]


def viewer_login(token: str) -> str:
    return gql(token, "query { viewer { login } }")["viewer"]["login"]


def fetch_repos(token: str, login: str, uid: str) -> list[dict]:
    query = """
    query($login: String!, $after: String, $uid: ID!) {
      user(login: $login) {
        repositories(ownerAffiliations: OWNER, isFork: false, first: 20, after: $after) {
          pageInfo { hasNextPage endCursor }
          nodes {
            isPrivate
            stargazerCount
            defaultBranchRef { target { ... on Commit { history(author: {id: $uid}) { totalCount } } } }
            languages(first: 20, orderBy: {field: SIZE, direction: DESC}) {
              edges { size node { name color } }
            }
          }
        }
      }
    }"""
    repos: list[dict] = []
    after = None
    while True:
        data = gql(token, query, {"login": login, "after": after, "uid": uid})
        page = data["user"]["repositories"]
        repos.extend(page["nodes"])
        if not page["pageInfo"]["hasNextPage"]:
            return repos
        after = page["pageInfo"]["endCursor"]


def fetch_user(token: str, login: str) -> dict:
    return gql(
        token,
        "query($l: String!) { user(login: $l) { id pullRequests { totalCount } issues { totalCount } } }",
        {"l": login},
    )["user"]


def fetch_contributions(token: str, login: str) -> dict:
    """All-time contribution totals, summed year by year since account creation."""
    created = gql(
        token, "query($l: String!) { user(login: $l) { createdAt } }", {"l": login}
    )["user"]["createdAt"]
    start_year = int(created[:4])
    now = dt.datetime.now(dt.timezone.utc)
    query = """
    query($login: String!, $from: DateTime!, $to: DateTime!) {
      user(login: $login) {
        contributionsCollection(from: $from, to: $to) {
          totalCommitContributions
          restrictedContributionsCount
          totalPullRequestContributions
          totalIssueContributions
          totalPullRequestReviewContributions
          contributionCalendar { totalContributions }
        }
      }
    }"""
    totals = {"commits": 0, "private": 0, "prs": 0, "issues": 0, "reviews": 0, "year": 0}
    for year in range(start_year, now.year + 1):
        frm = dt.datetime(year, 1, 1, tzinfo=dt.timezone.utc)
        to = min(dt.datetime(year, 12, 31, 23, 59, 59, tzinfo=dt.timezone.utc), now)
        c = gql(token, query, {"login": login, "from": frm.isoformat(), "to": to.isoformat()})[
            "user"
        ]["contributionsCollection"]
        totals["commits"] += c["totalCommitContributions"]
        totals["private"] += c["restrictedContributionsCount"]
        totals["prs"] += c["totalPullRequestContributions"]
        totals["issues"] += c["totalIssueContributions"]
        totals["reviews"] += c["totalPullRequestReviewContributions"]
        if year == now.year:
            totals["year"] = c["contributionCalendar"]["totalContributions"]
    return totals


STYLE = """
<style>
  :root { --bg:#ffffff; --border:#d0d7de; --fg:#1f2328; --muted:#59636e; --accent:#0969da; --track:#eaeef2; }
  @media (prefers-color-scheme: dark) {
    :root { --bg:#0d1117; --border:#30363d; --fg:#e6edf3; --muted:#9198a1; --accent:#4493f8; --track:#21262d; }
  }
  .bg { fill: var(--bg); stroke: var(--border); }
  .title { font: 600 16px 'Segoe UI', Ubuntu, -apple-system, sans-serif; fill: var(--accent); }
  .label { font: 400 13px 'Segoe UI', Ubuntu, -apple-system, sans-serif; fill: var(--muted); }
  .value { font: 600 13px 'Segoe UI', Ubuntu, -apple-system, sans-serif; fill: var(--fg); }
  .foot  { font: 400 10px 'Segoe UI', Ubuntu, -apple-system, sans-serif; fill: var(--muted); }
  .track { fill: var(--track); }
</style>"""


def fmt(n: int) -> str:
    return f"{n / 1000:.1f}k" if n >= 10_000 else f"{n:,}"


def overview_svg(login: str, stats: dict, updated: str) -> str:
    rows = [
        ("Total stars earned (public repos)", stats["stars"]),
        ("Commits (all time)", stats["commits"]),
        ("Pull requests", stats["prs"]),
        ("Issues", stats["issues"]),
        ("Contributions this year", stats["year"]),
        ("Repositories (public / private)", f"{stats['public_repos']} / {stats['private_repos']}"),
    ]
    h = 70 + len(rows) * 25 + 20
    out = [
        f'<svg xmlns="http://www.w3.org/2000/svg" width="420" height="{h}" viewBox="0 0 420 {h}" role="img" aria-labelledby="t">',
        f'<title id="t">{html.escape(login)} GitHub stats</title>',
        STYLE,
        f'<rect class="bg" x="0.5" y="0.5" width="419" height="{h - 1}" rx="6"/>',
        f'<text class="title" x="24" y="36">{html.escape(login)} · GitHub stats</text>',
    ]
    for i, (label, value) in enumerate(rows):
        y = 70 + i * 25
        v = fmt(value) if isinstance(value, int) else value
        out.append(f'<text class="label" x="24" y="{y}">{html.escape(label)}</text>')
        out.append(f'<text class="value" x="396" y="{y}" text-anchor="end">{html.escape(str(v))}</text>')
    out.append(f'<text class="foot" x="24" y="{h - 14}">Updated {updated} · includes private work as totals only</text>')
    out.append("</svg>")
    return "\n".join(out)


def languages_svg(langs: list[tuple[str, int, str]], updated: str, excluded: list[str]) -> str:
    total = sum(size for _, size, _ in langs) or 1
    top = langs[:8]
    h = 70 + 18 + ((len(top) + 1) // 2) * 24 + 26 + (16 if excluded else 0)
    out = [
        f'<svg xmlns="http://www.w3.org/2000/svg" width="420" height="{h}" viewBox="0 0 420 {h}" role="img" aria-labelledby="t">',
        '<title id="t">Most used languages</title>',
        STYLE,
        f'<rect class="bg" x="0.5" y="0.5" width="419" height="{h - 1}" rx="6"/>',
        '<text class="title" x="24" y="36">Most used languages</text>',
        '<rect class="track" x="24" y="52" width="372" height="8" rx="4"/>',
    ]
    x = 24.0
    for name, size, color in top:
        w = 372 * size / total
        out.append(f'<rect x="{x:.2f}" y="52" width="{w:.2f}" height="8" fill="{color}"/>')
        x += w
    for i, (name, size, color) in enumerate(top):
        col, row = i % 2, i // 2
        cx, cy = 24 + col * 190, 88 + row * 24
        pct = 100 * size / total
        out.append(f'<circle cx="{cx + 5}" cy="{cy - 4}" r="5" fill="{color}"/>')
        out.append(f'<text class="value" x="{cx + 16}" y="{cy}">{html.escape(name)}</text>')
        out.append(f'<text class="label" x="{cx + 176}" y="{cy}" text-anchor="end">{pct:.1f}%</text>')
    foot_y = h - 30 if excluded else h - 14
    out.append(f'<text class="foot" x="24" y="{foot_y}">Updated {updated} · by code size across owned, non-fork repos</text>')
    if excluded:
        out.append(f'<text class="foot" x="24" y="{h - 14}">Excludes markup/templates: {html.escape(", ".join(excluded))}</text>')
    out.append("</svg>")
    return "\n".join(out)


def main() -> int:
    token = os.environ.get("GITHUB_TOKEN")
    if not token:
        print("GITHUB_TOKEN is not set", file=sys.stderr)
        return 1
    login = os.environ.get("GITHUB_LOGIN") or viewer_login(token)
    excluded = [s.strip() for s in os.environ.get("EXCLUDE_LANGUAGES", "").split(",") if s.strip()]
    exclude = {s.lower() for s in excluded}
    out_dir = Path(os.environ.get("OUT_DIR", "stats"))

    user = fetch_user(token, login)
    repos = fetch_repos(token, login, user["id"])
    contrib = fetch_contributions(token, login)

    lang_sizes: dict[str, int] = {}
    lang_colors: dict[str, str] = {}
    for repo in repos:
        for edge in repo["languages"]["edges"]:
            name = edge["node"]["name"]
            if name.lower() in exclude:
                continue
            lang_sizes[name] = lang_sizes.get(name, 0) + edge["size"]
            lang_colors[name] = edge["node"]["color"] or "#8b949e"
    langs = sorted(((n, s, lang_colors[n]) for n, s in lang_sizes.items()), key=lambda t: -t[1])

    stats = {
        "stars": sum(r["stargazerCount"] for r in repos if not r["isPrivate"]),
        "public_repos": sum(1 for r in repos if not r["isPrivate"]),
        "private_repos": sum(1 for r in repos if r["isPrivate"]),
        **contrib,
        "repo_commits": sum(
            ((r.get("defaultBranchRef") or {}).get("target") or {}).get("history", {}).get("totalCount", 0)
            for r in repos
        ),
        "user_prs": user["pullRequests"]["totalCount"],
        "user_issues": user["issues"]["totalCount"],
    }
    updated = dt.datetime.now(dt.timezone.utc).strftime("%Y-%m-%d")
    print("totals: " + " ".join(f"{k}={v}" for k, v in stats.items()))

    out_dir.mkdir(parents=True, exist_ok=True)
    (out_dir / "overview.svg").write_text(overview_svg(login, stats, updated) + "\n")
    (out_dir / "languages.svg").write_text(languages_svg(langs, updated, excluded) + "\n")
    print(f"Wrote {out_dir}/overview.svg and {out_dir}/languages.svg for {login}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
