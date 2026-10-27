ROLLBACK;
BEGIN;

CREATE TABLE IF NOT EXISTS meetings (
    meeting_id UUID PRIMARY KEY,
    title TEXT NOT NULL,
    meeting_date TIMESTAMPTZ,
    transcript_blob_uri TEXT,
    processing_status TEXT NOT NULL DEFAULT 'pending'
        CHECK (processing_status IN (
        'pending', 'processing', 'completed', 'failed'
        ))
);

CREATE TABLE IF NOT EXISTS meeting_items (
    item_id UUID PRIMARY KEY,
    meeting_id UUID NOT NULL REFERENCES meetings(meeting_id),
    item_type TEXT NOT NULL CHECK (item_type IN ('action', 'decision', 'question')),
    content TEXT NOT NULL,
    evidence_refs TEXT
);
-- Alterations needed for compatibility with ticket_suggestion feature
ALTER TABLE meeting_items DROP CONSTRAINT IF EXISTS meeting_items_item_type_check;
ALTER TABLE meeting_items ADD CONSTRAINT meeting_items_item_type_check
    CHECK (item_type IN ('action', 'decision', 'question', 'problem'));
ALTER TABLE meeting_items ADD COLUMN IF NOT EXISTS severity TEXT
    CHECK (severity IN ('low', 'medium', 'high', 'critical'));
ALTER TABLE meeting_items ADD COLUMN IF NOT EXISTS related_problem_item_id UUID
    REFERENCES meeting_items(item_id) ON DELETE SET NULL;


CREATE TABLE IF NOT EXISTS ticket_proposals (
    proposal_id UUID PRIMARY KEY,
    item_id UUID NOT NULL REFERENCES meeting_items(item_id),
    summary TEXT NOT NULL,
    description TEXT,
    assignee_account_id TEXT,
    review_status TEXT NOT NULL DEFAULT 'pending'
        CHECK (review_status IN (
        'pending', 'approved', 'rejected'
        )),
    execution_status TEXT NOT NULL DEFAULT 'not_started'
        CHECK (execution_status IN (
        'not_started', 'in_progress', 'completed', 'blocked'
        )),
    existing_issue_key TEXT,
    jira_issue_key TEXT
);
-- Alterations needed for compatibility with ticket_suggestion feature
ALTER TABLE ticket_proposals ADD COLUMN IF NOT EXISTS proposal_type TEXT
    CHECK (proposal_type IN ('create', 'update'));
UPDATE ticket_proposals SET proposal_type = 'create' WHERE proposal_type IS NULL;  -- old test rows only
ALTER TABLE ticket_proposals ALTER COLUMN proposal_type SET NOT NULL;
ALTER TABLE ticket_proposals ADD COLUMN IF NOT EXISTS evidence JSONB NOT NULL DEFAULT '[]'::jsonb;
ALTER TABLE ticket_proposals ADD COLUMN IF NOT EXISTS rationale TEXT NOT NULL DEFAULT '';
ALTER TABLE ticket_proposals ADD COLUMN IF NOT EXISTS related_meeting_ids UUID[] NOT NULL DEFAULT '{}';

CREATE TABLE IF NOT EXISTS jira_issue_cache (
    issue_key TEXT PRIMARY KEY,
    summary TEXT NOT NULL,
    project_key TEXT NOT NULL,
    status TEXT NOT NULL,
    updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    synced_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

CREATE TABLE IF NOT EXISTS approval_audit (
    event_id UUID PRIMARY KEY,
    proposal_id UUID NOT NULL REFERENCES ticket_proposals(proposal_id),
    action TEXT NOT NULL
        CHECK (action IN ('approved', 'rejected', 'edited')),
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

CREATE TABLE IF NOT EXISTS integration_sync_state (
    integration_id BIGINT PRIMARY KEY,
    last_successful_sync TIMESTAMPTZ,
    sync_status TEXT NOT NULL DEFAULT 'idle'
        CHECK (sync_status IN (
        'idle', 'running', 'succeeded', 'error'
        ))
);

CREATE TABLE IF NOT EXISTS problem_assessments (
    item_id UUID PRIMARY KEY REFERENCES meeting_items(item_id) ON DELETE CASCADE,
    is_problem BOOLEAN NOT NULL,
    recurrence TEXT NOT NULL CHECK (recurrence IN ('new', 'recurring', 'uncertain')),
    recurrence_count INTEGER NOT NULL CHECK (recurrence_count >= 1),
    distinct_meeting_count INTEGER NOT NULL CHECK (distinct_meeting_count >= 1),
    first_observed_at TIMESTAMPTZ,
    last_observed_at TIMESTAMPTZ,
    earlier_occurrences JSONB NOT NULL DEFAULT '[]'::jsonb,
    likely_existing_issue TEXT,
    existing_issue_covers_problem BOOLEAN NOT NULL DEFAULT FALSE,
    existing_issue_resolved BOOLEAN,
    regressed_after_resolution BOOLEAN NOT NULL DEFAULT FALSE,
    unresolved BOOLEAN NOT NULL,
    sufficient_evidence BOOLEAN NOT NULL,
    evidence JSONB NOT NULL DEFAULT '[]'::jsonb,
    rationale TEXT NOT NULL,
    assessed_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

COMMIT;


SELECT * FROM meeting_items;

SELECT * FROM ticket_proposals;

SELECT * FROM jira_issue_cache;

SELECT * FROM problem_assessments;