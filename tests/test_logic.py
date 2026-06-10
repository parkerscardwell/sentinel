"""Synthetic verification of the rule engine, severity escalation, flood guard, diff.
Runs on stdlib only (no network, no DB)."""
import sys
from datetime import date, datetime, timedelta

sys.path.insert(0, ".")
from src.models import Issue
from src import config, rules, severity, diff

TODAY = date(2026, 6, 9)
TH = config.THRESHOLDS


def dt(d):
    return datetime(d.year, d.month, d.day)


def run_team(issues):
    findings = []
    for iss in issues:
        applicable = config.RULES_BY_TIER[config.tier_of(iss.team)]
        findings += rules.apply_rules(iss, TODAY, TH, applicable)
    findings = severity.escalate_multi_rule(findings, TH["rules_for_critical"])
    return findings


failures = []

def check(name, cond):
    print(f"  [{'PASS' if cond else 'FAIL'}] {name}")
    if not cond:
        failures.append(name)


print("== SWE-172 archetype (overdue, unowned, stuck triage, thin, milestone) ==")
swe172 = Issue(
    id="SWE-172", title="Alaska software", team="Software", status_type="triage",
    assignee=None, due_date=date(2026, 5, 15), created_at=dt(date(2026, 4, 24)),
    entered_status_at=dt(date(2026, 4, 24)), milestone="Alaska Ship Date", description="",
)
f = run_team([swe172])
fired = {x.rule for x in f}
print("   fired:", sorted(fired))
check("trips overdue", "overdue" in fired)
check("trips stuck_triage", "stuck_triage" in fired)
check("trips unowned_dated", "unowned_dated" in fired)
check("trips thin_deliverable", "thin_deliverable" in fired)
check("4+ rules -> all critical", all(x.severity == "critical" for x in f) and len(fired) >= 3)

print("== clean, well-formed issue -> no findings ==")
clean = Issue(
    id="SWE-900", title="tidy", team="Software", status_type="started",
    assignee="Darrion", project="Dev Productivity",
    created_at=dt(date(2026, 6, 1)), started_at=dt(date(2026, 6, 7)),
    last_state_change=dt(date(2026, 6, 8)), description="A properly described task.",
)
check("clean issue yields nothing", run_team([clean]) == [])

print("== dead WIP: started, real activity 12d ago, has milestone -> critical ==")
dead = Issue(
    id="SWE-185", title="synoptic detection", team="Software", status_type="started",
    assignee="Philip", milestone="Cirrus", created_at=dt(date(2026, 5, 1)),
    started_at=dt(date(2026, 5, 22)), last_state_change=dt(date(2026, 5, 28)),
    updated_at=dt(date(2026, 6, 8)),  # bulk-edit noise; must be ignored
    description="real description here for sure",
)
fd = run_team([dead])
print("   fired:", sorted({x.rule for x in fd}))
check("dead_wip fires despite recent updated_at", any(x.rule == "dead_wip" for x in fd))
check("dead_wip on milestone is critical", any(x.rule == "dead_wip" and x.severity == "critical" for x in fd))

print("== Tier B: dead_wip does NOT run ==")
b_issue = Issue(
    id="BD-10", title="stale deal", team="Business Development", status_type="started",
    assignee="Sam", started_at=dt(date(2026, 5, 1)), last_state_change=dt(date(2026, 5, 1)),
    created_at=dt(date(2026, 4, 1)), description="desc desc desc desc desc",
)
fb = run_team([b_issue])
check("Tier B suppresses dead_wip", not any(x.rule == "dead_wip" for x in fb))
check("Tier B still flags abandoned", any(x.rule == "abandoned" for x in fb))

print("== fresh issue (<48h) is skipped ==")
fresh = Issue(
    id="SWE-999", title="brand new", team="Software", status_type="triage",
    assignee=None, due_date=date(2026, 5, 1), created_at=dt(TODAY), milestone="x", description="",
)
check("fresh issue skipped by grace window", run_team([fresh]) == [])

