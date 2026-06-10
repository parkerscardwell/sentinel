# Linear Hygiene Sentinel

A private, headless COO instrument. Scans every Linear team on a schedule, applies
deterministic hygiene/risk rules in code, and DMs Parker a daily brief plus a Monday
performance rollup. Language models only phrase the output — never detect.

Standalone and private: its own repo, its own database, fully separated from the
Rainmaker monorepo and infrastructure.

## Architecture (v1.1)

- **Scheduler:** GitHub Actions cron. Daily weekdays, weekly Mondays, plus a monthly
  keepalive (GitHub disables crons after 60 days of repo inactivity; the keepalive's
  empty commit prevents that).
- **Detection:** pure Python over a **full scan of all open issues** every run
  (`src/rules.py`, `src/linear_client.py`). No model. The full scan — not a delta —
  is essential: the highest-value rules (dead WIP, abandoned) fire on *inactivity*,
  and inactive issues never appear in an updated-since delta. It also makes the
  flood guard's per-team denominators correct. ~700 open issues ≈ 7 API requests;
  enrichment is batched 20-up via GraphQL aliases.
- **Synthesis:** Haiku for the daily headline only (the register itself is
  deterministic code), Opus for the weekly
  rollup (the direction read and recommendations earn the quality; one call/week). Both have deterministic
  fallbacks — if the model call fails, a plain formatted DM goes out instead and the
  run still succeeds. Models are set in `src/config.py`.
- **State:** serverless Postgres (Neon free tier). Open flags, first-seen dates,
  daily counters, and weekly stats for trend work. Schema is created/migrated
  automatically on every run (`src/db.py: init_schema`); `schema.sql` is reference only.
- **Delivery:** Slack DM to Parker only. Any crash also attempts a
  "Sentinel run failed" DM before exiting, so a dead key is noticed same-morning.
- **Run guard:** GHA cron is best-effort and often late, so runs accept any start
  09:00–11:59 PT and dedupe on "already ran today (PT)". Both DST cron lines may
  fire; the first valid one wins. `RUN_FORCE=1` bypasses for manual runs.

## Daily DM — the team register
The daily is a complete, deterministic register: **every flagged issue, by name,
grouped by team** (worst first), with a team status line (X flagged / Y active ·
N Critical), per-issue titles, owners, plain-English conditions with ages,
milestone tags, Linear links, and action hints on Criticals. Movement is
annotation, not filter: 🆕 new / ⬆️ worsened markers, and resolved issues listed
by name. Identical-signature groups (same team, owner, rules, severity, and
project/milestone; 3+ issues) collapse into one cluster block that still names
every ID — 33 lines of the same problem become 3 with nothing hidden. The
exhaustive linked one-line-per-issue dump posts as a threaded reply under the
register. The register is rendered in code, never by a model, so completeness is
guaranteed; Haiku contributes only the one-sentence headline at the top, and the
register goes out with or without it. Heavy days split across sequential
messages automatically (Slack message cap). Flood-guard aggregates appear as a
workspace-wide section; their detail lives in the Monday rollup. Observe mode
sends the identical register tagged "(observe — would flag)".

## Weekly rollup (Mondays)
Computed entirely in code (`src/weekly_metrics.py`), phrased by Opus:
heartbeat (runs completed X/5) · flag flow (new/worsened/resolved, net, resolution
rate) · standing risk (total, median age, oldest five) · team scorecard (flag
*rate* per team — flags per active issue — with week-over-week arrows) · adherence
(ownership/project/description percentages and dated-issue count, workspace headline
plus per-team breakdown with WoW deltas, worst ownership first — the usage-discipline
trendline) · throughput (completions per team vs prior week) · concentration (top
owners, coaching frame, never a leaderboard) · aggregates & structural notes ·
a closing *Read & recommendations* block: 2–3 sentences on the week's direction plus
exactly 3 recommendations (max 6 points combined) covering cross-team communication
where work appears to overlap and general coaching. Overlap candidates are nominated
deterministically in code (cross-team title-similarity over open issues) — the model
only judges and phrases them, never hunts through raw tickets. Trend callouts require
three sustained weeks of direction before they appear.

## Setup

1. **Repo** — create a **private** GitHub repo, push this scaffold. The keepalive
   workflow needs no setup beyond existing (it commits an empty marker monthly).
2. **Database** — create a free project at neon.tech; copy the connection string
   into the `DATABASE_URL` secret. No manual schema step — the code creates it.
3. **Slack app** — api.slack.com/apps → bot scopes `chat:write` + `im:write` →
   install → copy Bot User OAuth Token (`SLACK_BOT_TOKEN`). Your member ID →
   `PARKER_SLACK_ID`.
4. **Linear key** — Settings → API → personal key, read access (a dedicated service
   account is ideal; write comes only with v2 closed-loop) → `LINEAR_API_KEY`.
5. **Anthropic key** — console.anthropic.com → `ANTHROPIC_API_KEY`.
6. **GitHub secrets** — repo Settings → Secrets and variables → Actions → add all
   five: `LINEAR_API_KEY`, `SLACK_BOT_TOKEN`, `PARKER_SLACK_ID`,
   `ANTHROPIC_API_KEY`, `DATABASE_URL`.

## Build / run order (milestones)

```
M0  Prove the pipe:
      python -m src.hello            # with .env loaded → expect a Slack DM
      then push and click "Run workflow" on sentinel-daily to prove cron+secrets.

M1  Detection, local console (no DB/Slack needed):
      RUN_FORCE=1 LINEAR_API_KEY=... python -m src.run_daily
      → full scan + grouped findings printed. Verify against real data here,
        especially that enrichment timestamps look sane (the run will be slow-ish
        the first time; ~25 API requests total).

M2  State: set DATABASE_URL. Re-run twice; second run shows new/worsened/resolved.

M3  Delivery: set Slack/Anthropic secrets. Starts in observe mode — the DM lists
      exactly what it WOULD flag, itemized, so thresholds can actually be tuned.
      Run 14 days, adjust src/config.py THRESHOLDS, then go live with:
        python -m src.set_mode live      (no SQL required)

M4  Weekly rollup: already implemented and scheduled; it gets richer as
      daily_history and weekly_history accrue. Trend arrows appear from week 2,
      sustained-trend callouts from week 4.
```

## Local development

```
python -m venv .venv && . .venv/bin/activate
pip install -r requirements.txt
cp .env.example .env   # fill in; export before running, or use a dotenv loader
python -m tests.test_logic     # rule engine + guards + weekly metrics (no network)
python -m tests.test_smoke     # full pipeline end-to-end with stubbed services
```

Run both test modules before every change. They need no credentials and no network.

## Tuning

All thresholds live in `src/config.py` (`THRESHOLDS`), team tiering in
`TIER_A/B/C`, and both model choices in `MODEL_DAILY` / `MODEL_WEEKLY`. Nothing
else hardcodes a number. Estimates and priority are intentionally NOT per-issue
rules — they are workspace-wide near-empty and would flood; the flood guard demotes
any rule exceeding 50% of a team's active issues to an aggregate (reported weekly),
and the adherence section of the weekly tracks field adoption as percentages instead.

## Endurance notes (why this keeps running unattended)

- Monthly keepalive defeats GitHub's 60-day cron auto-disable.
- Failed runs DM you the traceback; GitHub also emails on workflow failure.
- The weekly heartbeat line (runs X/5) makes silent gaps visible within days.
- Model outages degrade to deterministic briefs, never to missed mornings.
- Late cron starts are tolerated by the run-window guard; duplicates are deduped.
- Linear rate limits / transient 5xx are retried with backoff.
