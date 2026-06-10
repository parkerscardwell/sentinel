# Linear Hygiene Sentinel — Cowork Agent Specification

## Operating parameters (locked)

| Setting | Value |
|---|---|
| Owner / audience | Parker (COO) — sole recipient |
| Runtime (production) | Claude Code cloud routine — claude.ai/code/scheduled |
| Runtime (observe-mode only) | Cowork local task, while at desk |
| Data sources | Linear (MCP, read) · Slack (MCP, post) |
| State store | External JSON/db — required (cloud routines keep no inter-run memory) |
| Delivery | Slack DM to Parker only — never to teams or channels |
| Daily run | 09:00 America/Los_Angeles, every weekday, covering the prior day |
| Weekly rollup | Monday 09:00 PT, covering the prior week |
| Drive snapshot | None — DM only |
| Mid-day escalation | Deferred to v2 |
| Launch posture | 14-day observe mode, then thresholds locked and flipped to live |

---

## 1. Mission

Scour every Linear team daily for usage discipline, hygiene, deadline-slippage risk, low-quality reporting, and stalled work. Report only what is material and changing, ranked by severity, privately to Parker. This is a diagnostic instrument for the COO's situational awareness — not a public scorecard. It names issues and owners only so Parker can act; it is never routed to teams or used as a performance gotcha.

**Prime directive: signal over volume.** A report Parker stops reading is worthless. Every rule below is subordinate to that. When in doubt, suppress.

---

## 2. Scope and tiering

Monitoring universe is all teams in the workspace. Detection rules apply by class.

**Tier A — execution teams, full ruleset:**
Software, Seraph, Stratus, Hardware, Elijah, Dispersion, Sensing, Forecaster, Research, Meteorology, Field Operations, Logistics, Test Operations, Testing, Operator.

**Tier B — functional & incident teams, relaxed ruleset** (drop dead-WIP and cycle rules; keep slippage, untouched, missing-owner, reporting):
Business Development, Brand, Regulatory Affairs, Regulatory, Operations, Manufacturing, Flight Incidents.

**Tier C — cadence containers, externally-synced & dormant, excluded from hygiene scoring** (report only genuinely overdue dated items, or a one-line "dormant" note):
Program Planning, Projects, Operating Cadence, Quarterly, Monthly, Alignment, Production (First Resonance-synced), R&D, Research Testing, VVO, DISCO.

**Standing roster notes the agent must honor:**
- **Production** is synced from First Resonance; its system of record is external. Never flag its field hygiene.
- **DISCO** has been dormant since Feb 4. Report once as recommend-archive, then suppress.
- **Meteorology** was created June 9 and may be empty; do not report an empty team as "clean."
- **Regulatory** and **Regulatory Affairs** are separate teams. Surface this once as a structural-duplication note, not a recurring flag.

---

## 3. Universal exclusions

Before any rule runs, exclude from scoring:
- Issues with status type `completed`, `canceled`, or `duplicate`.
- Archived issues (`includeArchived = false`).
- Issues created within the last 48 hours (fresh triage is not a hygiene failure).

---

## 4. Detection rules

Each rule emits: issue identifier, team, owner (or "unowned"), the condition tripped, and age of condition. Severity per §5.

### 4.1 Improper usage
- **Stuck in Triage** — status type `triage` for more than **10 days**. Highest-value rule for this workspace; Triage is being used as a parking lot.
- **Unowned active work** — status type `started` or `unstarted`/Todo with no assignee.
- **Done with open children** — status `completed` while sub-issues remain open.
- **Terminal-state drift** — `canceled`/`duplicate` without a `duplicateOf` link or closing note (advisory).

### 4.2 Poor hygiene
- **Unowned + dated** — no assignee but a due date exists (always flag).
- **No project on Tier A active work** — `started`/`unstarted` issue with no project (advisory).
- **Field-population stats, not flags** — estimate and priority are effectively unused workspace-wide, so report them only as per-team aggregates ("Software: 0% of active issues estimated"), never per-issue. Same auto-demotion applies to any field whose absence exceeds the §6 flood threshold.

### 4.3 Deadline slippage risk
- **Overdue** — due date in the past and status not `completed`. Severity scales with days overdue and whether the item is owned.
- **Approaching and not started** — due date within **5 days** while status type is still `backlog`/`unstarted`/`triage`.
- **Date thrash** — due date, project target date, or milestone date changed more than once (a slipping commitment being papered over). Requires history access; skip if unavailable.
- **Project-level** — evaluate against **both project target dates and milestone dates**: any project or milestone past its date with open issues, or with open scope disproportionate to remaining time.