print("== flood guard: 6/10 unowned_active -> aggregate, not itemized ==")
team_issues = []
for i in range(6):
    team_issues.append(Issue(id=f"T-{i}", title="x", team="Testing", status_type="started",
                             assignee=None, created_at=dt(date(2026, 5, 1)),
                             last_state_change=dt(date(2026, 6, 8)), project="p", description="ok desc here"))
for i in range(6, 10):
    team_issues.append(Issue(id=f"T-{i}", title="x", team="Testing", status_type="started",
                             assignee="Owner", created_at=dt(date(2026, 5, 1)),
                             last_state_change=dt(date(2026, 6, 8)), project="p", description="ok desc here"))
ff = run_team(team_issues)
active_counts = {"Testing": 10}
kept, agg = severity.flood_guard(ff, active_counts, TH["flood_guard"])
ua_items = [x for x in kept if x.rule == "unowned_active"]
print("   aggregates:", [a.detail for a in agg])
check("unowned_active itemized suppressed", len(ua_items) == 0)
check("aggregate emitted", any(a.rule == "unowned_active__aggregate" for a in agg))

print("== diff: new / worsened / resolved ==")
prior = {"SWE-172": {"rules": ["overdue"], "severity": severity.RANK["watch"]},
         "OLD-1": {"rules": ["abandoned"], "severity": severity.RANK["watch"]}}
cur = diff.current_open(run_team([swe172]))
rec = diff.reconcile(cur, prior)
print("   ", rec)
check("SWE-172 worsened (more rules + higher severity)", "SWE-172" in rec["worsened"])
check("OLD-1 resolved", "OLD-1" in rec["resolved"])

print()
if failures:
    print(f"FAILED: {len(failures)} -> {failures}")
    sys.exit(1)
print("ALL CHECKS PASSED")

# ===========================================================================
# v1.1 additions: run guard, enrichment ordering, fallback brief, weekly metrics
# ===========================================================================
from datetime import timezone as _tz, datetime as _dt
from zoneinfo import ZoneInfo as _ZI
from src.run_daily import should_run as daily_should_run
from src.run_weekly import should_run as weekly_should_run
from src.linear_client import apply_enrichment
from src.synthesize import fallback_weekly
from src.weekly_metrics import compute_week
from src.models import Finding

_PT = _ZI("America/Los_Angeles")

print("== run guard: window + already-ran dedupe ==")
nine = _dt(2026, 6, 9, 9, 30, tzinfo=_PT)
check("9:30 PT, never ran -> run", daily_should_run(nine, None))
check("9:30 PT, ran yesterday -> run",
      daily_should_run(nine, _dt(2026, 6, 8, 16, 5, tzinfo=_tz.utc)))
check("9:30 PT, already ran today -> skip",
      not daily_should_run(nine, _dt(2026, 6, 9, 16, 5, tzinfo=_tz.utc)))
check("delayed start 10:40 PT still runs",
      daily_should_run(_dt(2026, 6, 9, 10, 40, tzinfo=_PT), None))
check("8:00 PT (winter early cron) blocked",
      not daily_should_run(_dt(2026, 6, 9, 8, 0, tzinfo=_PT), None))
check("weekly: Monday 9 PT runs", weekly_should_run(_dt(2026, 6, 8, 9, 5, tzinfo=_PT), None))
check("weekly: Tuesday blocked", not weekly_should_run(_dt(2026, 6, 9, 9, 5, tzinfo=_PT), None))
check("weekly: same ISO week dedupe",
      not weekly_should_run(_dt(2026, 6, 8, 10, 5, tzinfo=_PT),
                            _dt(2026, 6, 8, 16, 30, tzinfo=_tz.utc)))

print("== enrichment: ordering must not be trusted from the API ==")
iss = Issue(id="X-1", title="x", team="Software", status_type="started")
# nodes deliberately in ASCENDING order — oldest first
comments = [{"createdAt": "2026-05-01T00:00:00Z"}, {"createdAt": "2026-06-05T00:00:00Z"}]
history = [
    {"createdAt": "2026-04-01T00:00:00Z", "toState": {"type": "started"}},
    {"createdAt": "2026-06-01T00:00:00Z", "toState": {"type": "started"}},
    {"createdAt": "2026-05-15T00:00:00Z", "toState": {"type": "backlog"}},
]
apply_enrichment(iss, comments, history)
check("last_comment_at is the NEWEST comment", iss.last_comment_at.day == 5 and iss.last_comment_at.month == 6)
check("last_state_change is the NEWEST event", iss.last_state_change.month == 6)
check("entered_status_at = latest transition INTO current type", iss.entered_status_at.month == 6)

