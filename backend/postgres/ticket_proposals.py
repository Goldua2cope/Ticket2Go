"""Register ticket suggestions in ticket_proposals and read earlier ones.

Suggestions are always registered with the table defaults
(review_status 'pending', execution_status 'not_started'). This module never
changes a proposal that a user has already reviewed.
"""

import asyncio
import json
import logging
from typing import Any
from uuid import UUID

from starlette.datastructures import State

from backend.ticket_suggestion.schemas import PriorProposal, TicketProposal

logger = logging.getLogger(__name__)


class ProposalStorageError(Exception):
    """A database error safe to display to the user."""


async def save_pending_proposals(proposals: list[TicketProposal], config: State) -> list[dict[str, Any]]:
    """Register proposals and return the rows as stored in ticket_proposals.

    New proposals are inserted. A proposal that is still pending is refreshed.
    A proposal the user already approved or rejected is left untouched and is
    not part of the result.
    """
    if not proposals:
        return []

    registered: list[dict[str, Any]] = []
    try:
        async with asyncio.timeout(10):
            async with config.postgres.acquire() as connection:
                async with connection.transaction():
                    for proposal in proposals:
                        row = await _upsert_proposal(connection, proposal)
                        if row is not None:
                            registered.append(_row_to_dict(row))
    except Exception as exc:
        logger.warning("Ticket proposal save failed (%s)", type(exc).__name__)
        raise ProposalStorageError("Could not save ticket suggestions to PostgreSQL.") from None

    return registered


async def _upsert_proposal(connection, proposal: TicketProposal):
    """Insert or refresh one pending proposal. Returns the stored row, or None if skipped."""
    evidence = json.dumps([ref.model_dump(mode="json") for ref in proposal.evidence])

    return await connection.fetchrow(
        """
        INSERT INTO ticket_proposals (
            proposal_id, item_id, proposal_type, summary, description,
            existing_issue_key, rationale, evidence, related_meeting_ids
        )
        VALUES ($1, $2, $3, $4, $5, $6, $7, $8::jsonb, $9::uuid[])
        ON CONFLICT (proposal_id) DO UPDATE SET
            proposal_type = EXCLUDED.proposal_type,
            summary = EXCLUDED.summary,
            description = EXCLUDED.description,
            existing_issue_key = EXCLUDED.existing_issue_key,
            rationale = EXCLUDED.rationale,
            evidence = EXCLUDED.evidence,
            related_meeting_ids = EXCLUDED.related_meeting_ids
        WHERE ticket_proposals.review_status = 'pending'
        RETURNING proposal_id, item_id, proposal_type, summary, description,
                  assignee_account_id, review_status, execution_status,
                  existing_issue_key, jira_issue_key, rationale, evidence,
                  related_meeting_ids
        """,
        proposal.proposal_id,
        proposal.item_id,
        proposal.proposal_type.value,
        proposal.summary,
        proposal.description,
        proposal.existing_issue_key,
        proposal.rationale,
        evidence,
        proposal.related_meeting_ids,
    )


def _row_to_dict(row) -> dict[str, Any]:
    """A ticket_proposals row as a JSON-friendly dict."""
    data = dict(row)
    data["proposal_id"] = str(data["proposal_id"])
    data["item_id"] = str(data["item_id"])

    evidence = data.get("evidence")
    if isinstance(evidence, str):
        evidence = json.loads(evidence)
    data["evidence"] = evidence or []

    meeting_ids = []
    for meeting_id in data.get("related_meeting_ids") or []:
        meeting_ids.append(str(meeting_id))
    data["related_meeting_ids"] = meeting_ids
    return data


async def fetch_related_proposals(
    item_ids: list[UUID],
    issue_keys: list[str],
    config: State,
) -> list[PriorProposal]:
    """Earlier proposals for the given items or touching the given Jira issues,
    with their outcome (review, execution, whether the user edited them).
    The suggestion pipeline uses these as context only."""
    if not item_ids and not issue_keys:
        return []

    async with asyncio.timeout(5):
        rows = await config.postgres.fetch(
            """
            SELECT p.proposal_id, p.item_id, p.proposal_type, p.summary,
                   p.review_status, p.execution_status, p.existing_issue_key,
                   p.jira_issue_key,
                   EXISTS (
                       SELECT 1 FROM approval_audit AS a
                       WHERE a.proposal_id = p.proposal_id AND a.action = 'edited'
                   ) AS edited_by_user
            FROM ticket_proposals AS p
            WHERE p.item_id = ANY($1::uuid[])
               OR p.existing_issue_key = ANY($2::text[])
               OR p.jira_issue_key = ANY($2::text[])
            """,
            item_ids,
            issue_keys,
        )

    proposals: list[PriorProposal] = []
    for row in rows:
        proposals.append(PriorProposal(**dict(row)))
    return proposals
