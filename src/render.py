"""Deterministic daily register rendering.

The register is rendered entirely in code — never by a model — so completeness is
guaranteed: every flagged issue appears by name, every day, grouped by team.
Identical-signature groups (same team, owner, rule set, severity, and
project/milestone) of CLUSTER_MIN_SIZE+ collapse into one block that names every
ID, so 33 lines of the same problem become 3 with nothing hidden. The exhaustive
one-line-per-issue dump is rendered separately for the Slack thread.
The model contributes only an optional headline sentence at the top.
"""
from __future__ import annotations
from collections import defaultdict
from typing import Dict, List, Optional, Set

from . import config
from .models import Finding, Issue
from .severity import RANK

DOT = {2: "🔴", 1: "🟡", 0: "⚪"}


def issue_url(identifier: str) -> str:
    return f"https://linear.app/{config.LINEAR_WORKSPACE_SLUG}/issue/{identifier}"


def _plain(rule: str, age) -> str:
    a = age if age is not None else "?"
    return {
        "overdue": f"{a}d past due",
        "stuck_triage": f"in Triage {a}d",
        "unowned_dated": "no owner",
        "unowned_active": "no owner",
        "thin_deliverable": "no description",
        "dead_wip": f"in progress, silent {a}d",
        "abandoned": f"untouched {a}d",
        "approaching_not_started": f"due in {a}d, not started",
        "done_with_open_children": "marked Done, sub-issues open",
        "no_project": "no project",
    }.get(rule, rule.replace("_", " "))


def _plain_range(rule: str, ages: List[int]) -> str:
    ages = [a for a in ages if a is not None]
    if not ages or min(ages) == max(ages):
        return _plain(rule, ages[0] if ages else None)
    lo, hi = min(ages), max(ages)
    return {
        "overdue": f"{lo}–{hi}d past due",
        "stuck_triage": f"in Triage {lo}–{hi}d",
        "dead_wip": f"in progress, silent {lo}–{hi}d",
        "abandoned": f"untouched {lo}–{hi}d",
        "approaching_not_started": f"due in {lo}–{hi}d, not started",
    }.get(rule, _plain(rule, hi))


def action_hint(rules_set: Set[str], owner: Optional[str], plural: bool = False) -> Optional[str]:
    first = (owner or "").split("@")[0].split()[0] if owner else None
    if "unowned_dated" in rules_set or "unowned_active" in rules_set:
        return "bulk-assign owners" if plural else "assign an owner"
    if "overdue" in rules_set:
        who = first or "team lead"
        return f"re-date or escalate with {who}"
    if "dead_wip" in rules_set and first:
        return f"nudge {first}"
    if "stuck_triage" in rules_set:
        return "needs a triage sweep" if plural else "needs a triage decision"
    if "abandoned" in rules_set:
        return "confirm still real or close" + ("" if plural else " it")
    return None


def build_records(kept: List[Finding], issue_by_id: Dict[str, Issue]) -> List[dict]:
    """Fold findings into one renderable record per issue."""
    grouped: Dict[str, List[Finding]] = defaultdict(list)
    for f in kept:
        grouped[f.issue_id].append(f)
    records = []
    for iid, fs in grouped.items():
        iss = issue_by_id.get(iid)
        fs.sort(key=lambda f: (-RANK[f.severity], -(f.age_days or 0)))
        rule_age = {}
        for f in fs:
            rule_age.setdefault(f.rule, f.age_days)
        records.append({
            "id": iid,
            "title": (iss.title if iss else iid),
            "team": fs[0].team,
            "owner": fs[0].owner,
            "severity": max(RANK[f.severity] for f in fs),
            "rules": frozenset(f.rule for f in fs),
            "rule_age": rule_age,
            "max_age": max((f.age_days or 0) for f in fs),
            "milestone": (iss.milestone if iss else None),
            "project": (iss.project if iss else None),
        })
    return records


def cluster_records(records: List[dict], min_size: Optional[int] = None):
    """Group identical-signature records. Returns (clusters, singles)."""
    min_size = min_size or config.CLUSTER_MIN_SIZE
    buckets: Dict[tuple, List[dict]] = defaultdict(list)
    for r in records:
        key = (r["team"], r["owner"], r["severity"], r["rules"],
               r["milestone"] or r["project"])
        buckets[key].append(r)
    clusters, singles = [], []
    for (team, owner, sev, rules, group), rs in buckets.items():
        if len(rs) >= min_size:
            ages_by_rule = defaultdict(list)
            for r in rs:
                for rule, age in r["rule_age"].items():
                    ages_by_rule[rule].append(age)
            clusters.append({
                "team": team, "owner": owner, "severity": sev, "rules": rules,
                "group": group, "ids": sorted(r["id"] for r in rs),
                "count": len(rs), "ages_by_rule": dict(ages_by_rule),
                "max_age": max(r["max_age"] for r in rs),
            })
        else:
            singles.extend(rs)
    return clusters, singles


def _marker(iid: str, new: Set[str], worsened: Set[str]) -> str:
    if iid in new:
        return "🆕 "
    if iid in worsened:
        return "⬆️ "
    return ""


def _link(iid: str) -> str:
    return f"<{issue_url(iid)}|{iid}>"


def _title(t: str, cap: int = 64) -> str:
    return t if len(t) <= cap else t[:cap - 1] + "…"


OWNER_RULES = {"unowned_dated", "unowned_active"}


def _conds_for(rule_ages, owner, ranged=False):
    """Condition strings, skipping 'no owner' when the owner column already says unowned."""
    out = []
    for rule, val in rule_ages:
        if rule in OWNER_RULES and not owner:
            continue
        out.append(_plain_range(rule, val) if ranged else _plain(rule, val))
    return out