print("== deterministic register: clustering, markers, completeness ==")
from src.render import build_records, cluster_records, render_register, render_dump, chunk
fnd = [Finding("SWE-172", "Software", None, "overdue", "critical", age_days=25),
       Finding("SWE-172", "Software", None, "stuck_triage", "critical", age_days=46),
       Finding("HRD-21", "Hardware", "Marshall Bruner", "dead_wip", "watch", age_days=12)]
# identical-signature cluster: 3 unowned/stuck/thin items on the same milestone
for i, age in ((164, 41), (165, 43), (166, 47)):
    for rule, sev, a in (("unowned_dated", "critical", None),
                         ("stuck_triage", "critical", age),
                         ("thin_deliverable", "critical", None)):
        fnd.append(Finding(f"SWE-{i}", "Software", None, rule, sev, age_days=a))
ibi = {f"SWE-{i}": Issue(id=f"SWE-{i}", title=f"Season item {i}", team="Software",
                         status_type="triage", milestone="26-27 Season Readiness")
       for i in (164, 165, 166)}
ibi["SWE-172"] = Issue(id="SWE-172", title="Alaska software", team="Software",
                       status_type="triage", milestone="Alaska Ship Date")
ibi["HRD-21"] = Issue(id="HRD-21", title="Scan strategy data collection", team="Hardware",
                      status_type="started", milestone="System Health Checks")
recs = build_records(fnd, ibi)
check("one record per issue", len(recs) == 5)
cl, singles = cluster_records([r for r in recs if r["team"] == "Software"])
check("identical-signature trio clusters", len(cl) == 1 and cl[0]["count"] == 3)
check("cluster names every ID", cl[0]["ids"] == ["SWE-164", "SWE-165", "SWE-166"])
check("non-matching issue stays itemized", any(r["id"] == "SWE-172" for r in singles))
reg = render_register("Wed Jun 10", recs, {"Software": 50, "Hardware": 20},
                      new={"HRD-21"}, worsened={"SWE-172"},
                      resolved_named=[{"id": "STR-1", "team": "Stratus"}],
                      aggregates=[], headline="Test headline.")
check("register names all five issues",
      all(i in reg for i in ("SWE-172", "SWE-164", "SWE-165", "SWE-166", "HRD-21")))
check("cluster ages render as range", "in Triage 41–47d" in reg)
check("new/worsened markers present", "🆕" in reg and "⬆️" in reg)
check("resolved named in footer", "STR-1 (Stratus)" in reg)
check("team status lines", "*SOFTWARE* — 4 flagged / 50 active" in reg)
check("links use workspace slug", "rainmaker-technology-corp/issue/SWE-172" in reg)
check("action hint on critical cluster", "bulk-assign owners" in reg)
dump = render_dump(recs, {"HRD-21"}, set())
check("dump itemizes cluster members individually", dump.count("Season item") == 3)
pieces = chunk("\n".join(f"line {i} " + "x" * 80 for i in range(200)), limit=1000)
check("chunking respects limit", all(len(p) <= 1000 for p in pieces) and len(pieces) > 1)
check("chunking loses nothing", "\n".join(pieces).count("line ") == 200)

