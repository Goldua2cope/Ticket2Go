"""Load saved meetings and synchronize Jira when explicitly requested."""
import asyncio
import logging
from uuid import UUID

from azure.core.exceptions import ResourceNotFoundError
from fastapi import APIRouter, HTTPException, Query, Request

from backend.jira_sync import sync_jira_to_cosmos
from backend.postgres.meetings import fetch_meeting, list_meetings_with_proposals

router = APIRouter(tags=["Meeting history"])
logger = logging.getLogger(__name__)


@router.get("/meetings")
async def list_meetings(
    request: Request, limit: int = Query(100, ge=1, le=100), offset: int = Query(0, ge=0),
):
    """Page through saved meetings with proposals and cached Jira links/statuses."""
    try:
        async with asyncio.timeout(15):
            return await list_meetings_with_proposals(request.app.state, limit, offset)
    except Exception:
        logger.exception("Loading meeting history failed")
        raise HTTPException(503, "Could not load saved meetings. Please try again.") from None


@router.get("/meetings/{meeting_id}/summary")
async def meeting_summary(meeting_id: UUID, request: Request):
    """Load the saved summary on demand; full summaries live in Cosmos, not PostgreSQL."""
    try:
        async with asyncio.timeout(15):
            if await fetch_meeting(meeting_id, request.app.state) is None:
                raise HTTPException(404, "Meeting not found.")
            document_id = f"meeting:{meeting_id}:summary"
            try:
                document = await request.app.state.cosmos_container.read_item(
                    item=document_id, partition_key=document_id,
                )
            except ResourceNotFoundError:
                return {"meeting_id": str(meeting_id), "summary": None}
            return {"meeting_id": str(meeting_id), "summary": document.get("text") or None}
    except HTTPException:
        raise
    except Exception:
        logger.exception("Loading meeting summary failed")
        raise HTTPException(503, "Could not load this meeting's saved summary. Your suggestions are still available.") from None


@router.post("/meetings/refresh")
async def refresh_saved_data(request: Request):
    """Sync Jira into Cosmos and PostgreSQL before the frontend reloads history."""
    try:
        async with asyncio.timeout(300):
            await sync_jira_to_cosmos(request.app.state)
        return {"status": "completed"}
    except TimeoutError:
        raise HTTPException(504, "Jira synchronization timed out. Please try again.") from None
    except Exception:
        logger.exception("Refreshing saved Jira data failed")
        raise HTTPException(503, "Could not synchronize Jira. Please try again.") from None
