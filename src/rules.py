"""Deterministic detection rules. Pure functions, no model, no network.

Each rule takes (issue, today, th) and returns a Finding or None.
`today` is a date; `th` is config.THRESHOLDS.
"""
from __future__ import annotations
from datetime import date, datetime
from typing import Optional

from .models import Issue, Finding

TERMINAL = {"completed", "canceled", "duplicate"}


def _days_since(dt, today: date) -> Optional[int]:
    if dt is None:
        return None
    d = dt.date() if isinstance(dt, datetime) else dt
    return (today - d).days


def _last_real_activity(issue: Issue):
    """Genuine activity = state change or comment. updated_at is NOT trusted
    (bulk edits inflate it). Falls back to started_at only if nothing else exists."""
    candidates = [issue.last_state_change, issue.last_comment_at]
    candidates = [c for c in candidates if c is not None]
    if candidates:
        return max(candidates)
    return issue.started_at


def _entered_status(issue: Issue):
    return issue.entered_status_at or issue.started_at or issue.created_at


# ---- rules -----------------------------------------------------------------

def overdue(issue, today, th):
    if issue.due_date and issue.due_date < today and issue.status_type not in TERMINAL:
        days = (today - issue.due_date).days
        sev = "critical" if issue.assignee is None else "watch"
        return Finding(issue.id, issue.team, issue.assignee, "overdue", sev,
                       age_days=days, detail=f"{days}d overdue")


def approaching_not_started(issue, today, th):
    if issue.due_date and issue.status_type in {"backlog", "unstarted", "triage"}:
        delta = (issue.due_date - today).days
        if 0 <= delta <= th["approaching_days"]:
            return Finding(issue.id, issue.team, issue.assignee, "approaching_not_started",
                           "watch", age_days=delta, detail=f"due in {delta}d, not started")


def stuck_triage(issue, today, th):
    if issue.status_type == "triage":
        age = _days_since(_entered_status(issue), today)
        if age is not None and age > th["stuck_triage_days"]:
            return Finding(issue.id, issue.team, issue.assignee, "stuck_triage",
                           "watch", age_days=age, detail=f"{age}d in Triage")


def unowned_active(issue, today, th):
    if issue.status_type in {"started", "unstarted"} and issue.assignee is None:
        return Finding(issue.id, issue.team, None, "unowned_active", "watch",
                       detail="active work, no owner")


def unowned_dated(issue, today, th):
    if issue.assignee is None and issue.due_date is not None and issue.status_type not in TERMINAL:
        return Finding(issue.id, issue.team, None, "unowned_dated", "watch",
                       detail="dated, no owner")


def done_with_open_children(issue, today, th):
    if issue.status_type == "completed" and issue.has_open_children:
        return Finding(issue.id, issue.team, issue.assignee, "done_with_open_children",
                       "watch", detail="Done with open sub-issues")


def dead_wip(issue, today, th):
    if issue.status_type != "started":
        return None
    last = _last_real_activity(issue)
    age = _days_since(last, today)
    if age is not None and age >= th["dead_wip_days"]:
        sev = "critical" if (issue.due_date or issue.milestone) else "watch"
        return Finding(issue.id, issue.team, issue.assignee, "dead_wip", sev,
                       age_days=age, detail=f"In Progress, no activity {age}d")


def abandoned(issue, today, th):
    if issue.status_type in TERMINAL:
        return None
    last = _last_real_activity(issue) or issue.created_at
    age = _days_since(last, today)
    if age is not None and age >= th["abandoned_days"]:
        return Finding(issue.id, issue.team, issue.assignee, "abandoned", "watch",
                       age_days=age, detail=f"no activity {age}d")


def no_project(issue, today, th):
    if issue.status_type in {"started", "unstarted"} and not issue.project:
        return Finding(issue.id, issue.team, issue.assignee, "no_project", "advisory",
                       detail="active, no project")


def thin_deliverable(issue, today, th):
    dated = issue.due_date is not None or issue.milestone
    if dated and len((issue.description or "").strip()) < 20 and issue.status_type not in TERMINAL:
        return Finding(issue.id, issue.team, issue.assignee, "thin_deliverable", "advisory",
                       detail="dated deliverable, empty description")


REGISTRY = {
    "overdue": overdue,
    "approaching_not_started": approaching_not_started,
    "stuck_triage": stuck_triage,
    "unowned_active": unowned_active,
    "unowned_dated": unowned_dated,
    "done_with_open_children": done_with_open_children,
    "dead_wip": dead_wip,
    "abandoned": abandoned,
    "no_project": no_project,
    "thin_deliverable": thin_deliverable,
}


def apply_rules(issue, today, th, applicable):
    """Run only the rules applicable to this issue's tier; skip fresh + terminal items."""
    age_h = None
    if issue.created_at is not None:
        age_h = (datetime.combine(today, datetime.min.time()) - issue.created_at.replace(tzinfo=None)).total_seconds() / 3600
    if age_h is not None and age_h < th["grace_hours"]:
        return []
    out = []
    for name in applicable:
        fn = REGISTRY.get(name)
        if fn is None:
            continue
        f = fn(issue, today, th)
        if f is not None:
            out.append(f)
    return out
