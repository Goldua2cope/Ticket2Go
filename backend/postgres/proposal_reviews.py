"""Review storage for the current proposal/cache schema; decisions are final."""

import hashlib
import json
from uuid import UUID

from fastapi import HTTPException

from backend.postgres.ticket_proposals import _row_to_dict
from backend.ticket_suggestion.schemas import (
    RESOLVED_STATUS_NAMES,
    SPRINT_ISSUE_TYPES,
    SUBTASK_PARENT_TYPES,
    IssueType,
    parent_suggestion_id,
)

# Every read of a proposal returns these columns, so all views show the same data.
# parent_issue_key holds a Jira key, or a parent suggestion's proposal_id until
# that suggestion is in Jira; the two parent joins cover both cases.
_PROPOSAL_COLUMNS = """p.*, c.jira_url, c.status AS jira_status,
        parent.status AS parent_issue_status,
        pp.summary AS parent_proposal_summary,
        pp.review_status AS parent_proposal_review_status,
        pp.jira_issue_key AS parent_proposal_issue_key,
        origin.meeting_id AS origin_meeting_id,
        (SELECT COALESCE(json_agg(json_build_object(
                    'meeting_id', r.meeting_id, 'meeting_title', rm.title,
                    'change', r.change, 'created_at', r.created_at
                ) ORDER BY r.created_at), '[]'::json)
         FROM proposal_revisions r JOIN meetings rm ON rm.meeting_id = r.meeting_id
         WHERE r.proposal_id = p.proposal_id) AS revisions"""
_PROPOSAL_JOINS = """LEFT JOIN jira_issue_cache c ON c.issue_key = p.jira_issue_key
    LEFT JOIN jira_issue_cache parent ON parent.issue_key = p.parent_issue_key
    LEFT JOIN ticket_proposals pp ON pp.proposal_id::text = p.parent_issue_key
    JOIN meeting_items origin_item ON origin_item.item_id = p.item_id
    JOIN meetings origin ON origin.meeting_id = origin_item.meeting_id"""

SELECT_PROPOSAL = f"SELECT {_PROPOSAL_COLUMNS} FROM ticket_proposals p {_PROPOSAL_JOINS}"

# A meeting's suggestions: the ones created from its items, plus earlier
# suggestions it revised. A revised suggestion is listed under both meetings.
_MEETING_PROPOSALS = f"""
    WITH listed AS (
        SELECT p.proposal_id, mi.meeting_id
        FROM ticket_proposals p JOIN meeting_items mi ON mi.item_id = p.item_id
        WHERE mi.meeting_id = ANY($1::uuid[])
        UNION
        SELECT proposal_id, meeting_id FROM proposal_revisions
        WHERE meeting_id = ANY($1::uuid[])
    )
    SELECT {_PROPOSAL_COLUMNS}, listed.meeting_id AS listed_meeting_id
    FROM listed JOIN ticket_proposals p ON p.proposal_id = listed.proposal_id
    {_PROPOSAL_JOINS}
    ORDER BY p.created_at, p.proposal_id"""


def serialize(row):
    data = _row_to_dict(row)
    if isinstance(data.get("revisions"), str):  # asyncpg returns json as text
        data["revisions"] = json.loads(data["revisions"])
    if data.get("origin_meeting_id") is not None:
        data["origin_meeting_id"] = str(data["origin_meeting_id"])
    fields = (
        "summary",
        "description",
        "proposal_type",
        "jira_issue_key",
        "priority",
        "issue_type",
        "parent_issue_key",
        "assignee_account_id",
        "target_sprint_id",
    )
    data["review_token"] = hashlib.sha256(
        json.dumps([data.get(field) for field in fields]).encode()
    ).hexdigest()
    # Shown as a warning only; Jira allows a subtask under a finished parent.
    data["parent_issue_done"] = (
        (data.get("parent_issue_status") or "").strip().casefold() in RESOLVED_STATUS_NAMES
    )
    return data


