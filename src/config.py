"""Team tiering, thresholds, and rule applicability. Edit thresholds here, nowhere else."""

TIER_A = {  # execution teams — full ruleset
    "Software", "Seraph", "Stratus", "Hardware", "Elijah", "Dispersion",
    "Sensing", "Forecaster", "Research", "Meteorology", "Field Operations",
    "Logistics", "Test Operations", "Testing", "Operator",
}
TIER_B = {  # functional & incident — relaxed (no dead-WIP / cycle rules)
    "Business Development", "Brand", "Regulatory Affairs", "Regulatory",
    "Operations", "Manufacturing", "Flight Incidents",
}
TIER_C = {  # containers / synced / dormant — overdue-only, excluded from hygiene scoring
    "Program Planning", "Projects", "Operating Cadence", "Quarterly", "Monthly",
    "Alignment", "Production", "R&D", "Research Testing", "VVO", "DISCO",
}

# Teams whose field hygiene must never be flagged (external system of record).
SYNCED_TEAMS = {"Production"}


def tier_of(team: str) -> str:
    if team in TIER_A:
        return "A"
    if team in TIER_B:
        return "B"
    if team in TIER_C:
        return "C"
    return "A"  # unknown teams default to full ruleset; surfaced for classification


# Which rules run in which tier. Keep in sync with the spec.
RULES_BY_TIER = {
    "A": {
        "overdue", "approaching_not_started", "stuck_triage", "unowned_active",
        "unowned_dated", "done_with_open_children", "dead_wip", "abandoned",
        "no_project", "thin_deliverable",
    },
    "B": {
        "overdue", "approaching_not_started", "stuck_triage", "unowned_active",
        "unowned_dated", "done_with_open_children", "abandoned", "thin_deliverable",
    },
    "C": {"overdue"},
}

THRESHOLDS = {
    "stuck_triage_days": 10,
    "approaching_days": 5,
    "dead_wip_days": 7,
    "abandoned_days": 21,
    "grace_hours": 48,        # issues younger than this are skipped
    "flood_guard": 0.5,       # >50% of a team's active issues -> aggregate, not itemized
    "rules_for_critical": 3,  # an issue tripping this many rules escalates to Critical
}

# Models. Daily brief is short and formulaic -> Haiku. Weekly rollup carries the
# direction read and recommendations -> Opus (quality over the few extra cents;
# one call per week).
MODEL_DAILY = "claude-haiku-4-5"
MODEL_WEEKLY = "claude-opus-4-8"

# Cross-team overlap candidates fed to the weekly model (computed in code).
OVERLAP_MIN_SHARED_TOKENS = 2     # shared meaningful title tokens required
OVERLAP_MIN_COEFFICIENT = 0.6     # |intersection| / min(|A|,|B|)
OVERLAP_MAX_CANDIDATES = 8

# Daily run window guard (America/Los_Angeles). GHA cron is best-effort and often
# late; accept any start inside this window and dedupe on "already ran today".
RUN_WINDOW_HOURS = (9, 11)   # inclusive

# Daily register rendering.
LINEAR_WORKSPACE_SLUG = "rainmaker-technology-corp"
CLUSTER_MIN_SIZE = 3   # identical-signature groups of this size+ collapse to one block