print("== weekly metrics from synthetic daily history ==")
days = [
    {"day": "2026-06-02", "flow": {"new": 3, "worsened": 1, "resolved": 2},
     "open_total": 11, "open_total_prior": 10,
     "active_by_team": {"Software": 40, "Hardware": 20},
     "adoption": {"active_total": 60, "owner_pct": 70.0, "project_pct": 55.0,
                  "desc_pct": 40.0, "dated": 12},
     "completions_by_team": {"Software": 4}, "aggregates": ["Testing: 60% unowned (6/10)"]},
    {"day": "2026-06-03", "flow": {"new": 2, "worsened": 0, "resolved": 4},
     "open_total": 9, "open_total_prior": 11,
     "active_by_team": {"Software": 41, "Hardware": 19},
     "adoption": {"active_total": 60, "owner_pct": 72.0, "project_pct": 55.0,
                  "desc_pct": 41.0, "dated": 13},
     "completions_by_team": {"Software": 2, "Hardware": 3},
     "aggregates": ["Testing: 60% unowned (6/10)"]},
]
open_flags = {
    "SWE-172": {"rules": ["overdue", "stuck_triage"], "severity": 2, "team": "Software", "owner": None},
    "HRD-21": {"rules": ["dead_wip"], "severity": 1, "team": "Hardware", "owner": "Marshall"},
    "SWE-9": {"rules": ["abandoned"], "severity": 1, "team": "Software", "owner": "Philip"},
}
first_seen = {"SWE-172": "2026-05-01", "HRD-21": "2026-06-01", "SWE-9": "2026-06-07"}
prior = [{"week": "2026-W22",
          "team_scorecard": [{"team": "Software", "pct": 3}, {"team": "Hardware", "pct": 9}],
          "adherence": {"owner_pct": 68.0, "project_pct": 56.0, "desc_pct": 41.0},
          "throughput": {"total": 5}}]
stats = compute_week("2026-W23 (Jun 1–7)", days, open_flags, first_seen,
                     date(2026, 6, 8), prior)
ff = stats["flag_flow"]
check("flow sums across days", ff["new"] == 5 and ff["resolved"] == 6 and ff["worsened"] == 1)
check("net = new - resolved", ff["net"] == -1)
check("resolution rate uses open_start+new", ff["resolution_rate_pct"] == round(100*6/15))
check("standing total", stats["standing_risk"]["total_open"] == 3)
check("oldest flag first", stats["standing_risk"]["oldest"][0]["id"] == "SWE-172")
check("median age", stats["standing_risk"]["median_age_days"] == 7)
sc = {t["team"]: t for t in stats["team_scorecard"]}
check("scorecard pct uses latest active counts", sc["Software"]["pct"] == round(100*2/41))
check("scorecard WoW arrow worsening", sc["Software"]["wow"] == "↑")
check("adherence WoW delta", stats["adherence"]["owner_pct_wow"] == 4.0)
check("throughput totals + prior", stats["throughput"]["total"] == 9 and stats["throughput"]["prior_total"] == 5)
check("concentration tops owners", stats["concentration"][0]["flags"] == 1)
check("aggregates deduped", stats["aggregates"] == ["Testing: 60% unowned (6/10)"])
check("runs heartbeat", stats["runs_completed"] == 2)
wk = fallback_weekly(stats)
check("weekly fallback renders all sections",
      all(s in wk for s in ("Flag flow", "Standing risk", "Team scorecard",
                            "Adherence", "Throughput", "Concentration")))

print()
if failures:
    print(f"FAILED: {len(failures)} -> {failures}")
    sys.exit(1)
print("ALL CHECKS PASSED (v1.1 suite)")

# ===========================================================================
# v1.2 additions: per-team adherence, overlap candidates
# ===========================================================================
from src.weekly_metrics import overlap_candidates
from src.run_daily import _adoption

print("== per-team adoption in daily run ==")
from datetime import datetime as _d2, timezone as _tz2, timedelta as _td2
_now = _d2.now(_tz2.utc)
batch = [
    Issue(id="SW-1", title="a", team="Software", status_type="started", assignee="P",
          project="X", description="long enough description", created_at=_now - _td2(days=9)),
    Issue(id="SW-2", title="b", team="Software", status_type="backlog", assignee=None,
          created_at=_now - _td2(days=9)),
    Issue(id="HW-1", title="c", team="Hardware", status_type="started", assignee="S",
          due_date=date(2026, 7, 1), created_at=_now - _td2(days=9)),
    Issue(id="SW-9", title="done", team="Software", status_type="completed",
          created_at=_now - _td2(days=9)),
]
adoption, active = _adoption(batch)
check("active counts exclude terminal", active == {"Software": 2, "Hardware": 1})
check("workspace headline computed", adoption["active_total"] == 3 and adoption["owner_pct"] == 66.7)
check("per-team breakdown present", adoption["by_team"]["Software"]["owner_pct"] == 50.0)
check("per-team dated count", adoption["by_team"]["Hardware"]["dated"] == 1)

