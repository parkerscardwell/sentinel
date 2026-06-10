"""Reconcile this run's findings against prior state: new / worsened / resolved."""
from __future__ import annotations
from collections import defaultdict
from typing import Dict, List

from .models import Finding
from .severity import RANK


def current_open(findings: List[Finding]) -> Dict[str, dict]:
    """Collapse findings into a per-issue open-flag record."""
    rules = defaultdict(set)
    sev = {}
    for f in findings:
        rules[f.issue_id].add(f.rule)
        sev[f.issue_id] = max(sev.get(f.issue_id, 0), RANK[f.severity])
    return {
        iid: {"rules": sorted(rs), "severity": sev[iid]}
        for iid, rs in rules.items()
    }


def reconcile(current: Dict[str, dict], prior: Dict[str, dict]):
    new, worsened, resolved = [], [], []
    for iid, cur in current.items():
        if iid not in prior:
            new.append(iid)
        else:
            p = prior[iid]
            grew = set(cur["rules"]) - set(p.get("rules", []))
            higher = cur["severity"] > p.get("severity", 0)
            if grew or higher:
                worsened.append(iid)
    for iid in prior:
        if iid not in current:
            resolved.append(iid)
    return {"new": new, "worsened": worsened, "resolved": resolved}
