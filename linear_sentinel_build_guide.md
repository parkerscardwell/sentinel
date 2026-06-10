# Linear Hygiene Sentinel — Tier 3 Build Guide & v2 Vision

Companion to the operational spec. The spec defines *what the agent does*; this defines *how the platform is built* and *where it goes*.

---

# Part 1 — Tier 3 custom headless service

## 1.1 What Tier 3 actually is

Two cooperating pieces, built in sequence:

- **Scheduled runner (build now):** a GitHub Actions cron workflow that runs the daily brief and the weekly rollup. No server, no always-on process, uses infrastructure you already have.
- **Event receiver (build for v2):** a single always-on serverless function that receives Linear webhooks and Slack interactions, for real-time Critical escalation and closed-loop actions. Not needed for v1.

The governing principle from the spec holds: **deterministic code does detection; the model only narrates.** One small model call per run, everything else is plain data filtering.

## 1.2 Repository layout

```
linear-sentinel/
  .github/workflows/
    daily.yml            # cron: weekday brief
    weekly.yml           # cron: Monday rollup
  src/
    models.py            # Issue / Finding dataclasses (dependency-free)
    config.py            # team tiering, thresholds, rule applicability
    linear_client.py     # GraphQL: delta fetch + open-flag re-check + 2-pass enrich
    rules.py             # pure functions: issue -> [Finding]
    severity.py          # multi-rule escalation + per-team flood guard
    diff.py              # reconcile findings vs prior state (new/worsened/resolved)
    db.py                # private Postgres state store (load/save/record_week)
    synthesize.py        # one Anthropic call: distilled findings -> DM text
    slack_client.py      # open DM to Parker, post
    hello.py             # M0 pipe test
    run_daily.py         # orchestration
    run_weekly.py        # orchestration (M4)
  tests/
    test_logic.py        # synthetic verification of rules/severity/flood/diff (stdlib only)
  schema.sql             # one-time DB schema
  requirements.txt
  .env.example
  .gitignore
  README.md
```

## 1.3 State store (the keystone)

Each run is stateless, so state lives in your own private database — serverless Postgres (Neon free tier), fully separate from the Rainmaker monorepo and infrastructure. The connection string is a single `DATABASE_URL` secret; `src/db.py` reads state at the start of a run and writes it back at the end. Postgres (over a committed file or KV) is the right call here: it keeps the data independent and gives you queryable history for the v2 trend work. Schema lives in `schema.sql`, run once.

Two tables. A single-row `sentinel_state` holds current state; `weekly_history` accumulates per-week counters for trends.

```sql
create table sentinel_state (
    id text primary key default 'singleton',
    mode text not null default 'observe',        -- observe | live
    last_daily_run timestamptz,
    last_weekly_run timestamptz,
    open_flags jsonb not null default '{}'::jsonb,
    dismissed jsonb not null default '{}'::jsonb,
    owner_streaks jsonb not null default '{}'::jsonb,
    updated_at timestamptz not null default now()
);
create table weekly_history (
    week text primary key,                        -- e.g. 2026-W23
    by_team jsonb not null,
    by_rule jsonb not null,
    by_severity jsonb not null,
    recorded_at timestamptz not null default now()
);
```

`open_flags` is the per-issue record the diff runs against — keyed by identifier, each holding its tripped rules and current severity:

```json
"SWE-172": { "rules": ["overdue", "stuck_triage", "unowned_dated", "thin_deliverable"], "severity": 2 }
```

## 1.4 Daily data flow (v1.1 — full scan)

> **Why not a delta?** The original delta design (updatedAt since last run + standing
> re-check) was structurally blind to the rules that matter most: dead_wip and
> abandoned fire on *inactivity*, and inactive issues never appear in a delta — so
> they could never be flagged in the first place. At ~700 open issues, a full scan
> is ~7 requests and also makes the flood guard's per-team active counts correct.

