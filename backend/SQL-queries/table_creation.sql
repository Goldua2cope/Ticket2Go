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

COMMIT;
