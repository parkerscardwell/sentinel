"""Linear GraphQL client: full scan of open issues, recent completions, batched enrichment.

Auth: Linear personal API key in the Authorization header.
Endpoint: https://api.linear.app/graphql

Design note (v1.1): the original delta-fetch design could never see issues that went
quiet — dead_wip/abandoned fire on *inactivity*, and inactive issues never appear in
an updatedAt delta. The workspace is small (~700 open issues), so every run does a
full scan of open issues instead. This also makes the flood guard's per-team active
counts correct, and removes the per-identifier standing re-check (whose silent fetch
failures could misreport a flag as resolved).
"""
from __future__ import annotations
import os
import time
from datetime import date, datetime
from typing import Dict, List, Optional

import requests

from .models import Issue

ENDPOINT = "https://api.linear.app/graphql"
OPEN_STATE_TYPES = ("triage", "backlog", "unstarted", "started")
ENRICH_BATCH = 20          # issues per aliased enrichment request
MAX_PAGES = 60             # hard ceiling: 60 * 100 = 6,000 open issues before we refuse


def _headers():
    key = os.environ["LINEAR_API_KEY"]
    return {"Authorization": key, "Content-Type": "application/json"}


def _query(query: str, variables: Optional[dict] = None, retries: int = 5) -> dict:
    """POST with retry/backoff on rate limits (429) and transient server errors (5xx)."""
    delay = 2.0
    last_status = None
    for _ in range(retries):
        r = requests.post(ENDPOINT, json={"query": query, "variables": variables or {}},
                          headers=_headers(), timeout=60)
        last_status = r.status_code
        if r.status_code == 429 or r.status_code >= 500:
            wait = r.headers.get("Retry-After")
            time.sleep(float(wait) if wait else delay)
            delay = min(delay * 2, 60)
            continue
        r.raise_for_status()
        data = r.json()
        if "errors" in data:
            raise RuntimeError(f"Linear GraphQL errors: {data['errors']}")
        return data["data"]
    raise RuntimeError(f"Linear API: gave up after {retries} attempts (last status {last_status})")


def _parse_dt(s: Optional[str]) -> Optional[datetime]:
    if not s:
        return None
    return datetime.fromisoformat(s.replace("Z", "+00:00"))


def _parse_date(s: Optional[str]) -> Optional[date]:
    if not s:
        return None
    return date.fromisoformat(s[:10])


def _map(node: dict) -> Issue:
    state = node.get("state") or {}
    children = (node.get("children") or {}).get("nodes", [])
    has_open = any((c.get("state") or {}).get("type") not in
                   {"completed", "canceled"} for c in children)
    return Issue(
        id=node.get("identifier") or node["id"],
        title=node.get("title", ""),
        team=((node.get("team") or {}).get("name")) or "",
        status_type=state.get("type", ""),
        status_name=state.get("name", ""),
        assignee=((node.get("assignee") or {}) or {}).get("name"),
        due_date=_parse_date(node.get("dueDate")),
        created_at=_parse_dt(node.get("createdAt")),
        started_at=_parse_dt(node.get("startedAt")),
        updated_at=_parse_dt(node.get("updatedAt")),
        project=((node.get("project") or {}) or {}).get("name"),
        milestone=((node.get("projectMilestone") or {}) or {}).get("name"),
        has_open_children=has_open,
        description=node.get("description") or "",
    )


_LIST_FIELDS = """
  id identifier title description dueDate createdAt startedAt updatedAt completedAt
  state { type name }
  assignee { name }
  team { name }
  project { name }
  projectMilestone { name }
  children { nodes { state { type } } }
"""


def _paginate(filter_block: str, variables: dict) -> List[Issue]:
    q = f"""
    query($after: String{', ' + variables.pop('_decl') if '_decl' in variables else ''}) {{
      issues(first: 100, after: $after, filter: {{ {filter_block} }}) {{
        nodes {{ {_LIST_FIELDS} }}
        pageInfo {{ hasNextPage endCursor }}
      }}
    }}"""
    out: List[Issue] = []
    after = None
    for _ in range(MAX_PAGES):
        d = _query(q, {**variables, "after": after})
        conn = d["issues"]
        out += [_map(n) for n in conn["nodes"]]
        if not conn["pageInfo"]["hasNextPage"]:
            return out
        after = conn["pageInfo"]["endCursor"]
    raise RuntimeError(f"pagination exceeded {MAX_PAGES} pages — workspace larger than designed for")


def fetch_all_open() -> List[Issue]:
    """Full scan: every issue in a non-terminal state. ~7 requests at current scale."""
    types = ", ".join(f'"{t}"' for t in OPEN_STATE_TYPES)
    return _paginate(f"state: {{ type: {{ in: [{types}] }} }}", {})


def fetch_completed_since(since_iso: str) -> List[Issue]:
    """Recently completed issues — feeds done_with_open_children and weekly throughput."""
    return _paginate(
        'state: { type: { eq: "completed" } }, updatedAt: { gte: $since }',
        {"since": since_iso, "_decl": "$since: DateTimeOrDuration"})


# ---- enrichment (real activity timestamps) ----------------------------------

def apply_enrichment(issue: Issue, comment_nodes: list, history_nodes: list) -> Issue:
    """Pure function: fold comment/history nodes into the issue's activity fields.

    Sorts in code rather than trusting API ordering — if Linear returns these
    ascending instead of descending, the rules would otherwise read the OLDEST
    activity as the newest and dead_wip would misfire everywhere.
    """
    comments = sorted((c for c in comment_nodes if c.get("createdAt")),
                      key=lambda c: c["createdAt"], reverse=True)
    if comments:
        issue.last_comment_at = _parse_dt(comments[0]["createdAt"])

    events = sorted((h for h in history_nodes if h.get("toState") and h.get("createdAt")),
                    key=lambda h: h["createdAt"], reverse=True)
    if events:
        issue.last_state_change = _parse_dt(events[0]["createdAt"])
        for h in events:  # most recent transition INTO the current status type
            if (h.get("toState") or {}).get("type") == issue.status_type:
                issue.entered_status_at = _parse_dt(h["createdAt"])
                break
    return issue


def enrich_many(issues: List[Issue]) -> None:
    """Batched second pass: ~20 issues per request via GraphQL aliases.

    At current scale (~330 started/triage issues) this is ~17 requests instead of
    330, keeping the run fast and far inside Linear's rate limits.
    """
    by_alias: Dict[str, Issue] = {}
    for batch_start in range(0, len(issues), ENRICH_BATCH):
        batch = issues[batch_start:batch_start + ENRICH_BATCH]
        parts, by_alias = [], {}
        for i, iss in enumerate(batch):
            alias = f"i{i}"
            by_alias[alias] = iss
            safe_id = iss.id.replace('"', "")
            parts.append(f'''{alias}: issue(id: "{safe_id}") {{
              comments(first: 25) {{ nodes {{ createdAt }} }}
              history(first: 50) {{ nodes {{ createdAt toState {{ type }} }} }}
            }}''')
        q = "query {\n" + "\n".join(parts) + "\n}"
        try:
            d = _query(q)
        except Exception:
            continue  # enrichment is best-effort; rules fall back to started_at/created_at
        for alias, iss in by_alias.items():
            node = d.get(alias) or {}
            apply_enrichment(iss,
                             (node.get("comments") or {}).get("nodes", []),
                             (node.get("history") or {}).get("nodes", []))