1. Load state from Postgres (`db.load_state`).
2. **Full scan:** every issue in a non-terminal state, plus recently completed
   issues (for done-with-open-children and throughput counting).
3. **Batched enrich:** comments/history fetched only for started/triage/flagged
   candidates, ~20 issues per request via GraphQL aliases, with retry/backoff.
   Ordering is enforced in code — API node order is never trusted.
4. Run `rules.py` over the candidate set → findings.
5. `severity.py`: escalate any issue tripping 3+ rules to Critical; apply the >50% per-team flood guard.
6. `diff.py`: compare to prior `open_flags` → new / worsened / resolved.
7. Update `open_flags` and `last_daily_run`.
8. **If observe mode:** post the calibration summary (counts + what it *would* have flagged). **If live:** `synthesize.py` produces the DM (new/worsened Critical+Watch, top 10) → `slack_client.py` DMs Parker.
9. Save state back to Postgres (`db.save_state`).

## 1.5 Rule engine — representative pseudocode

Every rule is a pure function. No model involved.

```python
def overdue(issue, today):
    if issue.due_date and issue.due_date < today and issue.status_type != "completed":
        days = (today - issue.due_date).days
        sev = "critical" if issue.assignee is None else "watch"
        return Finding(issue, "overdue", severity=sev, age_days=days)

def stuck_triage(issue, today):
    if issue.status_type == "triage" and age_in_state(issue, today) > 10:
        return Finding(issue, "stuck_triage", severity="watch")

def dead_wip(issue, today):
    if issue.status_type != "started":
        return None
    # updatedAt is untrustworthy — bulk edits inflate it.
    last_real = max(issue.last_state_change, issue.last_comment_at)
    if (today - last_real).days >= 7:
        sev = "critical" if (issue.due_date or issue.milestone) else "watch"
        return Finding(issue, "dead_wip", severity=sev)
```

`config.py` holds the tier→ruleset mapping, so each team only runs its applicable rules.

## 1.6 GitHub Actions — daily workflow

GHA cron is UTC *and best-effort* — scheduled runs routinely start 10–40 minutes
late. 09:00 PT is 16:00 UTC in summer (PDT) and 17:00 UTC in winter (PST). Schedule
both; the guard accepts any start in the 09:00–11:59 PT window and dedupes on
"already ran today (PT)", so late starts still run and double-fires are skipped.

```yaml
# .github/workflows/daily.yml
name: sentinel-daily
on:
  schedule:
    - cron: "0 16 * * 1-5"   # 09:00 PDT
    - cron: "0 17 * * 1-5"   # 09:00 PST
  workflow_dispatch: {}        # manual run button
jobs:
  brief:
    runs-on: ubuntu-latest
    steps:
      - uses: actions/checkout@v4
      - uses: actions/setup-python@v5
        with: { python-version: "3.12" }
      - run: pip install -r requirements.txt
      - run: python -m src.run_daily
        env:
          LINEAR_API_KEY:   ${{ secrets.LINEAR_API_KEY }}
          SLACK_BOT_TOKEN:  ${{ secrets.SLACK_BOT_TOKEN }}
          PARKER_SLACK_ID:  ${{ secrets.PARKER_SLACK_ID }}
          ANTHROPIC_API_KEY: ${{ secrets.ANTHROPIC_API_KEY }}
          DATABASE_URL:     ${{ secrets.DATABASE_URL }}
```

State persists to Postgres, so there is no commit-back step and the workflow needs no write permission on the repo.

`run_daily.py` begins with a timezone guard:

```python
def should_run(now_pt, last_daily_run):
    if not (9 <= now_pt.hour <= 11):          # tolerant of late cron starts
        return False
    if last_daily_run and last_daily_run.astimezone(PT).date() == now_pt.date():
        return False                           # dedupe: already ran today
    return True
```

A third workflow, `keepalive.yml`, commits an empty marker monthly. GitHub
disables scheduled workflows after 60 days of repository inactivity; without the
keepalive, a push-once repo's crons silently die two months in.

