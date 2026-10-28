"""Keep jira_issue_cache in PostgreSQL in step with Jira.

ticket_proposals.jira_issue_key has a foreign key to this table, so a proposal
can only point at an issue that has been synced here first.
"""

import asyncio
import logging
from datetime import datetime
from typing import Any

from starlette.datastructures import State

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
                            updated_at, synced_at, jira_url, labels
                        )
                        VALUES ($1, $2, $3, $4, $5, $6, $7, $8)
                        ON CONFLICT (issue_key) DO UPDATE SET
                            summary = EXCLUDED.summary,
                            project_key = EXCLUDED.project_key,
                            status = EXCLUDED.status,
                            updated_at = EXCLUDED.updated_at,
                            synced_at = EXCLUDED.synced_at,
                            jira_url = EXCLUDED.jira_url,
                            labels = EXCLUDED.labels
                        """,
                        rows,
                    )
    except Exception as exc:
        logger.warning("Jira issue cache upsert failed (%s)", type(exc).__name__)
        raise JiraCacheStorageError(
            "Could not save Jira issues to PostgreSQL."
        ) from None

    return len(rows)


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
