"""Read and review suggestions, then execute approved Jira actions once."""

import asyncio
import logging
import re
from datetime import datetime, timezone
from typing import Literal
from uuid import UUID

from fastapi import APIRouter, HTTPException, Query, Request
from pydantic import BaseModel, ConfigDict, Field, field_validator

from backend.jira_actions import (
    JiraActionError,
    create_jira_issue,
    append_jira_comment,
    get_jira_issue,
    update_jira_issue,
    get_board_sprints,
    get_issue_sprint,
    place_new_issue,
)
from backend.postgres import proposal_reviews as storage
from backend.postgres.jira_issue_cache import fetch_subtask_parents, upsert_issues
from backend.ticket_suggestion.schemas import (
    SPRINT_ISSUE_TYPES,
    SUBTASK_PARENT_TYPES,
    IssueType,
    parent_suggestion_id,
)
from backend.jira_sync import sync_jira_to_cosmos

router = APIRouter(tags=["Task review"])
logger = logging.getLogger(__name__)

ISSUE_KEY_PATTERN = r"^[A-Z][A-Z0-9_]*-[0-9]+$"


class ReviewRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    review_token: str = Field(min_length=64, max_length=64)


class EditRequest(ReviewRequest):
    summary: str = Field(min_length=1, max_length=255)
    description: str = Field(default="", max_length=30000)
    priority: Literal["Lowest", "Low", "Medium", "High", "Highest"] | None = None
    issue_type: Literal["Epic", "Story", "Task", "Bug", "Subtask"] | None = None
    # A Jira key, or a parent suggestion's proposal_id. Only used for a Subtask.
    parent_issue_key: str | None = Field(default=None, max_length=64)
    # Sprint for a new Story, Task or Bug; None = backlog. Ignored for other types.
    target_sprint_id: int | None = Field(default=None, ge=1)

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


# These two are declared before /proposals/{proposal_id}, so their names
# aren't read as a proposal id.
@router.get("/proposals/parent-sprint")
async def read_parent_sprint(
    request: Request, issue_key: str = Query(pattern=ISSUE_KEY_PATTERN)
):
    """The sprint a subtask under this issue would join (it follows its parent)."""
    state = request.app.state
    if not issue_key.startswith(f"{state.jira_project_key}-"):
        raise HTTPException(422, f"{issue_key} is not in {state.jira_project_key}.")
    try:
        async with asyncio.timeout(20):
            return {"issue_key": issue_key, "sprint": await get_issue_sprint(state, issue_key)}
    except Exception:
        logger.exception("Loading the parent's sprint from Jira failed")
        raise HTTPException(503, "Could not load the parent's sprint from Jira.") from None


@router.get("/proposals/sprint-options")
async def read_sprint_options(request: Request):
    """Active and future sprints a new issue can be placed in."""
    try:
        async with asyncio.timeout(20):
            return {"sprints": await get_board_sprints(request.app.state)}
    except Exception:
        logger.exception("Loading sprints from Jira failed")
        raise HTTPException(503, "Could not load sprints from Jira.") from None


@router.get("/proposals/{proposal_id}")
async def read_proposal(proposal_id: UUID, request: Request):
    return await safe(storage.get_proposal(request.app.state, proposal_id))


@router.get("/proposals/{proposal_id}/parent-options")
async def read_parent_options(proposal_id: UUID, request: Request):
    """Existing Stories, Tasks and Bugs this suggestion can become a Subtask of."""
    state = request.app.state
    await safe(storage.get_proposal(state, proposal_id))  # 404 for an unknown suggestion
    return {"issues": await safe(fetch_subtask_parents(state.jira_project_key, state))}


