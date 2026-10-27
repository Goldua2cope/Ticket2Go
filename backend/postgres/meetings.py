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
