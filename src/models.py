"""Core data shapes. Dependency-free so the rule engine is unit-testable in isolation."""
from __future__ import annotations
from dataclasses import dataclass
from datetime import date, datetime
from typing import Optional


@dataclass
class Issue:
    id: str
    title: str
    team: str
    status_type: str  # triage|backlog|unstarted|started|completed|canceled|duplicate
    status_name: str = ""
    assignee: Optional[str] = None
    due_date: Optional[date] = None
    created_at: Optional[datetime] = None
    started_at: Optional[datetime] = None
    updated_at: Optional[datetime] = None
    # Enriched fields (second pass). updated_at is deliberately NOT trusted for activity.
    last_state_change: Optional[datetime] = None
    last_comment_at: Optional[datetime] = None
    entered_status_at: Optional[datetime] = None
    project: Optional[str] = None
    milestone: Optional[str] = None
    has_open_children: bool = False
    description: str = ""
    duplicate_of: Optional[str] = None
    due_date_changes: int = 0


@dataclass
class Finding:
    issue_id: str
    team: str
    owner: Optional[str]
    rule: str
    severity: str  # critical|watch|advisory
    age_days: Optional[int] = None
    detail: str = ""
