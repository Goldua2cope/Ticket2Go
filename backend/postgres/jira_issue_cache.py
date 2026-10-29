"""Keep jira_issue_cache in PostgreSQL in step with Jira.

ticket_proposals.jira_issue_key has a foreign key to this table, so a proposal
can only point at an issue that has been synced here first.
"""

import asyncio
import logging
from datetime import datetime
from typing import Any

from starlette.datastructures import State

from backend.ticket_suggestion.schemas import RESOLVED_STATUS_NAMES, SUBTASK_PARENT_TYPES

logger = logging.getLogger(__name__)


class JiraCacheStorageError(Exception):
    """A database error safe to display to the user."""


async def upsert_issues(
    issues: list[dict[str, Any]], synced_at: datetime, config: State
) -> int:
    """Insert or refresh one cache row per issue and return how many were written.

    `issues` are the documents built by jira_sync._transform_issue.
    All rows are written in one transaction, so a failure writes none of them.
    """
    if not issues:
        return 0

    rows = []
    for issue in issues:
        rows.append(
            (
                issue["issue_key"],
                issue.get("summary") or "(no summary)",
                issue["project_key"],
                issue.get("status") or "Unknown",
                _parse_jira_timestamp(issue.get("jira_updated_at")) or synced_at,
                synced_at,
                issue["jira_url"],
                issue.get("labels") or [],
                issue.get("issue_type") or "Unknown",
                issue.get("parent_issue_key"),
                issue.get("description") or None,
                issue.get("assignee_display_name"),
                issue.get("components") or [],
            )
        )

    try:
        async with asyncio.timeout(30):
            async with config.postgres.acquire() as connection:
                async with connection.transaction():
                    await connection.executemany(
                        """
                        INSERT INTO jira_issue_cache (
                            issue_key, summary, project_key, status,
                            updated_at, synced_at, jira_url, labels,
                            issue_type, parent_issue_key, description, assignee, components
                        )
                        VALUES ($1, $2, $3, $4, $5, $6, $7, $8, $9, $10, $11, $12, $13)
                        ON CONFLICT (issue_key) DO UPDATE SET
                            summary = EXCLUDED.summary,
                            project_key = EXCLUDED.project_key,
                            status = EXCLUDED.status,
                            updated_at = EXCLUDED.updated_at,
                            synced_at = EXCLUDED.synced_at,
                            jira_url = EXCLUDED.jira_url,
                            labels = EXCLUDED.labels,
                            issue_type = EXCLUDED.issue_type,
                            parent_issue_key = EXCLUDED.parent_issue_key,
                            description = EXCLUDED.description,
                            assignee = EXCLUDED.assignee,
                            components = EXCLUDED.components
                        """,
                        rows,
                    )
    except Exception as exc:
        logger.warning("Jira issue cache upsert failed (%s)", type(exc).__name__)
        raise JiraCacheStorageError(
            "Could not save Jira issues to PostgreSQL."
        ) from None

    return len(rows)


async def fetch_subtask_parents(project_key: str, config: State) -> list[dict[str, Any]]:
    """Cached issues a Subtask can go under: open ones first, then by key."""
    async with asyncio.timeout(5):
        rows = await config.postgres.fetch(
            """
            SELECT issue_key, summary, issue_type, status
            FROM jira_issue_cache
            WHERE project_key = $1 AND issue_type = ANY($2::text[])
            ORDER BY length(issue_key), issue_key
            """,
            project_key,
            [str(issue_type) for issue_type in sorted(SUBTASK_PARENT_TYPES)],
        )

    parents = []
    for row in rows:
        parent = dict(row)
        parent["is_done"] = (parent["status"] or "").strip().casefold() in RESOLVED_STATUS_NAMES
        parents.append(parent)
    parents.sort(key=lambda parent: parent["is_done"])  # open first; key order is kept
    return parents


async def cache_is_empty(config: State) -> bool:
    """True when no Jira issue has been cached yet."""
    async with asyncio.timeout(5):
        return not await config.postgres.fetchval(
            "SELECT EXISTS (SELECT 1 FROM jira_issue_cache)"
        )


def _parse_jira_timestamp(value: str | None) -> datetime | None:
    """Jira sends e.g. '2024-05-01T10:20:30.123+0200'."""
    if not value:
        return None
    try:
        return datetime.fromisoformat(value)
    except ValueError:
        return None
