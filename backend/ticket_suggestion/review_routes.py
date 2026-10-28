"""Read and review suggestions, then execute approved Jira actions once."""

import asyncio
import logging
import re
from datetime import datetime, timezone
from typing import Literal
from uuid import UUID

from fastapi import APIRouter, HTTPException, Request
from pydantic import BaseModel, ConfigDict, Field, field_validator

from backend.jira_actions import (
    create_jira_issue,
    append_jira_comment,
    get_jira_issue,
    update_jira_issue,
    get_active_sprint,
    place_new_issue,
)
from backend.postgres import proposal_reviews as storage
from backend.postgres.jira_issue_cache import upsert_issues
from backend.jira_sync import sync_jira_to_cosmos

router = APIRouter(tags=["Task review"])
logger = logging.getLogger(__name__)


class ReviewRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    review_token: str = Field(min_length=64, max_length=64)


class EditRequest(ReviewRequest):
    summary: str = Field(min_length=1, max_length=255)
    description: str = Field(default="", max_length=30000)
    priority: Literal["Lowest", "Low", "Medium", "High", "Highest"] | None = None

    @field_validator("summary")
    @classmethod
    def clean_summary(cls, value):
        if not value.strip():
            raise ValueError("Title must contain text.")
        return value.strip()


async def safe(operation):
    try:
        async with asyncio.timeout(15):
            return await operation
    except HTTPException:
        raise
    except Exception:
        logger.exception("Proposal storage operation failed")
        raise HTTPException(
            503,
            "Could not save or load this suggestion. Refresh its status before trying again.",
        ) from None


@router.get("/meetings/{meeting_id}/suggestions")
async def read_meeting(meeting_id: UUID, request: Request):
    return {
        "meeting_id": str(meeting_id),
        "proposals": await safe(
            storage.get_meeting_proposals(request.app.state, meeting_id)
        ),
    }


@router.get("/proposals/{proposal_id}")
async def read_proposal(proposal_id: UUID, request: Request):
    return await safe(storage.get_proposal(request.app.state, proposal_id))


@router.patch("/proposals/{proposal_id}")
async def edit_proposal(proposal_id: UUID, body: EditRequest, request: Request):
    row, _ = await safe(
        storage.review(
            request.app.state, proposal_id, "edited", body.review_token, body
        )
    )
    return row


@router.post("/proposals/{proposal_id}/reject")
async def reject_proposal(proposal_id: UUID, body: ReviewRequest, request: Request):
    row, _ = await safe(
        storage.review(request.app.state, proposal_id, "rejected", body.review_token)
    )
    return row


@router.post("/proposals/{proposal_id}/approve")
async def approve_proposal(proposal_id: UUID, body: ReviewRequest, request: Request):
    state = request.app.state
    row, claimed = await safe(
        storage.review(state, proposal_id, "approved", body.review_token)
    )
    if not claimed:
        return row  # Never repeat a completed, running or uncertain Jira write.
    try:
        async with asyncio.timeout(90):
            description = re.sub(
                r"\[Ticket2Go update from item [^\]]+\]", "", row["description"] or ""
            ).strip()
            sprint = None
            if row["proposal_type"] == "create":
                # Resolve the destination before creating anything in Jira.
                sprint = await get_active_sprint(state)
                issue = await create_jira_issue(
                    state,
                    summary=row["summary"],
                    description=description,
                    issue_type=row.get("issue_type") or "Task",
                    priority=row.get("priority"),
                    parent_issue_key=row.get("parent_issue_key"),
                    assignee_account_id=row.get("assignee_account_id"),
                )
                sync_jira_to_cosmos(state)
            elif row["proposal_type"] == "update":
                fields = {"description": description, "summary": row["summary"]}
                if row.get("priority"):
                    fields["priority"] = row["priority"]
                issue = await update_jira_issue(
                    state,
                    row["jira_issue_key"],
                    fields=fields,
                )
            else:
                note = f"{row['summary']}\n\n{description}".strip()
                if row.get("priority"):
                    note += f"\n\nSuggested priority: {row['priority']}"
                issue = await append_jira_comment(state, row["jira_issue_key"], note)
            # Populate the developer's cache before assigning the foreign key.
            details = await get_jira_issue(state, issue["key"])
            await upsert_issues(
                [
                    {
                        "issue_key": details["key"],
                        "summary": details["summary"],
                        "project_key": details["project_key"],
                        "status": details["status"],
                        "jira_updated_at": details.get("updated"),
                        "jira_url": details["url"],
                    }
                ],
                datetime.now(timezone.utc),
                state,
            )
            if row["proposal_type"] == "create":
                # Save the issue key before placement so a failed move cannot cause a duplicate creation.
                await storage.link_created_issue(state, proposal_id, issue["key"])
                await place_new_issue(state, issue["key"], sprint)
            result = await storage.finish(state, proposal_id, issue["key"])
            if row["proposal_type"] == "create":
                result["placement_message"] = (
                    f"Added to active sprint: {sprint.get('name') or sprint['id']}."
                    if sprint
                    else "No active sprint was found. The ticket was placed in the backlog."
                )
            return result
    except Exception:
        logger.exception(
            "Jira action needs reconciliation for proposal %s", proposal_id
        )
        await safe(storage.block(state, proposal_id))
        raise HTTPException(
            502,
            "Approval was saved, but Jira completion could not be confirmed. Refresh status and check Jira; automatic retries are disabled to prevent duplicates.",
        ) from None
