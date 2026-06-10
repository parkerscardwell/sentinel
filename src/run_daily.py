"""Daily orchestration.

Degrades gracefully by milestone:
  M1 (no DATABASE_URL / no SLACK): full scan + detect + print to console.
  M3 (DB + Slack present): observe mode posts an itemized would-flag summary;
      live mode DMs the synthesized brief.

Run guard: GHA cron is best-effort and routinely starts late, so the guard accepts
any start between 09:00 and 11:59 America/Los_Angeles and dedupes on "already ran
today (PT)". Both cron lines (PDT/PST) may fire; the first valid one wins.
RUN_FORCE=1 bypasses the guard for local/manual runs.

Failure visibility: any crash attempts a "Sentinel run failed" DM before exiting
non-zero, so a broken key or API change is noticed the same morning.
"""
from __future__ import annotations
import os
import sys
import traceback
from collections import Counter, defaultdict
from datetime import datetime, timedelta, timezone
from zoneinfo import ZoneInfo

from . import config, rules, severity, diff
from .models import Finding, Issue

PT = ZoneInfo("America/Los_Angeles")


def _now_pt():
    return datetime.now(PT)


def should_run(now_pt: datetime, last_daily_run) -> bool:
    """Pure guard logic: inside the morning window and not already run today (PT)."""
    lo, hi = config.RUN_WINDOW_HOURS
    if not (lo <= now_pt.hour <= hi):
        return False
    if last_daily_run is not None:
        last = last_daily_run
        if last.tzinfo is None:
            last = last.replace(tzinfo=timezone.utc)
        if last.astimezone(PT).date() == now_pt.date():
            return False
    return True


def _adoption_stats(items):
    n = len(items)
    if not n:
        return {"active_total": 0, "owner_pct": 0, "project_pct": 0,
                "desc_pct": 0, "dated": 0}
    return {
        "active_total": n,
        "owner_pct": round(100 * sum(1 for i in items if i.assignee) / n, 1),
        "project_pct": round(100 * sum(1 for i in items if i.project) / n, 1),
        "desc_pct": round(100 * sum(1 for i in items
                                    if len((i.description or "").strip()) >= 20) / n, 1),
        "dated": sum(1 for i in items if i.due_date),
    }


def _adoption(issues):
    """Field-adoption stats over active (non-terminal) issues — workspace headline
    plus a per-team breakdown — and per-team active counts. These feed the weekly
    adherence section: measured, never flagged per-issue (workspace-wide the fields
    are too sparse; per-issue flags would flood the daily)."""
    active = [i for i in issues if i.status_type not in rules.TERMINAL]
    by_team_issues = defaultdict(list)
    for i in active:
        by_team_issues[i.team].append(i)
    adoption = _adoption_stats(active)
    adoption["by_team"] = {team: _adoption_stats(items)
                           for team, items in by_team_issues.items()}
    return adoption, {team: len(items) for team, items in by_team_issues.items()}


def _collect(issues, active_counts, today):
    findings = []
    for iss in issues:
        applicable = config.RULES_BY_TIER[config.tier_of(iss.team)]
        findings += rules.apply_rules(iss, today, config.THRESHOLDS, applicable)
    findings = severity.escalate_multi_rule(findings, config.THRESHOLDS["rules_for_critical"])
    kept, aggregates = severity.flood_guard(findings, active_counts,
                                            config.THRESHOLDS["flood_guard"])
    return kept, aggregates


def _print_report(kept, aggregates, rec):
    print(f"new={rec['new']}  worsened={rec['worsened']}  resolved={rec['resolved']}")
    by_sev = defaultdict(list)
    for f in kept:
        by_sev[f.severity].append(f)
    for sev in ("critical", "watch", "advisory"):
        if by_sev[sev]:
            print(f"\n{sev.upper()}")
            for f in by_sev[sev]:
                print(f"  • {f.issue_id} [{f.team}] {f.owner or 'unowned'} — "
                      f"{f.rule} ({f.detail})")
    if aggregates:
        print("\nAGGREGATES (held for weekly rollup)")
        for a in aggregates:
            print(f"  • {a.team}: {a.detail}")