async def get_proposal(state, proposal_id: UUID):
    row = await state.postgres.fetchrow(
        SELECT_PROPOSAL + " WHERE p.proposal_id=$1", proposal_id
    )
    if row is None:
        raise HTTPException(404, "Suggestion not found.")
    return serialize(row)


async def fetch_proposals_for_meetings(db, meeting_ids: list[UUID]) -> list[tuple[UUID, dict]]:
    """(meeting_id, proposal) pairs for the given meetings. `db` is the pool or a
    connection, so callers can read inside their own transaction."""
    rows = await db.fetch(_MEETING_PROPOSALS, list(meeting_ids))
    pairs = []
    for row in rows:
        data = dict(row)
        meeting_id = data.pop("listed_meeting_id")
        pairs.append((meeting_id, serialize(data)))
    return pairs


async def get_meeting_proposals(state, meeting_id: UUID):
    return [proposal for _, proposal in await fetch_proposals_for_meetings(state.postgres, [meeting_id])]


async def _select(connection, proposal_id):
    """The proposal as the API returns it, read inside the caller's transaction."""
    return serialize(
        await connection.fetchrow(SELECT_PROPOSAL + " WHERE p.proposal_id=$1", proposal_id)
    )


async def review(state, proposal_id: UUID, action: str, token: str, edits=None):
    """Commit approval before Jira is called, so concurrent/retried requests cannot repeat it."""
    async with state.postgres.acquire() as connection:
        async with connection.transaction():
            row = await connection.fetchrow(
                "SELECT * FROM ticket_proposals WHERE proposal_id=$1 FOR UPDATE",
                proposal_id,
            )
            if row is None:
                raise HTTPException(404, "Suggestion not found.")
            if row["review_status"] in {"approved", "rejected"}:
                if row["review_status"] == action:
                    return await _select(connection, proposal_id), False
                raise HTTPException(
                    409, "This decision is final. Further changes belong in Jira."
                )
            if serialize(row)["review_token"] != token:
                raise HTTPException(
                    409,
                    "This suggestion changed. Refresh its status and review the latest details.",
                )
            if action == "edited":
                issue_type, parent_key = await _checked_work_type(
                    connection, row, edits, state.jira_project_key
                )
                # Only a new Story, Task or Bug has a sprint; the route has
                # already checked that the sprint is open.
                target_sprint_id = (
                    edits.target_sprint_id
                    if row["proposal_type"] == "create" and issue_type in SPRINT_ISSUE_TYPES
                    else None
                )
                await connection.execute(
                    """UPDATE ticket_proposals SET summary=$2, description=$3, priority=$4,
                       issue_type=$5, parent_issue_key=$6, target_sprint_id=$7,
                       review_status='edited' WHERE proposal_id=$1""",
                    proposal_id,
                    edits.summary,
                    edits.description,
                    edits.priority,
                    issue_type,
                    parent_key,
                    target_sprint_id,
                )
            else:
                parent_key = row["parent_issue_key"]
                if action == "approved":
                    if row["proposal_type"] == "update" and not row["jira_issue_key"]:
                        raise HTTPException(
                            409,
                            "The existing Jira issue is no longer linked to this suggestion.",
                        )
                    if row["proposal_type"] == "create" and row["issue_type"] == IssueType.SUBTASK:
                        # Swaps a parent suggestion for its real Jira key, or raises
                        # before anything is saved if it isn't in Jira yet.
                        parent_key = await _approved_parent_key(connection, row)
                await connection.execute(
                    """UPDATE ticket_proposals SET review_status=$2, execution_status=$3,
                       parent_issue_key=$4 WHERE proposal_id=$1""",
                    proposal_id,
                    action,
                    "in_progress" if action == "approved" else "not_started",
                    parent_key,
                )
            result = await _select(connection, proposal_id)
    return result, True


