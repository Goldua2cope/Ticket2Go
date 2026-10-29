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
    item_type TEXT NOT NULL
        CHECK (item_type IN ('action', 'decision', 'question', 'problem')),
    content TEXT NOT NULL,
    evidence_refs TEXT,
    severity TEXT
        CHECK (severity IN ('low', 'medium', 'high', 'critical')),
    related_problem_item_id UUID
        REFERENCES meeting_items(item_id) ON DELETE SET NULL
);

CREATE TABLE IF NOT EXISTS jira_issue_cache (
    issue_key TEXT PRIMARY KEY,
    summary TEXT NOT NULL,
    description TEXT,
    issue_type TEXT NOT NULL,
    project_key TEXT NOT NULL,
    parent_issue_key TEXT,
    status TEXT NOT NULL,
    labels TEXT[],
    assignee TEXT,
    updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    synced_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    jira_url TEXT NOT NULL,
    components TEXT[]
);

ALTER TABLE jira_issue_cache ADD COLUMN IF NOT EXISTS issue_type TEXT NOT NULL DEFAULT 'Unknown';
ALTER TABLE jira_issue_cache ALTER COLUMN issue_type DROP DEFAULT;
ALTER TABLE jira_issue_cache ADD COLUMN IF NOT EXISTS parent_issue_key TEXT;
ALTER TABLE jira_issue_cache ADD COLUMN IF NOT EXISTS description TEXT;
ALTER TABLE jira_issue_cache ADD COLUMN IF NOT EXISTS assignee TEXT;
ALTER TABLE jira_issue_cache ADD COLUMN IF NOT EXISTS components TEXT[];


CREATE TABLE IF NOT EXISTS ticket_proposals (
    proposal_id UUID PRIMARY KEY,
    item_id UUID NOT NULL REFERENCES meeting_items(item_id) ON DELETE CASCADE,
    summary TEXT NOT NULL,
    description TEXT,
    assignee_account_id TEXT,
    review_status TEXT NOT NULL DEFAULT 'pending'
        CHECK (review_status IN (
        'pending', 'edited', 'approved', 'rejected'
        )),
    execution_status TEXT NOT NULL DEFAULT 'not_started'
        CHECK (execution_status IN (
        'not_started', 'in_progress', 'completed', 'blocked'
        )),
    jira_issue_key TEXT
        REFERENCES jira_issue_cache(issue_key) ON DELETE SET NULL,
    proposal_type TEXT NOT NULL
        CHECK (proposal_type IN ('create', 'update')),
    evidence JSONB NOT NULL DEFAULT '[]'::jsonb,
    rationale TEXT NOT NULL,
    related_meeting_ids UUID[] NOT NULL DEFAULT '{}',
    issue_type TEXT
        CHECK (issue_type IN (
            'Subtask', 'Task', 'Story', 'Bug', 'Epic'
        )),
    priority TEXT
        CHECK (priority IN (
            'Lowest', 'Low', 'Medium', 'High', 'Highest'
        )),
    -- A Subtask's parent: a Jira key, or the parent suggestion's proposal_id
    -- until that suggestion is created in Jira (then replaced by its key).
    parent_issue_key TEXT,
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    labels TEXT[],
    -- Sprint a new Story, Task or Bug goes into on approval. NULL = backlog.
    target_sprint_id INTEGER CHECK (target_sprint_id > 0)
);
ALTER TABLE ticket_proposals ADD COLUMN IF NOT EXISTS target_sprint_id INTEGER
    CHECK (target_sprint_id > 0);

-- One row per meeting item that revised a pending suggestion from an earlier
-- meeting. The suggestion is listed under its own meeting and each revising one.
CREATE TABLE IF NOT EXISTS proposal_revisions (
    proposal_id UUID NOT NULL REFERENCES ticket_proposals(proposal_id) ON DELETE CASCADE,
    item_id UUID NOT NULL REFERENCES meeting_items(item_id) ON DELETE CASCADE,
    meeting_id UUID NOT NULL REFERENCES meetings(meeting_id),
    change TEXT NOT NULL,
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    PRIMARY KEY (proposal_id, item_id)
);
CREATE INDEX IF NOT EXISTS proposal_revisions_meeting_id_idx ON proposal_revisions (meeting_id);

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

SELECT * FROM meetings;
SELECT * FROM meeting_items;
SELECT * FROM jira_issue_cache;
SELECT * FROM ticket_proposals;
SELECT * FROM problem_assessments;
SELECT * FROM proposal_revisions;