def _cluster_lines(c: dict, new: Set[str], worsened: Set[str]) -> List[str]:
    conds = _conds_for(sorted(c["ages_by_rule"].items()), c["owner"], ranged=True)
    label = c["group"] or "no project"
    owner = (c["owner"] or "unowned").split("@")[0]
    n_new = sum(1 for i in c["ids"] if i in new)
    tag = f" · 🆕 {n_new} new" if 0 < n_new < c["count"] else ("🆕 " if n_new == c["count"] else "")
    head = (f"{DOT[c['severity']]} {tag if n_new == c['count'] else ''}*{label} — "
            f"{c['count']} issues* — {owner} · {', '.join(conds)}"
            f"{tag if 0 < n_new < c['count'] else ''}")
    # plain IDs here — linked one-per-line versions are in the thread dump;
    # full URLs would blow past Slack's message size cap on heavy days
    ids = ", ".join(c["ids"])
    lines = [head, f"      {ids}"]
    if c["severity"] == 2:
        h = action_hint(c["rules"], c["owner"], plural=True)
        if h:
            lines.append(f"      → {h}")
    return lines


def _single_lines(r: dict, new: Set[str], worsened: Set[str]) -> List[str]:
    conds = _conds_for(list(r["rule_age"].items()), r["owner"])
    owner = (r["owner"] or "unowned").split("@")[0]
    tail = f" · _{r['milestone']}_" if r["milestone"] else ""
    lines = [f"{DOT[r['severity']]} {_marker(r['id'], new, worsened)}{_link(r['id'])} "
             f"{_title(r['title'])} — {owner} · {', '.join(conds)}{tail}"]
    if r["severity"] == 2:
        h = action_hint(r["rules"], r["owner"])
        if h:
            lines.append(f"      → {h}")
    return lines


def render_register(date_label: str, records: List[dict],
                    active_by_team: Dict[str, int],
                    new: Set[str], worsened: Set[str],
                    resolved_named: List[dict], aggregates: List[Finding],
                    headline: Optional[str] = None, observe: bool = False) -> str:
    by_team: Dict[str, List[dict]] = defaultdict(list)
    for r in records:
        by_team[r["team"]].append(r)
    crit = sum(1 for r in records if r["severity"] == 2)
    watch = sum(1 for r in records if r["severity"] == 1)

    mode_tag = " (observe — would flag)" if observe else ""
    L = [f"*Linear · {date_label}*{mode_tag} — {len(records)} flagged across "
         f"{len(by_team)} teams · {crit} Critical · {watch} Watch · "
         f"🆕 {len(new)} new · ⬆️ {len(worsened)} worsened · {len(resolved_named)} resolved"]
    if headline:
        L += ["", f"_{headline}_"]

    team_order = sorted(by_team.items(),
                        key=lambda kv: (-sum(1 for r in kv[1] if r["severity"] == 2),
                                        -len(kv[1])))
    for team, rs in team_order:
        nc = sum(1 for r in rs if r["severity"] == 2)
        L += ["", f"*{team.upper()}* — {len(rs)} flagged / {active_by_team.get(team, 0)} active"
              + (f" · {nc} Critical" if nc else "")]
        clusters, singles = cluster_records(rs)
        for sev in (2, 1, 0):
            for c in sorted([c for c in clusters if c["severity"] == sev],
                            key=lambda c: -c["count"]):
                L += _cluster_lines(c, new, worsened)
            for r in sorted([r for r in singles if r["severity"] == sev],
                            key=lambda r: -r["max_age"]):
                L += _single_lines(r, new, worsened)

    if aggregates:
        L += ["", "*Workspace-wide (flood-guarded; detail in Monday rollup)*"]
        for a in aggregates:
            L.append(f"• {a.team}: {a.rule.replace('__aggregate', '').replace('_', ' ')} — {a.detail}")

    L.append("")
    if resolved_named:
        names = " · ".join(f"{r['id']} ({r['team']})" for r in resolved_named)
        L.append(f"✅ *Resolved since last run:* {names}")
    else:
        L.append("✅ Resolved since last run: none")
    L.append("_Full per-issue list in thread._")
    return "\n".join(L)


def render_dump(records: List[dict], new: Set[str], worsened: Set[str]) -> str:
    """Exhaustive one-line-per-issue list, grouped by team — for the thread."""
    by_team: Dict[str, List[dict]] = defaultdict(list)
    for r in records:
        by_team[r["team"]].append(r)
    L = ["*Full register — every flagged issue:*"]
    for team in sorted(by_team, key=lambda t: (-sum(1 for r in by_team[t] if r["severity"] == 2),
                                               -len(by_team[t]))):
        L.append(f"\n*{team.upper()}*")
        for r in sorted(by_team[team], key=lambda r: (-r["severity"], -r["max_age"])):
            conds = _conds_for(list(r["rule_age"].items()), r["owner"])
            owner = (r["owner"] or "unowned").split("@")[0]
            tail = f" · _{r['milestone']}_" if r["milestone"] else ""
            L.append(f"{DOT[r['severity']]} {_marker(r['id'], new, worsened)}{_link(r['id'])} "
                     f"{_title(r['title'], 70)} — {owner} · {', '.join(conds)}{tail}")
    return "\n".join(L)


def chunk(text: str, limit: int = 3800) -> List[str]:
    """Split on line boundaries for Slack-friendly thread messages."""
    out, cur = [], ""
    for line in text.split("\n"):
        if cur and len(cur) + len(line) + 1 > limit:
            out.append(cur)
            cur = line
        else:
            cur = f"{cur}\n{line}" if cur else line
    if cur:
        out.append(cur)
    return out
