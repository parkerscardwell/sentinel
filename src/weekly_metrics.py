"""Weekly performance statistics. Pure functions over daily_history records,
current open flags, and prior weekly stats — no network, fully unit-testable.

Everything the weekly DM states is computed here in code; the model only phrases.
"""
from __future__ import annotations
import re
from collections import Counter, defaultdict
from datetime import date
from itertools import combinations
from statistics import median
from typing import Dict, List, Optional

from . import config
from .severity import UNRANK


def _arrow(curr: float, prev: Optional[float], higher_is_worse: bool = True) -> str:
    if prev is None:
        return "→"
    if abs(curr - prev) < 1e-9:
        return "→"
    worsening = curr > prev if higher_is_worse else curr < prev
    return "↑" if worsening else "↓"


def compute_week(week_label: str, days: List[dict], open_flags: Dict[str, dict],
                 flag_first_seen: Dict[str, str], today: date,
                 prior_weeks: Optional[List[dict]] = None) -> dict:
    """Assemble the weekly stats object.

    days:            daily_history records for the covered week (oldest first)
    open_flags:      current per-issue flag records {id: {rules, severity, team, owner}}
    flag_first_seen: {issue_id: iso-date when first flagged} for age math
    prior_weeks:     previous weekly stats rows (oldest first) for WoW/trend
    """
    prior_weeks = prior_weeks or []
    prior = prior_weeks[-1] if prior_weeks else None

    # ---- flag flow ----
    new = sum(d.get("flow", {}).get("new", 0) for d in days)
    worsened = sum(d.get("flow", {}).get("worsened", 0) for d in days)
    resolved = sum(d.get("flow", {}).get("resolved", 0) for d in days)
    open_start = days[0].get("open_total_prior", len(open_flags)) if days else len(open_flags)
    denom = open_start + new
    flag_flow = {
        "new": new, "worsened": worsened, "resolved": resolved,
        "net": new - resolved,
        "resolution_rate_pct": round(100 * resolved / denom) if denom else 0,
    }

    # ---- standing risk ----
    ages = []
    oldest = []
    for iid, rec in open_flags.items():
        first = flag_first_seen.get(iid)
        age = (today - date.fromisoformat(first)).days if first else 0
        ages.append(age)
        oldest.append({"id": iid, "team": rec.get("team", "?"), "days": age,
                       "rules": rec.get("rules", []),
                       "severity": UNRANK.get(rec.get("severity", 0), "advisory")})
    oldest.sort(key=lambda o: -o["days"])
    standing_risk = {
        "total_open": len(open_flags),
        "median_age_days": int(median(ages)) if ages else 0,
        "oldest": oldest[:5],
    }

    # ---- team scorecard ----
    latest = days[-1] if days else {}
    active_by_team = latest.get("active_by_team", {})
    flags_by_team = Counter(rec.get("team", "?") for rec in open_flags.values())
    prior_scorecard = {t["team"]: t["pct"] for t in (prior or {}).get("team_scorecard", [])}
    scorecard = []
    for team, flags in flags_by_team.most_common():
        active = active_by_team.get(team, 0)
        pct = round(100 * flags / active) if active else 0
        scorecard.append({"team": team, "flags": flags, "active": active, "pct": pct,
                          "wow": _arrow(pct, prior_scorecard.get(team))})
    scorecard.sort(key=lambda t: -t["pct"])

    # ---- adherence (field adoption — usage discipline over time) ----
    adoption = latest.get("adoption", {})
    prior_ad = (prior or {}).get("adherence", {})

    def _with_wow(curr_stats: dict, prev_stats: dict) -> dict:
        out = {}
        for key in ("owner_pct", "project_pct", "desc_pct"):
            out[key] = curr_stats.get(key, 0)
            if key in prev_stats:
                out[f"{key}_wow"] = round(out[key] - prev_stats[key], 1)
        out["dated"] = curr_stats.get("dated", 0)
        out["active_total"] = curr_stats.get("active_total", 0)
        return out

    adherence = _with_wow(adoption, prior_ad)
    prior_by_team = prior_ad.get("by_team", {})
    by_team_ad = []
    for team, t_stats in (adoption.get("by_team") or {}).items():
        row = _with_wow(t_stats, prior_by_team.get(team, {}))
        row["team"] = team
        by_team_ad.append(row)
    by_team_ad.sort(key=lambda r: r["owner_pct"])   # worst ownership first
    adherence["by_team"] = {r["team"]: {k: v for k, v in r.items() if k != "team"}
                            for r in by_team_ad}
    adherence["by_team_worst_first"] = by_team_ad

    # ---- throughput ----
    completions = Counter()
    for d in days:
        completions.update(d.get("completions_by_team", {}))
    prior_tp = (prior or {}).get("throughput", {})
    throughput = {
        "total": sum(completions.values()),
        "by_team": dict(completions.most_common()),
        "prior_total": prior_tp.get("total"),
    }

    # ---- concentration (DM-only, coaching frame) ----
    by_owner = Counter()
    for rec in open_flags.values():
        by_owner[rec.get("owner") or "unowned"] += 1
    concentration = [{"owner": o, "flags": n} for o, n in by_owner.most_common(3)]

    # ---- aggregates & by-* counters for trend history ----
    agg_seen = []
    for d in days:
        for a in d.get("aggregates", []):
            if a not in agg_seen:
                agg_seen.append(a)
    by_rule = Counter()
    by_severity = Counter()
    for rec in open_flags.values():
        by_severity[UNRANK.get(rec.get("severity", 0), "advisory")] += 1
        for r in rec.get("rules", []):
            by_rule[r] += 1

    # ---- trends (require sustained direction; one bad week is not a regression) ----
    trends = []
    history = prior_weeks + []
    if len(history) >= 3:
        series = defaultdict(list)
        for w in history:
            for t in w.get("team_scorecard", []):
                series[t["team"]].append(t["pct"])
        for team, vals in series.items():
            tail = vals[-3:]
            if len(tail) == 3 and tail[0] < tail[1] < tail[2]:
                trends.append(f"{team} flag rate rising 3 weeks running ({tail[0]}% → {tail[2]}%)")
            elif len(tail) == 3 and tail[0] > tail[1] > tail[2]:
                trends.append(f"{team} flag rate falling 3 weeks running ({tail[0]}% → {tail[2]}%)")

    return {
        "week": week_label, "week_label": week_label,
        "runs_completed": len(days),
        "flag_flow": flag_flow,
        "standing_risk": standing_risk,
        "team_scorecard": scorecard,
        "adherence": adherence,
        "throughput": throughput,
        "concentration": concentration,
        "aggregates": agg_seen,
        "trends": trends,
        "by_team": dict(flags_by_team), "by_rule": dict(by_rule),
        "by_severity": dict(by_severity),
    }


