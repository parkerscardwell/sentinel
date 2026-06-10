"""Weekly rollup (Mondays 09:00 PT, covering the prior week).

Computes the performance statistics in code (src/weekly_metrics.py), records the
week into weekly_history for trend work, and DMs the rollup. Synthesis uses the
weekly model (Sonnet-class) with a deterministic fallback. Same guard and failure
DM pattern as the daily run. RUN_FORCE=1 bypasses the guard.
"""
from __future__ import annotations
import os
import sys
import traceback
from datetime import datetime, timedelta, timezone
from zoneinfo import ZoneInfo

from . import config
from .weekly_metrics import compute_week, overlap_candidates

PT = ZoneInfo("America/Los_Angeles")


def should_run(now_pt: datetime, last_weekly_run) -> bool:
    lo, hi = config.RUN_WINDOW_HOURS
    if now_pt.weekday() != 0 or not (lo <= now_pt.hour <= hi):
        return False
    if last_weekly_run is not None:
        last = last_weekly_run
        if last.tzinfo is None:
            last = last.replace(tzinfo=timezone.utc)
        if last.astimezone(PT).isocalendar()[:2] == now_pt.isocalendar()[:2]:
            return False
    return True


def run() -> None:
    if not (os.environ.get("DATABASE_URL") and os.environ.get("SLACK_BOT_TOKEN")):
        print("weekly rollup requires DATABASE_URL and SLACK_BOT_TOKEN; exiting.")
        sys.exit(0)

    from . import db
    db.init_schema()
    state = db.load_state()
    now = datetime.now(PT)
    if os.environ.get("RUN_FORCE") != "1" and not should_run(now, state.get("last_weekly_run")):
        print("guard: not Monday morning PT or already ran this week; exiting.")
        sys.exit(0)

    today = now.date()
    week_start = today - timedelta(days=7)
    iso = week_start.isocalendar()
    week_key = f"{iso[0]}-W{iso[1]:02d}"
    week_label = f"{week_key} ({week_start.strftime('%b %d').replace(' 0', ' ')}–" \
                 f"{(today - timedelta(days=1)).strftime('%b %d').replace(' 0', ' ')})"

    days = db.load_days(week_start)
    prior_weeks = [w for w in db.load_recent_weeks(6) if w.get("week") != week_key]

    stats = compute_week(week_label, days, state.get("open_flags", {}) or {},
                         state.get("flag_first_seen", {}) or {}, today, prior_weeks)
    stats["week"] = week_key

    # Cross-team overlap candidates: fresh Monday scan, nominated in code,
    # judged and phrased by the model. Best-effort — never blocks the rollup.
    try:
        from .linear_client import fetch_all_open
        stats["overlap_candidates"] = overlap_candidates(fetch_all_open())
    except Exception:
        stats["overlap_candidates"] = []

    from .synthesize import weekly_brief
    from .slack_client import dm_parker
    dm_parker(weekly_brief(stats))

    db.record_week(week_key, stats)
    state["last_weekly_run"] = datetime.now(timezone.utc)
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
            dm_parker(f":warning: *Sentinel weekly rollup failed*\n```{err[-1500:]}```")
        except Exception:
            pass
        sys.exit(1)


if __name__ == "__main__":
    main()
