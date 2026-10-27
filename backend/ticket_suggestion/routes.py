"""HTTP entry point for generating suggestions from a saved meeting."""

import asyncio
import logging
from uuid import UUID

from fastapi import APIRouter, HTTPException, Request
from fastapi.responses import JSONResponse

from backend.postgres.meetings import MeetingStorageError, fetch_meeting
from backend.postgres.meeting_items import MeetingItemStorageError
from backend.postgres.ticket_proposals import ProposalStorageError
from backend.storage import TranscriptStorageError, load_meeting_transcript
from backend.ticket_suggestion.suggestion_service import suggest_tickets

logger = logging.getLogger(__name__)
router = APIRouter(tags=["Task suggestions"])


@router.post("/meetings/{meeting_id}/suggestions")
async def generate_suggestions(meeting_id: UUID, request: Request):
    """Generate and save pending task suggestions for a completed meeting.

    Call after the transcription WebSocket sends `done`, using its meeting_id.
    No request body is needed. The response contains proposals and run status;
    this request waits for generation to finish. It does not change Jira.
    """
    state = request.app.state
    try:
        meeting = await fetch_meeting(meeting_id, state)
        if meeting is None:
            raise HTTPException(status_code=404, detail="Meeting not found.")
        if meeting["processing_status"] != "completed":
            raise HTTPException(
                status_code=409,
                detail="Wait until the meeting has completed processing.",
            )
        if not meeting["transcript_blob_uri"]:
            raise HTTPException(
                status_code=409, detail="This meeting has no saved transcript."
            )

        transcription = await load_meeting_transcript(str(meeting_id), state)
        async with asyncio.timeout(300):
            result = await suggest_tickets(
                meeting_id=str(meeting_id),
                transcription=transcription,
                summary=None,
                state=state,
                meeting_date=(
                    meeting["meeting_date"].isoformat()
                    if meeting["meeting_date"]
                    else None
                ),
            )
    except HTTPException:
        raise
    except (
        MeetingStorageError,
        MeetingItemStorageError,
        ProposalStorageError,
        TranscriptStorageError,
    ) as exc:
        raise HTTPException(status_code=503, detail=str(exc)) from None
    except TimeoutError:
        raise HTTPException(
            status_code=504, detail="Task suggestion generation timed out."
        ) from None
    except Exception:
        logger.exception("Task suggestion generation failed for meeting %s", meeting_id)
        raise HTTPException(
            status_code=503,
            detail="Could not generate task suggestions. Please try again.",
        ) from None

    outcome = result["status"]["outcome"]
    status_code = (
        409 if outcome == "already_running" else 502 if outcome == "failed" else 200
    )
    return JSONResponse(
        content={"meeting_id": str(meeting_id), **result}, status_code=status_code
    )