### 4.4 Low-quality reporting
- **Stale project status** — project update older than its expected interval (default 14 days) on any active project, or no status update on an at-risk project.
- **Thin deliverables** — issue with a due date or milestone but an empty/near-empty description (live examples: SWE-172/173/174 — milestone-linked, dated, blank).

### 4.5 Untouched / stalled
- **Dead WIP (Tier A only)** — status type `started` with no genuine activity for **7+ days**. *Genuine activity = state change or new comment, NOT raw `updatedAt`.* Bulk edits inflate `updatedAt` and will mask stalls (confirmed: a June 8 bulk edit reset timestamps across stale items). Corroborate via state history and comments; treat `updatedAt` as untrustworthy.
- **Abandoned** — any non-terminal issue with no state change and no comment for **21+ days**.

---

## 5. Severity model

- **Critical** — overdue *and* unowned deliverable; dead WIP on a dated/milestone item; at-risk project with no status update; any item tripping 3+ rules at once.
- **Watch** — stuck in Triage > 10 days; approaching due date not started; stalled In Progress 7–14 days; unowned active work.
- **Advisory** — missing project, thin description, structural notes, all aggregate field stats.

Daily flags carry Critical and Watch only. Advisory rolls into the weekly digest.

---

## 6. Self-calibration (anti-flood)

- **Per-rule, per-team flood guard:** if a rule would flag more than **50%** of a team's active issues, suppress the itemized flags and emit one aggregate line instead. Handles per-team convention variance without manual tuning.
- **Observe mode (first 14 days):** the agent runs daily but posts a calibration summary — counts per rule per team and what it *would* have flagged — instead of live alerts. Parker reviews, adjusts thresholds, then flips to live.
- **Standing-baseline suppression:** daily flags report only **new or newly-worsened** conditions since the last run. Unchanged standing issues are summarized in one line, never re-listed daily.

---

## 7. Output format

### Daily flag — 09:00 PT, prior day (DM)
Lead with counts, then itemized Critical/Watch that are new or worsened since the last run, capped at the top **10** by severity. One closing line for the unchanged standing baseline. If nothing new, a single line saying so. No preamble.

```
Linear · Mon Jun 9 (covering Jun 8)
New/worsened: 3 Critical · 4 Watch  ·  Standing: 47 (unchanged)

CRITICAL
• SWE-172 [Software] unowned · due May 15 (25d overdue) · in Triage · no description — trips 4 rules
• ...

WATCH
• ...
```

### Weekly rollup — Monday 09:00 PT, prior week (DM)
- Topline: total open flags, week-over-week delta, count by severity.
- Per tier, then per team: flag counts and change from last week.
- Aging view: items that crossed a threshold this week (newly overdue, newly dead WIP).
- Top 5 standing offenders by rule-count and age.
- Aggregate field-hygiene stats per Tier A team.
- Structural notes (Regulatory duplication, DISCO dormant) — once, until resolved.
- A 3–4 sentence plain-language read on whether org hygiene is improving, flat, or degrading.

---

## 8. Deployment checklist

1. Create the agent as a **Claude Code cloud routine** at claude.ai/code/scheduled (for at-desk observe mode only, a Cowork local task is acceptable). Paste this spec as the routine prompt — it must be self-contained, since routines run with no human in the loop.
2. Attach the Linear and Slack MCP connectors. Configure the cloud environment: network scope, any API credentials, and access to the external state store (§10.4).
3. Set schedule: daily 09:00 America/Los_Angeles (weekdays), weekly rollup Monday 09:00 PT.
4. Set delivery target: Slack DM to Parker only.
5. Run in **observe mode** for 14 days; review the daily calibration summaries.
6. Adjust the bolded thresholds in §3–§5 against Parker's judgment, then flip to live.
7. Revisit after one month: prune dead rules, confirm flood guards are working.

---

## 9. Runtime & deployment architecture (persistence)

A 09:00 unattended brief cannot depend on a laptop being awake. Three tiers, in increasing durability:

**Tier 1 — Cowork local task.** Zero-code, set up in the desktop app. Runs only while the machine is awake and the app is open; otherwise the run is skipped and caught up later. Acceptable for the at-desk observe window; not for production.

**Tier 2 — Claude Code cloud routine (production default).** Runs on Anthropic-managed cloud infrastructure independent of any local machine. Same paste-the-spec workflow as Cowork, with Linear and Slack connectors attached. Design around its two constraints: minimum interval is one hour (irrelevant — we run once daily), and each run is stateless with no memory of prior runs (handled by the external state store, §10.4). Routine runs draw subscription usage and are subject to a daily routine-run cap; one daily plus one weekly run sits comfortably inside it.