print("== weekly adherence: per-team with WoW, worst-first ==")
days2 = [{"day": "2026-06-03", "flow": {"new": 0, "worsened": 0, "resolved": 0},
          "open_total": 0, "open_total_prior": 0,
          "active_by_team": {"Software": 2, "Hardware": 1},
          "adoption": {"active_total": 3, "owner_pct": 66.7, "project_pct": 33.3,
                       "desc_pct": 33.3, "dated": 1,
                       "by_team": {"Software": {"active_total": 2, "owner_pct": 50.0,
                                                "project_pct": 50.0, "desc_pct": 50.0, "dated": 0},
                                   "Hardware": {"active_total": 1, "owner_pct": 100.0,
                                                "project_pct": 0.0, "desc_pct": 0.0, "dated": 1}}},
          "completions_by_team": {}, "aggregates": []}]
prior2 = [{"week": "2026-W22", "team_scorecard": [],
           "adherence": {"owner_pct": 60.0,
                         "by_team": {"Software": {"owner_pct": 40.0}}},
           "throughput": {"total": 0}}]
st2 = compute_week("2026-W23", days2, {}, {}, date(2026, 6, 8), prior2)
ad2 = st2["adherence"]
check("workspace WoW", ad2["owner_pct_wow"] == 6.7)
check("per-team WoW where prior exists", ad2["by_team"]["Software"]["owner_pct_wow"] == 10.0)
check("per-team without prior has no WoW", "owner_pct_wow" not in ad2["by_team"]["Hardware"])
check("worst ownership first", ad2["by_team_worst_first"][0]["team"] == "Software")

print("== overlap candidates: real Rainmaker archetype ==")
ov = [
    Issue(id="STR-305", title="Multi-site Nowcasting", team="Stratus", status_type="triage"),
    Issue(id="SWE-163", title="Enable multi-site nowcasting in Stratus, make it faster and less buggy",
          team="Software", status_type="triage"),
    Issue(id="HRD-45", title="APTS Housing", team="Hardware", status_type="started"),
    Issue(id="SWE-900", title="Refactor auth middleware", team="Software", status_type="started"),
    Issue(id="SWE-901", title="Refactor auth middleware part 2", team="Software", status_type="started"),
    Issue(id="OLD-1", title="Multi-site nowcasting archive", team="R&D", status_type="completed"),
]
cands = overlap_candidates(ov)
check("nominates the genuine cross-team pair",
      any({c["a"]["id"], c["b"]["id"]} == {"STR-305", "SWE-163"} for c in cands))
check("same-team near-duplicates excluded",
      not any({c["a"]["id"], c["b"]["id"]} == {"SWE-900", "SWE-901"} for c in cands))
check("terminal issues excluded",
      not any("OLD-1" in (c["a"]["id"], c["b"]["id"]) for c in cands))
check("unrelated titles not nominated",
      not any("HRD-45" in (c["a"]["id"], c["b"]["id"]) for c in cands))
check("candidates capped", len(cands) <= 8)
wk2 = fallback_weekly({**st2, "overlap_candidates": cands,
                       "standing_risk": {"total_open": 0, "median_age_days": 0, "oldest": []},
                       "flag_flow": {"new": 0, "worsened": 0, "resolved": 0, "net": 0,
                                     "resolution_rate_pct": 0},
                       "throughput": {"total": 0, "by_team": {}},
                       "concentration": [], "aggregates": [], "team_scorecard": [],
                       "week_label": "2026-W23", "runs_completed": 1})
check("fallback renders per-team adherence", "Software: owned 50.0%" in wk2)
check("fallback lists overlap for review", "STR-305" in wk2 and "↔" in wk2)

print()
if failures:
    print(f"FAILED: {len(failures)} -> {failures}")
    sys.exit(1)
print("ALL CHECKS PASSED (v1.2 suite)")
