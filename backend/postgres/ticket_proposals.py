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

from backend.ticket_suggestion.schemas import PriorProposal, ProposalRevision, TicketProposal

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
            jira_issue_key, rationale, evidence, related_meeting_ids, priority,
            issue_type, parent_issue_key
        )
        VALUES ($1, $2, $3, $4, $5, $6, $7, $8::jsonb, $9::uuid[], $10, $11, $12)
        ON CONFLICT (proposal_id) DO UPDATE SET
            proposal_type = EXCLUDED.proposal_type,
            summary = EXCLUDED.summary,
            description = EXCLUDED.description,
            jira_issue_key = EXCLUDED.jira_issue_key,
            rationale = EXCLUDED.rationale,
            evidence = EXCLUDED.evidence,
            related_meeting_ids = EXCLUDED.related_meeting_ids,
            priority = EXCLUDED.priority,
            issue_type = EXCLUDED.issue_type,
            parent_issue_key = EXCLUDED.parent_issue_key
        WHERE ticket_proposals.review_status = 'pending'
        RETURNING proposal_id, item_id, proposal_type, summary, description,
                  assignee_account_id, review_status, execution_status,
                  jira_issue_key, rationale, evidence, related_meeting_ids, priority,
                  issue_type, parent_issue_key
        """,
        proposal.proposal_id,
        proposal.item_id,
        proposal.proposal_type.value,
        proposal.summary,
        proposal.description,
        # The model calls the update target existing_issue_key; the table stores it
        # in jira_issue_key, which must exist in jira_issue_cache (or be NULL).
        proposal.existing_issue_key,
        proposal.rationale,
        evidence,
        proposal.related_meeting_ids,
        proposal.priority,
        proposal.issue_type,
        proposal.parent_issue_key,
    )


async def apply_revisions(
    revisions: list[ProposalRevision],
    config: State,
    meeting_date: str | None = None,
) -> list[dict[str, Any]]:
    """Append each change to its pending suggestion and return the revised rows.

    The change is appended as a dated note, never rewritten, so two changes to
    the same suggestion can't overwrite each other. A marker per revising item
    stops a re-run from appending the same note twice. Suggestions the user
    already approved or rejected are left untouched, and so are suggestions
    from a meeting held on or after the revising meeting (e.g. when an older
    meeting is re-run).
    """
    if not revisions:
        return []

    revised: dict[str, dict[str, Any]] = {}
    try:
        async with asyncio.timeout(10):
            async with config.postgres.acquire() as connection:
                async with connection.transaction():
                    for revision in revisions:
                        row = await _append_revision(connection, revision, meeting_date)
                        if row is not None:
                            data = _row_to_dict(row)
                            revised[data["proposal_id"]] = data
    except Exception as exc:
        logger.warning("Revising ticket proposals failed (%s)", type(exc).__name__)
        raise ProposalStorageError("Could not revise earlier ticket suggestions in PostgreSQL.") from None

    return list(revised.values())


def revision_marker(revision: ProposalRevision) -> str:
    return f"[Ticket2Go update from item {revision.item_id}]"


async def _append_revision(connection, revision: ProposalRevision, meeting_date: str | None):
    marker = revision_marker(revision)
    when = f" ({meeting_date[:10]})" if meeting_date else ""
    note = f"\n\nUpdate from a later meeting{when}: {revision.change} {marker}"
    evidence = json.dumps([ref.model_dump(mode="json") for ref in revision.evidence])

    row = await connection.fetchrow(
        """
        UPDATE ticket_proposals SET
            description = COALESCE(description, '') || $2,
            evidence = evidence || $3::jsonb,
            related_meeting_ids = CASE
                WHEN $4::uuid = ANY(related_meeting_ids) THEN related_meeting_ids
                ELSE array_append(related_meeting_ids, $4::uuid)
            END
        WHERE proposal_id = $1
          AND review_status = 'pending'
          -- Each item revises a suggestion once. The marker check also covers
          -- revisions made before proposal_revisions existed.
          AND position($5 IN COALESCE(description, '')) = 0
          AND NOT EXISTS (
              SELECT 1 FROM proposal_revisions AS r
              WHERE r.proposal_id = $1 AND r.item_id = $6
          )
          -- Only a later meeting may revise. If either date is missing, skip.
          AND EXISTS (
              SELECT 1
              FROM meeting_items AS mi
              JOIN meetings AS origin ON origin.meeting_id = mi.meeting_id
              JOIN meetings AS reviser ON reviser.meeting_id = $4::uuid
              WHERE mi.item_id = ticket_proposals.item_id
                AND origin.meeting_date < reviser.meeting_date
          )
        RETURNING proposal_id, item_id, proposal_type, summary, description,
                  assignee_account_id, review_status, execution_status,
                  jira_issue_key, rationale, evidence, related_meeting_ids, priority,
                  issue_type, parent_issue_key
        """,
        revision.proposal_id,
        note,
        evidence,
        revision.meeting_id,
        marker,
        revision.item_id,
    )
    if row is not None:
        # Lists the suggestion under the revising meeting too (proposal_reviews).
        await connection.execute(
            """
            INSERT INTO proposal_revisions (proposal_id, item_id, meeting_id, change)
            VALUES ($1, $2, $3, $4)
            ON CONFLICT (proposal_id, item_id) DO NOTHING
            """,
            revision.proposal_id,
            revision.item_id,
            revision.meeting_id,
            revision.change,
        )
    return row


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
            SELECT p.proposal_id, p.item_id, p.proposal_type, p.summary, p.description,
                   p.review_status, p.execution_status, p.jira_issue_key, p.priority, p.issue_type,
                   (p.review_status = 'edited') AS edited_by_user
            FROM ticket_proposals AS p
            WHERE p.item_id = ANY($1::uuid[])
               OR p.jira_issue_key = ANY($2::text[])
            """,
            item_ids,
            issue_keys,
        )

    proposals: list[PriorProposal] = []
    for row in rows:
        proposals.append(PriorProposal(**dict(row)))
    return proposals

