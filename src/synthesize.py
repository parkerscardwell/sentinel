"""Model synthesis with deterministic fallbacks.

The daily register is rendered deterministically (src/render.py) so completeness
is guaranteed; the model contributes only a one-sentence headline (Haiku), and the
register goes out with or without it. The weekly rollup is phrased by the weekly
model (Opus) with a deterministic fallback — a model outage degrades prose, never
delivery.
"""
from __future__ import annotations
import json
import os
from typing import List

from . import config
from .models import Finding

HEADLINE_SYSTEM = """You write ONE orienting headline (1-2 sentences, max 40 words) for the top
of a COO's daily Linear register. Input is JSON: per-team flag counts and the largest
issue clusters. Point at the day's center of gravity — the team, milestone, or cluster
that most deserves attention — using only facts present in the input. No preamble,
no markdown, no bullet, just the sentence(s)."""


# ---- daily headline ----------------------------------------------------------

def headline(summary: dict) -> str:
    """One orienting sentence for the register top. Empty string on any failure —
    the register renders fine without it."""
    try:
        msg = _client().messages.create(
            model=config.MODEL_DAILY, max_tokens=120, system=HEADLINE_SYSTEM,
            messages=[{"role": "user", "content": json.dumps(summary)}])
        return "".join(b.text for b in msg.content if b.type == "text").strip()
    except Exception:
        return ""


# ---- weekly ------------------------------------------------------------------

def fallback_weekly(stats: dict) -> str:
    """Deterministic rollup if the model call fails. Same sections, plainer prose."""
    ff = stats.get("flag_flow", {})
    sr = stats.get("standing_risk", {})
    lines = [f"*Linear Weekly · {stats.get('week_label', stats.get('week', ''))}*  "
             f"(runs {stats.get('runs_completed', '?')}/5)",
             f"*Flag flow* — new {ff.get('new', 0)} · worsened {ff.get('worsened', 0)} · "
             f"resolved {ff.get('resolved', 0)} · net {ff.get('net', 0):+d} · "
             f"resolution rate {ff.get('resolution_rate_pct', 0)}%",
             f"*Standing risk* — {sr.get('total_open', 0)} open flags, "
             f"median age {sr.get('median_age_days', 0)}d"]
    for o in sr.get("oldest", []):
        lines.append(f"• {o['id']} [{o['team']}] {o['days']}d — {', '.join(o['rules'])}")
    lines.append("*Team scorecard*")
    for t in stats.get("team_scorecard", []):
        lines.append(f"• {t['team']}: {t['flags']}/{t['active']} ({t['pct']}%) {t.get('wow', '→')}")
    ad = stats.get("adherence", {})
    lines.append(f"*Adherence* — owned {ad.get('owner_pct', 0)}% · project {ad.get('project_pct', 0)}% · "
                 f"described {ad.get('desc_pct', 0)}% · dated {ad.get('dated', 0)}")
    for row in ad.get("by_team_worst_first", [])[:10]:
        lines.append(f"• {row['team']}: owned {row.get('owner_pct', 0)}% · "
                     f"project {row.get('project_pct', 0)}% · "
                     f"described {row.get('desc_pct', 0)}% ({row.get('active_total', 0)} active)")
    tp = stats.get("throughput", {})
    lines.append(f"*Throughput* — {tp.get('total', 0)} completed (prior week {tp.get('prior_total', 'n/a')})")
    for team, n in (tp.get("by_team") or {}).items():
        lines.append(f"• {team}: {n}")
    lines.append("*Concentration*")
    for o in stats.get("concentration", []):
        lines.append(f"• {o['owner']}: {o['flags']} open flags")
    for a in stats.get("aggregates", []):
        lines.append(f"• {a}")
    cands = stats.get("overlap_candidates", [])
    if cands:
        lines.append("*Possible cross-team overlap* (review)")
        for c in cands[:3]:
            lines.append(f"• {c['a']['id']} [{c['a']['team']}] ↔ {c['b']['id']} "
                         f"[{c['b']['team']}] — shared: {', '.join(c['shared'][:4])}")
    return "\n".join(lines)


def weekly_brief(stats: dict) -> str:
    try:
        msg = _client().messages.create(
            model=config.MODEL_WEEKLY, max_tokens=1600, system=WEEKLY_SYSTEM,
            messages=[{"role": "user", "content": json.dumps(stats)}])
        text = "".join(b.text for b in msg.content if b.type == "text").strip()
        if text:
            return text
    except Exception:
        pass
    return fallback_weekly(stats)