`weekly.yml` is identical with `cron: "0 16 * * 1"` / `"0 17 * * 1"` and `run_weekly`.

## 1.7 Secrets and access

GitHub repo → Settings → Secrets:
- `LINEAR_API_KEY` — a Linear personal API key, ideally on a dedicated service account with read access (and write later, for v2 closed-loop).
- `SLACK_BOT_TOKEN` — a Slack app bot token with `chat:write` and `im:write`; the bot opens a DM to `PARKER_SLACK_ID` via `conversations.open` and posts there.
- `PARKER_SLACK_ID` — your Slack user ID.
- `ANTHROPIC_API_KEY` — for the synthesis call.
- `DATABASE_URL` — the Neon/Postgres connection string for the private state store.

## 1.8 LLM usage and cost

One Anthropic call per run, with a deterministic fallback formatter if it fails —
a model outage degrades the prose, never the delivery. Daily uses Haiku (short,
rigidly formatted); the weekly rollup uses Sonnet, where the performance narrative
justifies the marginal cost (both set in `src/config.py`). Prompt caching is moot
at one call per day (cache TTL is minutes). Realistic cost: well under $1/month
for the models, GitHub Actions inside free minutes, Linear/Slack APIs free, Neon
free tier for state. Cost tracks flagged issues, not workspace size.

## 1.9 Build milestones

- **M0 — Prove the pipe (½ day).** Repo, secrets, run `src.hello` (and the GHA "Run workflow" button) to post a fixed DM. Confirms scheduling, secrets, and Slack delivery before any logic.
- **M1 — Detection, local console (1–2 days).** `linear_client` + `rules` + `config`. Run `RUN_FORCE=1 python -m src.run_daily` with only `LINEAR_API_KEY` set — no DB, no Slack — and read findings off the console. This is where you confirm the `enrich()` history/comment field shapes against your Linear and tune the rules against real data.
- **M2 — State + diff (1 day).** Provision the Neon DB, run `schema.sql`, set `DATABASE_URL`. Re-run; `db` + `diff` give new/worsened/resolved and standing-baseline suppression.
- **M3 — Synthesis + delivery, wired to cron (1 day).** Set the Slack and Anthropic secrets. Runs in observe mode (`mode='observe'`) — posting "would flag" calibration summaries. **Run 14 days here**, tune `THRESHOLDS`, then `update sentinel_state set mode='live';`.
- **M4 — Weekly rollup (½ day).** Implement `run_weekly` body + `record_week` trend counters; the weekly workflow is already scheduled.

Flip to live after M3's observe window. Everything past M4 is Part 2.

---

# Part 2 — v2 vision, built out

Each capability lists what it delivers, the data it needs, how it rides the platform, rough effort, dependencies, and the caution that matters most.

## 2.1 Event-driven Critical escalation *(your confirmed priority)*
- **Delivers:** a Critical condition triggers an immediate DM instead of waiting for 09:00.
- **Data/infra:** a Linear webhook subscription (issue create/update) pointing at the **event receiver** serverless function.
- **How:** the receiver evaluates only the changed issue against Critical rules using the same `rules.py`; if it trips, DM immediately and mark it in state so the morning digest dedupes it.
- **Effort:** medium — this is what introduces the always-on endpoint.
- **Depends on:** v1 rules; the event receiver.
- **Caution:** debounce a flurry of edits; only true Critical escalates (everything else waits for the digest) or you reintroduce the noise the whole design avoids. Respect snoozes.

## 2.2 Trend memory & regression detection
- **Delivers:** direction, not snapshots — "Software's stuck-in-Triage up four weeks running," "this issue has appeared in five straight digests."
- **Data/infra:** `weekly_history` and per-issue/per-owner streak counters — already in state.
- **How:** a detector compares trailing-N-week counts and per-issue digest streaks.
- **Effort:** low once state exists. **Build this first in v2** — no new infrastructure, immediate value.
- **Depends on:** a few weeks of accumulated state.
- **Caution:** require sustained direction before alerting; one bad week is not a regression.