# ---- cross-team overlap candidates ------------------------------------------
# Deterministic pre-filter so the model never goes hunting through raw tickets:
# code nominates the pairs; the model only judges and phrases. Uses the overlap
# coefficient (|shared tokens| / smaller title's tokens) so a short title that is
# a subset of a longer one — "Multi-site Nowcasting" vs "Enable multi-site
# nowcasting in Stratus..." — scores high where Jaccard would not.

_STOP = {
    "the", "a", "an", "and", "or", "of", "in", "on", "for", "to", "with", "is",
    "are", "be", "by", "at", "from", "into", "all", "any", "new", "fix", "add",
    "update", "improve", "make", "implement", "support", "issue", "task",
}


def _title_tokens(title: str) -> frozenset:
    words = re.findall(r"[a-z0-9]+", (title or "").lower())
    return frozenset(w for w in words if len(w) >= 3 and w not in _STOP)


def overlap_candidates(issues, min_shared: int = None, min_coeff: float = None,
                       cap: int = None) -> List[dict]:
    """Cross-team pairs of open issues whose titles share substantial vocabulary.
    `issues` is any iterable of objects with .id, .title, .team, .status_type."""
    min_shared = min_shared or config.OVERLAP_MIN_SHARED_TOKENS
    min_coeff = min_coeff if min_coeff is not None else config.OVERLAP_MIN_COEFFICIENT
    cap = cap or config.OVERLAP_MAX_CANDIDATES

    items = [(i, _title_tokens(i.title)) for i in issues
             if i.status_type in ("triage", "backlog", "unstarted", "started")]
    items = [(i, t) for i, t in items if len(t) >= min_shared]
    pairs = []
    for (a, ta), (b, tb) in combinations(items, 2):
        if a.team == b.team:
            continue
        shared = ta & tb
        if len(shared) < min_shared:
            continue
        coeff = len(shared) / min(len(ta), len(tb))
        if coeff >= min_coeff:
            pairs.append({"a": {"id": a.id, "team": a.team, "title": a.title[:90]},
                          "b": {"id": b.id, "team": b.team, "title": b.title[:90]},
                          "shared": sorted(shared), "score": round(coeff, 2)})
    pairs.sort(key=lambda p: -p["score"])
    return pairs[:cap]
