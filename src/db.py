"""Private state store (serverless Postgres, e.g. Neon). Connection via DATABASE_URL.

Tables:
  sentinel_state  — single row: mode, run timestamps, open flags
  daily_history   — one row per daily run: counters, adoption stats, completions
  weekly_history  — one row per ISO week: the computed weekly stats

init_schema() is the single source of truth for the schema and is safe to run on
every start (CREATE/ALTER IF NOT EXISTS). schema.sql is kept only as reference.
"""
from __future__ import annotations
import json
import os
from datetime import date, datetime, timedelta
from typing import List, Optional

import psycopg

SINGLETON = "singleton"


def _conn():
    return psycopg.connect(os.environ["DATABASE_URL"])


def init_schema() -> None:
    ddl = """
    create table if not exists sentinel_state (
        id text primary key default 'singleton',
        mode text not null default 'observe',
        last_daily_run timestamptz,
        last_weekly_run timestamptz,
        open_flags jsonb not null default '{}'::jsonb,
        dismissed jsonb not null default '{}'::jsonb,
        owner_streaks jsonb not null default '{}'::jsonb,
        updated_at timestamptz not null default now()
    );
    create table if not exists daily_history (
        day date primary key,
        data jsonb not null,
        recorded_at timestamptz not null default now()
    );
    create table if not exists weekly_history (
        week text primary key,
        by_team jsonb not null default '{}'::jsonb,
        by_rule jsonb not null default '{}'::jsonb,
        by_severity jsonb not null default '{}'::jsonb,
        recorded_at timestamptz not null default now()
    );
    alter table weekly_history add column if not exists stats jsonb;
    alter table sentinel_state add column if not exists flag_first_seen jsonb not null default '{}'::jsonb;
    insert into sentinel_state (id) values ('singleton')
        on conflict (id) do nothing;
    """
    with _conn() as c, c.cursor() as cur:
        cur.execute(ddl)
        c.commit()


def load_state() -> dict:
    with _conn() as c, c.cursor() as cur:
        cur.execute("select mode, last_daily_run, last_weekly_run, open_flags, "
                    "dismissed, owner_streaks, flag_first_seen "
                    "from sentinel_state where id=%s", (SINGLETON,))
        row = cur.fetchone()
    if not row:
        return {"mode": "observe", "last_daily_run": None, "last_weekly_run": None,
                "open_flags": {}, "dismissed": {}, "owner_streaks": {}, "flag_first_seen": {}}
    mode, ldr, lwr, of, dis, streaks, first_seen = row
    return {"mode": mode, "last_daily_run": ldr, "last_weekly_run": lwr,
            "open_flags": of, "dismissed": dis, "owner_streaks": streaks,
            "flag_first_seen": first_seen or {}}


def save_state(state: dict) -> None:
    with _conn() as c, c.cursor() as cur:
        cur.execute(
            "update sentinel_state set mode=%s, last_daily_run=%s, last_weekly_run=%s, "
            "open_flags=%s, dismissed=%s, owner_streaks=%s, flag_first_seen=%s, "
            "updated_at=now() where id=%s",
            (state.get("mode", "observe"), state.get("last_daily_run"),
             state.get("last_weekly_run"), json.dumps(state.get("open_flags", {})),
             json.dumps(state.get("dismissed", {})), json.dumps(state.get("owner_streaks", {})),
             json.dumps(state.get("flag_first_seen", {})), SINGLETON))
        c.commit()


def set_mode(mode: str) -> None:
    if mode not in ("observe", "live"):
        raise ValueError("mode must be 'observe' or 'live'")
    with _conn() as c, c.cursor() as cur:
        cur.execute("update sentinel_state set mode=%s, updated_at=now() where id=%s",
                    (mode, SINGLETON))
        c.commit()


def record_day(day: date, data: dict) -> None:
    with _conn() as c, c.cursor() as cur:
        cur.execute(
            "insert into daily_history (day, data) values (%s,%s) "
            "on conflict (day) do update set data=excluded.data, recorded_at=now()",
            (day, json.dumps(data)))
        c.commit()


def load_days(since: date) -> List[dict]:
    """Daily records on/after `since`, oldest first. Each dict gains a 'day' key."""
    with _conn() as c, c.cursor() as cur:
        cur.execute("select day, data from daily_history where day >= %s order by day", (since,))
        rows = cur.fetchall()
    out = []
    for day, data in rows:
        d = dict(data)
        d["day"] = day.isoformat()
        out.append(d)
    return out


def record_week(week: str, stats: dict) -> None:
    with _conn() as c, c.cursor() as cur:
        cur.execute(
            "insert into weekly_history (week, by_team, by_rule, by_severity, stats) "
            "values (%s,%s,%s,%s,%s) on conflict (week) do update set "
            "by_team=excluded.by_team, by_rule=excluded.by_rule, "
            "by_severity=excluded.by_severity, stats=excluded.stats, recorded_at=now()",
            (week, json.dumps(stats.get("by_team", {})), json.dumps(stats.get("by_rule", {})),
             json.dumps(stats.get("by_severity", {})), json.dumps(stats)))
        c.commit()


def load_recent_weeks(n: int = 6) -> List[dict]:
    """Most recent n weekly stats rows, oldest first. Each gains a 'week' key."""
    with _conn() as c, c.cursor() as cur:
        cur.execute("select week, stats from weekly_history order by week desc limit %s", (n,))
        rows = cur.fetchall()
    out = []
    for week, stats in reversed(rows):
        d = dict(stats or {})
        d["week"] = week
        out.append(d)
    return out