**Tier 3 — custom headless service (scale-out).** Cloud cron / GitHub Actions schedule / serverless trigger → Linear GraphQL API → deterministic rule engine in code → Anthropic API for synthesis only → Slack API. Maximum control, the best token economics (§10), and a durable owned state store. Move here only when you need sub-hour cadence, multiple workspaces, event triggers at volume, or strict cost governance.

**Recommendation:** observe mode on Tier 1 (or straight to Tier 2); production on Tier 2; graduate to Tier 3 only if scale, frequency, or cost demands it.

---

## 10. Token economics & scalability

Governing principle: **detection is data filtering, not language. Spend tokens only on the writeup.**

1. **Code computes rules; the model only narrates.** Every rule in §4 is a deterministic filter over Linear records — no model required. The LLM receives only a capped, distilled findings object to prioritize and phrase. ~95% token reduction versus reasoning over raw issues.
2. **Delta reads.** Each run pulls only issues changed since the last run (Linear `updatedAt` filter) and re-evaluates the persisted open-flag set against today's date. Cost scales with change volume, not total ticket count.
3. **Two-pass enrichment.** Cheap pass filters on light fields (state, dates, assignee). Only issues that trip a rule get the expensive fetch (description, comments, history). Bounds cost as the workspace grows.
4. **External state store (required).** A small JSON/db holding: current open flags, last-run timestamp, weekly counters, and the snooze/dismiss list. Enables delta processing, standing-baseline suppression, and all v2 trend work. Candidates: a committed file in a repo, a Drive file, or a managed KV/db.
5. **Cheapest sufficient model.** Synthesis runs on a Haiku-class model; detection uses no model at all. Reserve larger models only for genuinely hard reasoning added later.
6. **Prompt caching (Tier 3).** Cache the static spec and ruleset so unchanging instructions are not re-billed each run.
7. **Batch the non-urgent work.** Run the heavy weekly rollup or any backfill via asynchronous batch processing for a substantial cost reduction; keep the latency-sensitive daily DM synchronous.
8. **Capped payloads.** The model never sees full issue bodies — only top-N findings plus aggregates. Bounded input means bounded, predictable cost regardless of how messy Linear becomes.

Net effect: spend is a function of flagged and changed issues, not total ticket volume — the property required for something that monitors continuously.

---

## 11. v2 vision

Sequenced, each building on the v1 core and the §10.4 state store.

1. **Event-driven Critical escalation** *(confirmed; build first).* A Linear webhook fires a lightweight, Critical-only evaluation on the single changed issue; if it trips, an immediate DM. Daily and weekly cadence persist for everything else. Maps to a routine's API/webhook trigger (Tier 2) or a webhook handler (Tier 3).
2. **Trend memory & regression detection.** Track per-team and per-owner hygiene over time and report *direction*, not just state: "Software's stuck-in-Triage count up four weeks running," or an item "deferred three Mondays in a row." Surfaces chronic avoidance, the thing point-in-time scans miss.
3. **Owner & manager rollups.** Aggregate flags by assignee and, via the org map, by manager — so a cluster of Criticals resolves to "who and where," for Parker's coaching conversations. DM-only; framing stays diagnostic.
4. **Predictive slippage.** Forecast misses before the date arrives: remaining open scope versus the team's historical close rate flags milestones that *will* slip. Early warning rather than post-mortem — the highest-value COO signal. Requires throughput history from the state store.
5. **Cross-system corroboration.** Layer Slack and Drive onto the proven Linear core: an at-risk milestone with no recent Slack discussion and a stale design doc is a higher-confidence risk than Linear alone. This is the path back to the full org-pulse / AI-Chief-of-Staff vision, now built on a foundation that works.
6. **Closed-loop actions & feedback.** Reply in the DM thread to reassign, nudge an owner, snooze a flag, or mark a false positive. Snooze stops re-nagging; false-positive feedback feeds calibration.
7. **Self-tuning thresholds.** Use dismiss/snooze rates and per-rule hit-rates to propose adjustments ("you dismiss 80% of no-project advisories — recommend retiring that rule").

**Sequencing:** (1) first — confirmed, and the webhook plus state store unlock it. (2) and (4) next — both ride the same state store and deliver the early-warning value. (3) alongside. (5), (6), (7) are the mature phase.
