-- ia-reviewer: persisted review records + normalized findings.
--
-- Runs once on first Postgres boot (anything under db/init/ is replayed
-- in lexical order). After that, ReviewStore.ensure_schema() executes
-- the same DDL idempotently at app startup so the tables exist even
-- when Postgres was provisioned before the feature landed.

CREATE TABLE IF NOT EXISTS reviews (
    thread_id            UUID PRIMARY KEY,
    mode                 TEXT NOT NULL CHECK (mode IN ('pr', 'repo')),
    target_url           TEXT NOT NULL,
    ref                  TEXT,                      -- repo-mode only; NULL for PR
    author               TEXT,
    validation_category  TEXT,
    validation_accepted  BOOLEAN,
    overall_severity     TEXT,
    finding_count        INTEGER NOT NULL DEFAULT 0,
    report_markdown      TEXT NOT NULL,             -- mirrors reports/<thread_id>.md
    -- Phase C `state.exploit_proposals` serialised here so approved PoCs +
    -- declined / skipped records survive the BackgroundTask exit. JSONB
    -- so `/reviews/<id>` can return the list as-is and indexed queries
    -- (e.g. "all reviews with at least one approved exploit") stay cheap.
    exploit_proposals    JSONB NOT NULL DEFAULT '[]'::jsonb,
    created_at           TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    completed_at         TIMESTAMPTZ
);

-- For Postgres instances provisioned before this column landed.
-- IF NOT EXISTS makes the ALTER idempotent — safe to replay every
-- startup via `ReviewStore.ensure_schema`.
ALTER TABLE reviews
    ADD COLUMN IF NOT EXISTS exploit_proposals JSONB NOT NULL DEFAULT '[]'::jsonb;

CREATE TABLE IF NOT EXISTS review_findings (
    id           BIGSERIAL PRIMARY KEY,
    thread_id    UUID NOT NULL REFERENCES reviews(thread_id) ON DELETE CASCADE,
    role         TEXT NOT NULL,                     -- dependency | injection | owasp
    file         TEXT,
    line         INTEGER,
    category     TEXT,                              -- A01/A05/sqli/cmd/...
    severity     TEXT NOT NULL,
    issue        TEXT NOT NULL,
    raw          JSONB NOT NULL DEFAULT '{}'::jsonb -- fields we didn't normalize
);

CREATE INDEX IF NOT EXISTS idx_findings_thread ON review_findings (thread_id);
CREATE INDEX IF NOT EXISTS idx_reviews_created ON reviews (created_at DESC);
