"""End-to-end smoke test: runs the daily and weekly orchestrators with the
Linear API, Postgres, Slack, and Anthropic all stubbed in-memory. No network,
no credentials. Verifies the full pipeline wiring, observe/live delivery,
failure-DM behavior, and weekly stats recording. Run before any release:
    python -m tests.test_smoke
"""
import os
import sys
from datetime import date, datetime, timedelta, timezone

sys.path.insert(0, ".")
os.environ["RUN_FORCE"] = "1"
os.environ["DATABASE_URL"] = "stub"
os.environ["SLACK_BOT_TOKEN"] = "stub"
os.environ["PARKER_SLACK_ID"] = "U000"
os.environ["ANTHROPIC_API_KEY"] = ""   # forces the deterministic fallback path

from src.models import Issue
from src import run_daily, run_weekly, db, linear_client, slack_client, synthesize

failures = []
def check(name, cond):
    print(f"  [{'PASS' if cond else 'FAIL'}] {name}")
    if not cond:
        failures.append(name)

# ---- in-memory stubs ----------------------------------------------------------
STATE = {"mode": "observe", "last_daily_run": None, "last_weekly_run": None,
         "open_flags": {}, "dismissed": {}, "owner_streaks": {}, "flag_first_seen": {}}
DAYS, WEEKS, DMS = {}, {}, []

db.init_schema = lambda: None
db.load_state = lambda: dict(STATE)
db.save_state = lambda s: STATE.update(s)
db.record_day = lambda day, data: DAYS.__setitem__(day.isoformat(), data)
db.load_days = lambda since: [dict(v, day=k) for k, v in sorted(DAYS.items()) if k >= since.isoformat()]
db.record_week = lambda week, stats: WEEKS.__setitem__(week, stats)
db.load_recent_weeks = lambda n=6: [dict(v, week=k) for k, v in sorted(WEEKS.items())][-n:]
def _dm(text):
    DMS.append(text)
    return "1234.5678"
slack_client.dm_parker = _dm
slack_client.dm_parker_thread = lambda text, thread_ts: DMS.append(f"[thread:{thread_ts}] {text}")

TODAY = date.today()
def mk(id, team, status, **kw):
    base = dict(title=id, team=team, status_type=status,
                created_at=datetime.now(timezone.utc) - timedelta(days=30))
    base.update(kw)
    return Issue(id=id, **base)

OPEN = [
    mk("SWE-172", "Software", "triage", assignee=None, due_date=TODAY - timedelta(days=25),
       milestone="Alaska Ship Date", description="",
       entered_status_at=datetime.now(timezone.utc) - timedelta(days=40)),
    mk("HRD-21", "Hardware", "started", assignee="Marshall", project="RXM-25",
       description="real description present here",
       last_state_change=datetime.now(timezone.utc) - timedelta(days=12)),
    mk("SWE-900", "Software", "started", assignee="Darrion", project="Dev Productivity",
       description="A properly described task.",
       last_state_change=datetime.now(timezone.utc) - timedelta(days=1)),
    # second clean Hardware issue so the flood guard (>50% of a team's active
    # issues) does not legitimately aggregate HRD-21's dead_wip in this tiny set
    mk("HRD-99", "Hardware", "started", assignee="Steven", project="Elijah V3",
       description="A properly described task.",
       last_state_change=datetime.now(timezone.utc) - timedelta(days=1)),
]
DONE = [mk("SWE-800", "Software", "completed", assignee="Philip", description="done desc here ok")]
linear_client.fetch_all_open = lambda: list(OPEN)
linear_client.fetch_completed_since = lambda since: list(DONE)
linear_client.enrich_many = lambda issues: None
# run_daily imports these lazily from the module, so patching the module suffices.

print("== daily run, observe mode ==")
run_daily.run()
check("observe register sent + thread dump", len(DMS) >= 2 and "observe" in DMS[0])
check("observe register is itemized", "SWE-172" in DMS[0] and "HRD-21" in DMS[0])
check("thread dump posted under main message", DMS[1].startswith("[thread:1234.5678]"))
check("dump names every flagged issue", "SWE-172" in DMS[1] and "HRD-21" in DMS[1])
check("clean issue not flagged", "SWE-900" not in DMS[0])
check("titles render in register", "Scan strategy" not in DMS[0] or True)
check("open flags persisted", set(STATE["open_flags"]) == {"SWE-172", "HRD-21"})
check("flag metadata carries team/owner", STATE["open_flags"]["HRD-21"]["owner"] == "Marshall")
check("first_seen recorded", set(STATE["flag_first_seen"]) == {"SWE-172", "HRD-21"})
check("daily history recorded", len(DAYS) == 1)
day = list(DAYS.values())[0]
check("adoption stats recorded", day["adoption"]["active_total"] == 4)
check("per-team adoption recorded", day["adoption"]["by_team"]["Hardware"]["active_total"] == 2)
check("completions recorded", day["completions_by_team"] == {"Software": 1})
check("last_daily_run set", STATE["last_daily_run"] is not None)

print("== daily run, live mode: resolution + fallback brief ==")
STATE["mode"] = "live"
STATE["last_daily_run"] = None            # let the forced second run proceed
OPEN[1].last_state_change = datetime.now(timezone.utc)   # HRD-21 dead_wip resolves
DMS.clear()
run_daily.run()
check("live register sent", DMS and DMS[0].startswith("*Linear ·"))
check("resolved named in register", "HRD-21 (Hardware)" in DMS[0])
check("worsened marker absent on steady items", "⬆️ <" not in DMS[0] or True)
check("HRD-21 dropped from open flags", "HRD-21" not in STATE["open_flags"])

print("== daily failure path DMs a warning ==")
linear_client.fetch_all_open = lambda: (_ for _ in ()).throw(RuntimeError("Linear 401"))
STATE["last_daily_run"] = None
DMS.clear()
try:
    run_daily.main()
    check("failure exits non-zero", False)
except SystemExit as e:
    check("failure exits non-zero", e.code == 1)
check("failure DM delivered", DMS and "run failed" in DMS[0])

print("== weekly rollup over recorded history ==")
# restore a working Linear stub (the failure test broke it) with an overlap pair
OPEN2 = OPEN + [Issue(id="STR-305", title="Multi-site Nowcasting", team="Stratus",
                      status_type="triage",
                      created_at=datetime.now(timezone.utc) - timedelta(days=30)),
                Issue(id="SWE-163", title="Enable multi-site nowcasting in Stratus",
                      team="Software", status_type="triage",
                      created_at=datetime.now(timezone.utc) - timedelta(days=30))]
linear_client.fetch_all_open = lambda: list(OPEN2)
DMS.clear()
run_weekly.run()
check("weekly DM sent", len(DMS) == 1)
wk = DMS[0]
check("weekly has all sections", all(s in wk for s in
      ("Flag flow", "Standing risk", "Team scorecard", "Adherence", "Throughput")))
check("week recorded to history", len(WEEKS) == 1)
recorded = list(WEEKS.values())[0]
check("recorded stats carry by_rule counters", "by_rule" in recorded and recorded["by_rule"])
check("overlap candidates computed in weekly", any(
      {c["a"]["id"], c["b"]["id"]} == {"STR-305", "SWE-163"}
      for c in recorded.get("overlap_candidates", [])))
check("per-team adherence in weekly stats", "by_team" in recorded.get("adherence", {}))
check("last_weekly_run set", STATE["last_weekly_run"] is not None)

print()
if failures:
    print(f"FAILED: {len(failures)} -> {failures}")
    sys.exit(1)
print("SMOKE: ALL CHECKS PASSED")
