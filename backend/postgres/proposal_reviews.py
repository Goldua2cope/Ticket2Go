"""Review storage for the current proposal/cache schema; decisions are final."""

import hashlib
import json
from uuid import UUID

from fastapi import HTTPException

from backend.postgres.ticket_proposals import _row_to_dict

SELECT_PROPOSAL = """SELECT p.*, c.jira_url, c.status AS jira_status FROM ticket_proposals p
    LEFT JOIN jira_issue_cache c ON c.issue_key = p.jira_issue_key"""


def serialize(row):
    data = _row_to_dict(row)
    fields = (
        "summary",
        "description",
        "proposal_type",
        "jira_issue_key",
        "priority",
        "issue_type",
        "parent_issue_key",
        "assignee_account_id",
    )
    data["review_token"] = hashlib.sha256(
        json.dumps([data.get(field) for field in fields]).encode()
    ).hexdigest()
    return data


async def get_proposal(state, proposal_id: UUID):
    row = await state.postgres.fetchrow(
        SELECT_PROPOSAL + " WHERE p.proposal_id=$1", proposal_id
    )
    if row is None:
        raise HTTPException(404, "Suggestion not found.")
    return serialize(row)


async def get_meeting_proposals(state, meeting_id: UUID):
    rows = await state.postgres.fetch(
        SELECT_PROPOSAL + """ JOIN meeting_items mi ON mi.item_id=p.item_id
        WHERE mi.meeting_id=$1 OR $1=ANY(p.related_meeting_ids)
        ORDER BY p.created_at, p.proposal_id""",
        meeting_id,
    )
    return [serialize(row) for row in rows]


async def _with_url(connection, row):
    data = serialize(row)
    cached = (
        await connection.fetchrow(
            "SELECT jira_url, status AS jira_status FROM jira_issue_cache WHERE issue_key=$1",
            row["jira_issue_key"],
        )
        if row["jira_issue_key"]
        else None
    )
    data["jira_url"] = cached["jira_url"] if cached else None
    data["jira_status"] = cached["jira_status"] if cached else None
    return data


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
                    return await _with_url(connection, row), False
                raise HTTPException(
                    409, "This decision is final. Further changes belong in Jira."
                )
            if serialize(row)["review_token"] != token:
                raise HTTPException(
                    409,
                    "This suggestion changed. Refresh its status and review the latest details.",
                )
            if action == "edited":
                row = await connection.fetchrow(
                    """UPDATE ticket_proposals SET summary=$2, description=$3, priority=$4,
                       review_status='edited' WHERE proposal_id=$1 RETURNING *""",
                    proposal_id,
                    edits.summary,
                    edits.description,
                    edits.priority,
                )
            else:
                if (
                    action == "approved"
                    and row["proposal_type"] == "update"
                    and not row["jira_issue_key"]
                ):
                    raise HTTPException(
                        409,
                        "The existing Jira issue is no longer linked to this suggestion.",
                    )
                row = await connection.fetchrow(
                    """UPDATE ticket_proposals SET review_status=$2, execution_status=$3
                       WHERE proposal_id=$1 RETURNING *""",
                    proposal_id,
                    action,
                    "in_progress" if action == "approved" else "not_started",
                )
            result = await _with_url(connection, row)
    return result, True


async def finish(state, proposal_id, issue_key):
    # The caller must cache the issue first to satisfy jira_issue_key's foreign key.
    await state.postgres.execute(
        """UPDATE ticket_proposals SET execution_status='completed', jira_issue_key=$2
           WHERE proposal_id=$1 AND review_status='approved'""",
        proposal_id,
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