## 2.3 Owner & manager rollups
- **Delivers:** flags grouped by assignee and, via the org map, by manager — "X owns four of this week's six Criticals."
- **Data/infra:** an `owners.yaml` mapping assignee → manager, seeded from your existing org structure.
- **How:** group findings by owner; roll up to manager.
- **Effort:** low for the grouping; ongoing cost is keeping the mapping current.
- **Depends on:** the org mapping.
- **Caution:** the surveillance line. Strictly DM-only, framed as where-to-coach, never a leaderboard. This guardrail is already set; do not relax it here.

## 2.4 Predictive slippage *(highest-value COO signal)*
- **Delivers:** flags milestones that *will* miss, before the date — early warning, not post-mortem.
- **Data/infra:** completion history (state), open scope per milestone (Linear), target/milestone dates (Linear).
- **How:** track completed-issues-per-week per team → for each dated milestone, projected finish = open scope ÷ throughput → if projected > target, flag with a confidence band.
- **Effort:** medium — the throughput model and scope accounting.
- **Depends on:** several weeks of completion history; 2.2's plumbing.
- **Caution:** estimates are unused in this workspace, so use **issue count** as the scope proxy, never estimate sums. Present probabilistic ranges, not false precision. Noisy until enough history accrues — gate it behind a minimum-history check.

## 2.5 Cross-system corroboration
- **Delivers:** higher-confidence risk by combining sources — an at-risk milestone that is *also* Slack-silent and has a stale linked doc is a stronger signal than Linear alone. This is the path back to the full org-pulse vision.
- **Data/infra:** Slack + Drive access; a project → channel/folder mapping.
- **How:** for each at-risk milestone, check recent Slack activity and Drive last-modified; compute a composite risk score.
- **Effort:** high — the mapping and multi-source joins are the work.
- **Depends on:** a trusted Linear core; the mapping.
- **Caution:** scope creep. Do not start until the Linear core is something you act on daily. The mapping is the hard, unglamorous part — budget for it.

## 2.6 Closed-loop actions & feedback
- **Delivers:** act from the DM thread — reassign, nudge the owner, snooze a flag, mark a false positive.
- **Data/infra:** Slack interactivity (buttons / thread replies) into the **event receiver**; Linear write scope.
- **How:** the receiver maps an action to a Linear write (`save_issue` reassign, add comment) and/or a state update (snooze, dismiss).
- **Effort:** medium-high.
- **Depends on:** the event receiver; Linear write access.
- **Caution:** scope the bot's write access tightly and confirm-before-act on anything destructive. This is where dismiss/snooze data starts accruing for 2.7.

## 2.7 Self-tuning thresholds
- **Delivers:** the system proposes rule changes from your behavior — "you dismiss 80% of no-project advisories; recommend retiring that rule."
- **Data/infra:** per-rule dismiss-rate and hit-rate from state.
- **How:** weekly, surface suggestions alongside the rollup.
- **Effort:** low once 2.6 feeds it data.
- **Depends on:** 2.6.
- **Caution:** **propose, never auto-apply.** Silent self-adjustment risks the system going blind to something it decided was noise. You stay in the loop on every threshold change.

## 2.8 Sequencing

```
v1 (Tier 3 scheduled) ──> state store is the keystone
   │
   ├─ Phase 1
   │    2.2 Trend memory        (no new infra — do first)
   │    2.1 Event escalation    (introduces the event receiver)
   │
   ├─ Phase 2
   │    2.4 Predictive slippage  (needs throughput history)
   │    2.3 Owner/manager rollups (needs org map)
   │
   └─ Phase 3
        2.6 Closed-loop actions   (needs event receiver)
        2.7 Self-tuning           (needs 2.6)
        2.5 Cross-system          (largest; last)
```