async def _checked_work_type(connection, row, edits, project_key: str):
    """(issue_type, parent_issue_key) for an edit, checked against Jira's rules.
    Only new issues have a work type, and only a Subtask has a parent."""
    if row["proposal_type"] != "create":
        return row["issue_type"], row["parent_issue_key"]
    issue_type = edits.issue_type or row["issue_type"] or IssueType.TASK
    if issue_type != IssueType.SUBTASK:
        return issue_type, None

    parent = (edits.parent_issue_key or "").strip()
    if not parent:
        raise HTTPException(422, "A subtask needs a parent story, task or bug.")

    suggestion_id = parent_suggestion_id(parent)
    if suggestion_id is not None:
        allowed = await connection.fetchval(
            """SELECT 1 FROM ticket_proposals
               WHERE proposal_id=$1 AND proposal_id<>$2 AND proposal_type='create'
                 AND COALESCE(issue_type, 'Task') = ANY($3::text[])
                 AND review_status<>'rejected'""",
            suggestion_id,
            row["proposal_id"],
            [str(parent_type) for parent_type in SUBTASK_PARENT_TYPES],
        )
        if allowed is None:
            raise HTTPException(
                422, "The parent suggestion must be a story, task or bug that hasn't been rejected."
            )
        return issue_type, str(suggestion_id)

    cached_type = await connection.fetchval(
        "SELECT issue_type FROM jira_issue_cache WHERE issue_key=$1 AND project_key=$2",
        parent,
        project_key,
    )
    if cached_type not in SUBTASK_PARENT_TYPES:
        raise HTTPException(422, f"{parent} is not a story, task or bug in {project_key}.")
    return issue_type, parent


async def _approved_parent_key(connection, row) -> str:
    """The Jira key a Subtask will be created under. A parent that is still a
    suggestion must be approved and created in Jira first."""
    parent = row["parent_issue_key"]
    suggestion_id = parent_suggestion_id(parent)
    if parent and suggestion_id is None:
        return parent  # already a Jira key
    if suggestion_id is None:
        raise HTTPException(409, "Choose a parent story, task or bug before approving this subtask.")

    suggestion = await connection.fetchrow(
        "SELECT summary, jira_issue_key, execution_status FROM ticket_proposals WHERE proposal_id=$1",
        suggestion_id,
    )
    if (
        suggestion is not None
        and suggestion["jira_issue_key"]
        and suggestion["execution_status"] == "completed"
    ):
        return suggestion["jira_issue_key"]
    name = f' "{suggestion["summary"]}"' if suggestion is not None else ""
    raise HTTPException(
        409,
        f"Approve the parent suggestion{name} first, "
        "or choose an existing story, task or bug as the parent.",
    )


async def finish(state, proposal_id, issue_key):
    # The caller must cache the issue first to satisfy jira_issue_key's foreign key.
    async with state.postgres.acquire() as connection:
        async with connection.transaction():
            await connection.execute(
                """UPDATE ticket_proposals SET execution_status='completed', jira_issue_key=$2
                   WHERE proposal_id=$1 AND review_status='approved'""",
                proposal_id,
                issue_key,
            )
            # Subtask suggestions waiting for this one now point at the real issue.
            await connection.execute(
                """UPDATE ticket_proposals SET parent_issue_key=$2
                   WHERE parent_issue_key=$1 AND review_status IN ('pending', 'edited')""",
                str(proposal_id),
                issue_key,
            )
    return await get_proposal(state, proposal_id)


async def block(state, proposal_id):
    await state.postgres.execute(
        """UPDATE ticket_proposals SET execution_status='blocked'
           WHERE proposal_id=$1 AND execution_status='in_progress'""",
        proposal_id,
    )


async def link_created_issue(state, proposal_id, issue_key):
    """Retain the created issue/link if later sprint placement fails. Cache it first."""
    await state.postgres.execute(
        """UPDATE ticket_proposals SET jira_issue_key=$2
           WHERE proposal_id=$1 AND review_status='approved' AND execution_status='in_progress'""",
        proposal_id, issue_key,
    )