@router.patch("/proposals/{proposal_id}")
async def edit_proposal(proposal_id: UUID, body: EditRequest, request: Request):
    if body.target_sprint_id is not None:
        await _resolve_destination(request.app.state, body.target_sprint_id)
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
    current = await safe(storage.get_proposal(state, proposal_id))
    sprint = None
    places_in_sprint = (
        current["proposal_type"] == "create"
        and (current.get("issue_type") or IssueType.TASK) in SPRINT_ISSUE_TYPES
    )
    if current["review_status"] in ("pending", "edited"):
        await _check_parent_in_jira(state, current)
        if places_in_sprint:
            # Checked before the approval is saved, so a sprint closed since the
            # edit gives a clear message instead of a blocked approval.
            sprint = await _resolve_destination(state, current.get("target_sprint_id"))
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
            issue_type = row.get("issue_type") or IssueType.TASK
            if row["proposal_type"] == "create":
                issue = await create_jira_issue(
                    state,
                    summary=row["summary"],
                    description=description,
                    issue_type=issue_type,
                    priority=row.get("priority"),
                    parent_issue_key=row.get("parent_issue_key"),
                    assignee_account_id=row.get("assignee_account_id"),
                )
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
                        "issue_type": details.get("issue_type"),
                        "parent_issue_key": details.get("parent_issue_key"),
                        "description": details.get("description"),
                        "assignee_display_name": details.get("assignee_display_name"),
                        "components": details.get("components"),
                    }
                ],
                datetime.now(timezone.utc),
                state,
            )
            if row["proposal_type"] == "create":
                # Save the issue key before placement so a failed move cannot cause a duplicate creation.
                await storage.link_created_issue(state, proposal_id, issue["key"])
                if places_in_sprint:
                    await place_new_issue(state, issue["key"], sprint)
            result = await storage.finish(state, proposal_id, issue["key"])
            if row["proposal_type"] == "create":
                result["placement_message"] = _placement_message(
                    issue_type, sprint, row.get("parent_issue_key")
                )
            _sync_in_background(state)
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


async def _check_parent_in_jira(state, proposal) -> None:
    """Check a Subtask's parent live in Jira before the approval is saved, so an
    out-of-date cache can't leave a blocked approval."""
    if proposal["proposal_type"] != "create" or proposal.get("issue_type") != IssueType.SUBTASK:
        return
    key = proposal.get("parent_issue_key")
    if parent_suggestion_id(key) is not None:
        key = proposal.get("parent_proposal_issue_key")  # the parent suggestion's Jira key, if created
    if not key:
        return  # storage.review explains what's missing
    try:
        parent = await get_jira_issue(state, key)
    except Exception:
        raise HTTPException(409, f"Could not find parent {key} in Jira. Choose another parent.") from None
    if (
        parent.get("project_key") != state.jira_project_key
        or parent.get("issue_type") not in SUBTASK_PARENT_TYPES
    ):
        raise HTTPException(409, f"{key} can't be a parent. Subtasks go under a story, task or bug.")


def _sync_in_background(state) -> None:
    """Refresh the Cosmos DB index with the changed issue without delaying or
    failing the approval. Uses the same app.state.jira_sync_task as main.py,
    so only one sync runs at a time."""
    running = getattr(state, "jira_sync_task", None)
    if running is not None and not running.done():
        return  # the running sync, or the next one, picks the change up
    state.jira_sync_task = asyncio.create_task(_run_sync(state), name="jira-sync")


async def _run_sync(state) -> None:
    try:
        await sync_jira_to_cosmos(state)
    except Exception:
        logger.exception("Jira sync after approval failed; the next sync will retry.")


async def _resolve_destination(state, sprint_id: int | None) -> dict | None:
    """The open sprint with this id, or None (the backlog) when no sprint is chosen."""
    if sprint_id is None:
        return None
    try:
        sprints = await get_board_sprints(state)
    except JiraActionError as exc:
        raise HTTPException(409, str(exc)) from None
    except Exception:
        logger.exception("Checking the chosen sprint failed")
        raise HTTPException(503, "Could not check the sprint in Jira. Try again.") from None
    for sprint in sprints:
        if sprint["id"] == sprint_id:
            return sprint
    raise HTTPException(
        409, "That sprint is closed or no longer on the board. Edit the suggestion to choose another."
    )


def _placement_message(issue_type, sprint, parent_key) -> str:
    if issue_type == IssueType.SUBTASK:
        return f"Created under {parent_key}. It follows that issue's sprint."
    if issue_type == IssueType.EPIC:
        return "Epics are not placed in a sprint."
    if sprint:
        return f"Added to sprint: {sprint.get('name') or sprint['id']}."
    return "Placed in the backlog."