def run() -> None:
    now = _now_pt()
    today = now.date()
    has_db = bool(os.environ.get("DATABASE_URL"))
    has_slack = bool(os.environ.get("SLACK_BOT_TOKEN"))

    # ---- state ----
    if has_db:
        from . import db
        db.init_schema()
        state = db.load_state()
    else:
        state = {"mode": "observe", "last_daily_run": None, "open_flags": {},
                 "flag_first_seen": {}}

    if os.environ.get("RUN_FORCE") != "1" and not should_run(now, state.get("last_daily_run")):
        print("guard: outside run window or already ran today; exiting.")
        sys.exit(0)

    from .linear_client import fetch_all_open, fetch_completed_since, enrich_many

    # ---- fetch: full scan of open issues + recent completions ----
    issues = fetch_all_open()
    since = state.get("last_daily_run") or (datetime.now(timezone.utc) - timedelta(days=3))
    completed = fetch_completed_since(since.isoformat())
    completions_by_team = Counter(i.team for i in completed)

    # ---- enrichment for candidates that need real activity timestamps ----
    prior_flags = state.get("open_flags", {}) or {}
    candidates = [i for i in issues
                  if i.status_type in {"started", "triage"} or i.id in prior_flags]
    enrich_many(candidates)

    adoption, active_by_team = _adoption(issues)
    kept, aggregates = _collect(issues + completed, active_by_team, today)

    # ---- diff vs prior ----
    cur_open = diff.current_open(kept)
    # carry team/owner/title onto the open-flag record for weekly grouping and
    # the resolved-by-name footer on later days
    issue_by_id = {i.id: i for i in issues + completed}
    meta = {f.issue_id: (f.team, f.owner) for f in kept}
    for iid, rec_ in cur_open.items():
        team, owner = meta.get(iid, ("?", None))
        rec_["team"], rec_["owner"] = team, owner
        rec_["title"] = issue_by_id[iid].title if iid in issue_by_id else iid
    prior_bare = {k: {"rules": v.get("rules", []), "severity": v.get("severity", 0)}
                  for k, v in prior_flags.items()}
    rec = diff.reconcile(cur_open, prior_bare)
    new_worsened = [f for f in kept if f.issue_id in set(rec["new"]) | set(rec["worsened"])]

    # first-seen dates for flag-age math in the weekly rollup
    first_seen = dict(state.get("flag_first_seen", {}) or {})
    for iid in cur_open:
        first_seen.setdefault(iid, today.isoformat())
    first_seen = {iid: d for iid, d in first_seen.items() if iid in cur_open}

    counts = {"critical": sum(1 for f in new_worsened if f.severity == "critical"),
              "watch": sum(1 for f in new_worsened if f.severity == "watch")}
    standing_unchanged = len(cur_open) - len(rec["new"]) - len(rec["worsened"])
    today_label = now.strftime("%a %b %d").replace(" 0", " ")
    resolved_named = [{"id": iid, "team": prior_flags.get(iid, {}).get("team", "?")}
                      for iid in rec["resolved"]]

    # ---- console mode (M1) ----
    if not (has_db and has_slack):
        print(f"=== Linear Sentinel (console / M1) — {today_label} ===")
        _print_report(kept, aggregates, rec)
        return

    # ---- record the day (feeds the weekly rollup) ----
    by_rule = Counter(f.rule for f in kept)
    by_sev = Counter(f.severity for f in kept)
    db.record_day(today, {
        "flow": {"new": len(rec["new"]), "worsened": len(rec["worsened"]),
                 "resolved": len(rec["resolved"])},
        "open_total": len(cur_open),
        "open_total_prior": len(prior_flags),
        "by_rule": dict(by_rule), "by_severity": dict(by_sev),
        "active_by_team": active_by_team,
        "adoption": adoption,
        "completions_by_team": dict(completions_by_team),
        "aggregates": [f"{a.team}: {a.detail}" for a in aggregates],
        "mode": state.get("mode", "observe"),
    })

    # ---- deliver: summary anchor, then one self-contained message per team
    # (forwardable to that team's lead) with the team's linked dump threaded
    # underneath. Observe and live differ only in the tag. ----
    from .render import (build_records, render_summary, render_team_message,
                         render_dump, chunk, cluster_records, team_order)
    from .slack_client import dm_parker, dm_parker_thread
    records = build_records(kept, issue_by_id)
    new_set, worse_set = set(rec["new"]), set(rec["worsened"])

    head = ""
    if state.get("mode") != "observe":
        from .synthesize import headline
        clusters, _ = cluster_records(records)
        team_counts = Counter(r["team"] for r in records)
        head = headline({
            "counts": {"flagged": len(records), **counts, "resolved": len(rec["resolved"])},
            "by_team": dict(team_counts.most_common(8)),
            "largest_clusters": [
                {"team": c["team"], "group": c["group"], "count": c["count"],
                 "severity": c["severity"], "owner": c["owner"]}
                for c in sorted(clusters, key=lambda c: -c["count"])[:5]],
        })

    observe_mode = state.get("mode") == "observe"
    summary = render_summary(today_label, records, active_by_team, new_set, worse_set,
                             resolved_named, aggregates, headline=head or None,
                             observe=observe_mode)
    for piece in chunk(summary):
        dm_parker(piece)
    recs_by_team = defaultdict(list)
    for r in records:
        recs_by_team[r["team"]].append(r)
    for team in team_order(records):
        msg = render_team_message(team, recs_by_team[team],
                                  active_by_team.get(team, 0),
                                  new_set, worse_set, today_label, observe_mode)
        pieces = chunk(msg)
        ts = dm_parker(pieces[0])
        for piece in pieces[1:]:
            dm_parker(piece)
        if ts:
            for piece in chunk(render_dump(records, new_set, worse_set, only_team=team)):
                dm_parker_thread(piece, ts)

    # ---- save state ----
    state["open_flags"] = cur_open
    state["flag_first_seen"] = first_seen
    state["last_daily_run"] = datetime.now(timezone.utc)
    db.save_state(state)


def main():
    try:
        run()
    except SystemExit:
        raise
    except Exception:
        err = traceback.format_exc()
        print(err, file=sys.stderr)
        try:
            from .slack_client import dm_parker
            dm_parker(f":warning: *Sentinel daily run failed*\n```{err[-1500:]}```")
        except Exception:
            pass
        sys.exit(1)


if __name__ == "__main__":
    main()
