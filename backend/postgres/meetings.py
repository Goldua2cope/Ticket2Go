"""Store meeting metadata and transcript locations in PostgreSQL."""

import asyncio
import logging
from datetime import datetime
from uuid import UUID
from zoneinfo import ZoneInfo

from starlette.datastructures import State

logger = logging.getLogger(__name__)


class MeetingStorageError(Exception):
    """A database error that is safe to display to the user."""


async def fetch_meeting(meeting_id: UUID, config: State):
    """Return the meeting needed for suggestion generation, or None."""
    try:
        async with asyncio.timeout(5):
            return await config.postgres.fetchrow(
                """
                SELECT meeting_id, meeting_date, processing_status, transcript_blob_uri
                FROM meetings WHERE meeting_id = $1
                """,
                meeting_id,
            )
    except Exception as exc:
        logger.warning("Meeting lookup failed (%s)", type(exc).__name__)
        raise MeetingStorageError("Could not load the meeting from PostgreSQL.") from None


async def create_meeting(meeting_id: str, meeting_date: datetime, config: State):
    """Create a pending record using the same ID as the transcript blob."""
    local_date = meeting_date.astimezone(ZoneInfo("Europe/Stockholm"))
    try:
        async with asyncio.timeout(5):
            await config.postgres.execute(
                """
                INSERT INTO meetings (meeting_id, title, meeting_date, processing_status)
                VALUES ($1, $2, $3, 'pending')
                """,
                UUID(meeting_id),
                f"Meeting {local_date:%Y-%m-%d %H:%M}",
                meeting_date,
            )
    except Exception as exc:
        logger.warning("Meeting creation failed (%s)", type(exc).__name__)
        raise MeetingStorageError(
            "Could not create the meeting in PostgreSQL. Check the database connection and meetings table."
        ) from None


async def update_meeting(
    meeting_id: str,
    status: str,
    config: State,
    transcript_blob_uri: str | None = None,
):
    """Update processing status, retaining any previously saved blob reference."""
    if status not in {"processing", "completed", "failed"}:
        raise ValueError("Invalid meeting processing status.")
    try:
        async with asyncio.timeout(5):
            result = await config.postgres.execute(
                """
                UPDATE meetings
                SET processing_status = $2,
                    transcript_blob_uri = COALESCE($3, transcript_blob_uri)
                WHERE meeting_id = $1
                """,
                UUID(meeting_id),
                status,
                transcript_blob_uri,
            )
            if result != "UPDATE 1":
                raise ValueError("Meeting record was not found.")
    except Exception as exc:
        logger.warning("Meeting update failed (%s)", type(exc).__name__)
        raise MeetingStorageError(
            "Could not update the meeting in PostgreSQL. Any uploaded transcript remains in Blob Storage."
        ) from None


async def list_meetings_with_proposals(config: State, limit: int, offset: int) -> dict:
    """Two reads per page, including meetings with no proposals and each proposal once."""
    from backend.postgres.proposal_reviews import serialize

    async with config.postgres.acquire() as connection:
        async with connection.transaction(isolation="repeatable_read", readonly=True):
            rows = await connection.fetch(
                """SELECT meeting_id, title, meeting_date, processing_status FROM meetings
                   ORDER BY meeting_date DESC NULLS LAST, meeting_id DESC
                   LIMIT $1 OFFSET $2""", limit + 1, offset,
            )
            has_more = len(rows) > limit
            meetings = []
            by_id = {}
            for row in rows[:limit]:
                meeting = dict(row)
                meeting["meeting_id"] = str(row["meeting_id"])
                meeting["proposals"] = []
                meetings.append(meeting)
                by_id[row["meeting_id"]] = meeting
            if meetings:
                proposals = await connection.fetch(
                    """SELECT p.*, mi.meeting_id AS source_meeting_id,
                              c.jira_url, c.status AS jira_status
                       FROM ticket_proposals p
                       JOIN meeting_items mi ON mi.item_id=p.item_id
                       LEFT JOIN jira_issue_cache c ON c.issue_key=p.jira_issue_key
                       WHERE mi.meeting_id=ANY($1::uuid[])
                       ORDER BY p.created_at, p.proposal_id""", list(by_id),
                )
                for row in proposals:
                    proposal = dict(row)
                    source_id = proposal.pop("source_meeting_id")
                    by_id[source_id]["proposals"].append(serialize(proposal))
    return {"meetings": meetings, "next_offset": offset + limit if has_more else None}
