"""Severity escalation and the per-team flood guard. Pure functions."""
from __future__ import annotations
from collections import defaultdict
from typing import Dict, List, Tuple

from .models import Finding

RANK = {"advisory": 0, "watch": 1, "critical": 2}
UNRANK = {v: k for k, v in RANK.items()}


def escalate_multi_rule(findings: List[Finding], rules_for_critical: int) -> List[Finding]:
    """An issue tripping N+ distinct rules is escalated to Critical across its findings."""
    by_issue: Dict[str, List[Finding]] = defaultdict(list)
    for f in findings:
        by_issue[f.issue_id].append(f)
    for issue_id, fs in by_issue.items():
        distinct = {f.rule for f in fs}
        if len(distinct) >= rules_for_critical:
            for f in fs:
                f.severity = "critical"
    return findings


def issue_severity(findings: List[Finding]) -> Dict[str, str]:
    """Highest severity per issue, after escalation."""
    out: Dict[str, int] = {}
    for f in findings:
        out[f.issue_id] = max(out.get(f.issue_id, 0), RANK[f.severity])
    return {k: UNRANK[v] for k, v in out.items()}


def flood_guard(findings: List[Finding], active_counts: Dict[str, int],
                threshold: float) -> Tuple[List[Finding], List[Finding]]:
    """If a (team, rule) pair flags >threshold of that team's active issues, drop the
    itemized findings and emit one aggregate advisory instead."""
    grouped: Dict[Tuple[str, str], List[Finding]] = defaultdict(list)
    for f in findings:
        grouped[(f.team, f.rule)].append(f)

    kept: List[Finding] = []
    aggregates: List[Finding] = []
    for (team, rule), fs in grouped.items():
        active = active_counts.get(team, 0)
        if active and (len(fs) / active) > threshold:
            pct = round(100 * len(fs) / active)
            aggregates.append(Finding(
                issue_id=f"{team}:{rule}", team=team, owner=None,
                rule=f"{rule}__aggregate", severity="advisory",
                detail=f"{pct}% of {team} active issues ({len(fs)}/{active})",
            ))
        else:
            kept.extend(fs)
    return kept, aggregates
