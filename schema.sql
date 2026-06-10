-- REFERENCE ONLY. The authoritative schema lives in src/db.py (init_schema),
-- which runs CREATE/ALTER IF NOT EXISTS on every start — no manual step needed.
create table if not exists sentinel_state (
    id text primary key default 'singleton',
    mode text not null default 'observe',        -- observe | live
    last_daily_run timestamptz,
    last_weekly_run timestamptz,
    open_flags jsonb not null default '{}'::jsonb,
    dismissed jsonb not null default '{}'::jsonb,
    owner_streaks jsonb not null default '{}'::jsonb,
    flag_first_seen jsonb not null default '{}'::jsonb,
    updated_at timestamptz not null default now()
);
create table if not exists daily_history (
    day date primary key,
    data jsonb not null,
    recorded_at timestamptz not null default now()
);
create table if not exists weekly_history (
    week text primary key,                        -- e.g. 2026-W23
    by_team jsonb not null default '{}'::jsonb,
    by_rule jsonb not null default '{}'::jsonb,
    by_severity jsonb not null default '{}'::jsonb,
    stats jsonb,
    recorded_at timestamptz not null default now()
);
