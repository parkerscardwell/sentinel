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


HINT_PRIORITY = ("unowned_dated", "unowned_active", "overdue", "dead_wip",
                 "stuck_triage", "abandoned")


def _block_hint(rules_union: Set[str], owner: Optional[str], n: int) -> Optional[str]:
    for rule in HINT_PRIORITY:
        if rule in rules_union:
            return action_hint({rule}, owner, plural=n > 1)
    return None


def _owner_blocks(singles: List[dict], new: Set[str], worsened: Set[str]) -> List[str]:
    """Group an owner's items under one header with one action hint, so ten lines
    ending 'nudge Amie' become one block. Owners with a single item render inline."""
    by_owner: Dict[Optional[str], List[dict]] = defaultdict(list)
    for r in singles:
        by_owner[r["owner"]].append(r)
    groups = sorted(by_owner.items(), key=lambda kv: (-len(kv[1]), kv[0] or "zzz"))
    L: List[str] = []
    for owner, rs in groups:
        rs.sort(key=lambda r: -r["max_age"])
        if len(rs) == 1:
            L += _single_lines(rs[0], new, worsened)
            continue
        name = (owner or "Unowned").split("@")[0]
        rules_union: Set[str] = set().union(*(r["rules"] for r in rs))
        sev = max(r["severity"] for r in rs)
        hint = _block_hint(rules_union, owner, len(rs)) if sev == 2 else None
        L.append(f"{DOT[sev]} *{name} — {len(rs)} items*" + (f"  → {hint}" if hint else ""))
        for r in rs:
            conds = _conds_for(list(r["rule_age"].items()), r["owner"])
            tail = f" · _{r['milestone']}_" if r["milestone"] else ""
            L.append(f"      {_marker(r['id'], new, worsened)}{_link(r['id'])} "
                     f"{_title(r['title'], 56)} — {', '.join(conds)}{tail}")
    return L


def render_team_message(team: str, records: List[dict], active: int,
                        new: Set[str], worsened: Set[str], date_label: str,
                        observe: bool = False) -> str:
    """Self-contained per-team register — forwardable to that team's lead."""
    nc = sum(1 for r in records if r["severity"] == 2)
    n_new = sum(1 for r in records if r["id"] in new)
    n_worse = sum(1 for r in records if r["id"] in worsened)
    mode_tag = " (observe)" if observe else ""
    move = "".join([f" · 🆕 {n_new}" if n_new else "", f" · ⬆️ {n_worse}" if n_worse else ""])
    L = [f"*{team.upper()} · {date_label}*{mode_tag} — {len(records)} flagged / "
         f"{active} active" + (f" · {nc} Critical" if nc else "") + move]
    clusters, singles = cluster_records(records)
    for sev in (2, 1, 0):
        sev_clusters = sorted([c for c in clusters if c["severity"] == sev],
                              key=lambda c: -c["count"])
        sev_singles = [r for r in singles if r["severity"] == sev]
        for c in sev_clusters:
            L += _cluster_lines(c, new, worsened)
        L += _owner_blocks(sev_singles, new, worsened)
    L.append("_Full linked list in thread._")
    return "\n".join(L)


def render_summary(date_label: str, records: List[dict],
                   active_by_team: Dict[str, int],
                   new: Set[str], worsened: Set[str],
                   resolved_named: List[dict], aggregates: List[Finding],
                   headline: Optional[str] = None, observe: bool = False) -> str:
    """The anchor message: totals, headline, per-team index, resolved, aggregates."""
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
    L.append("")
    for team, rs in sorted(by_team.items(),
                           key=lambda kv: (-sum(1 for r in kv[1] if r["severity"] == 2),
                                           -len(kv[1]))):
        nc = sum(1 for r in rs if r["severity"] == 2)
        n_new = sum(1 for r in rs if r["id"] in new)
        L.append(f"• *{team}* — {len(rs)}/{active_by_team.get(team, 0)}"
                 + (f" · {nc} Critical" if nc else "")
                 + (f" · 🆕 {n_new}" if n_new else ""))
    clean = sorted(t for t, n in active_by_team.items() if n and not by_team.get(t))
    if clean:
        L.append(f"✨ Clean: {', '.join(clean)}")
    if aggregates:
        L.append("")
        for a in aggregates:
            L.append(f"⚠️ {a.team}: {a.rule.replace('__aggregate', '').replace('_', ' ')} — {a.detail}")
    L.append("")
    if resolved_named:
        names = " · ".join(f"{r['id']} ({r['team']})" for r in resolved_named)
        L.append(f"✅ *Resolved since last run:* {names}")
    else:
        L.append("✅ Resolved since last run: none")
    L.append("_Team registers follow, one message each — forward to the relevant lead._")
    return "\n".join(L)


def team_order(records: List[dict]) -> List[str]:
    by_team: Dict[str, List[dict]] = defaultdict(list)
    for r in records:
        by_team[r["team"]].append(r)
    return [t for t, _ in sorted(by_team.items(),
            key=lambda kv: (-sum(1 for r in kv[1] if r["severity"] == 2), -len(kv[1])))]


def render_dump(records: List[dict], new: Set[str], worsened: Set[str],
                only_team: Optional[str] = None) -> str:
    """Exhaustive one-line-per-issue list — threads under each team message."""
    if only_team is not None:
        records = [r for r in records if r["team"] == only_team]
    by_team: Dict[str, List[dict]] = defaultdict(list)
    for r in records:
        by_team[r["team"]].append(r)
    L = [f"*Full register — every flagged {only_team} issue:*" if only_team
         else "*Full register — every flagged issue:*"]
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
