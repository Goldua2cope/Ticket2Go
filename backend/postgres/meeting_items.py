"""Store and read meeting items in PostgreSQL."""

import asyncio
import json
import logging
import re
from datetime import datetime
from uuid import UUID

from starlette.datastructures import State

from backend.ticket_suggestion.schemas import HistoricalItem, ItemType, MeetingItem

logger = logging.getLogger(__name__)

DEFAULT_HISTORY_TYPES = (ItemType.PROBLEM, ItemType.ACTION, ItemType.DECISION)


class MeetingItemStorageError(Exception):
    """A database error safe to display to the user."""


async def upsert_meeting_items(items: list[MeetingItem], config: State) -> list[UUID]:
    """Insert or update items in one transaction and return their ids.

    Problems are written first so actions can reference them through
    related_problem_item_id.
    """
    if not items:
        return []

    problems = [item for item in items if item.item_type == ItemType.PROBLEM]
    others = [item for item in items if item.item_type != ItemType.PROBLEM]

    try:
        async with asyncio.timeout(10):
            async with config.postgres.acquire() as connection:
                async with connection.transaction():
                    for item in problems + others:
                        await _upsert_item(connection, item)
    except Exception as exc:
        logger.warning("Meeting item upsert failed (%s)", type(exc).__name__)
        raise MeetingItemStorageError("Could not save meeting items to PostgreSQL.") from None

    return [item.item_id for item in problems + others]


async def _upsert_item(connection, item: MeetingItem) -> None:
    evidence_refs = json.dumps(
        [ref.model_dump(mode="json") for ref in item.evidence_refs]
    )
    severity = item.severity.value if item.severity is not None else None

    await connection.execute(
        """
        INSERT INTO meeting_items (
            item_id, meeting_id, item_type, content,
            severity, related_problem_item_id, evidence_refs
        )
        VALUES ($1, $2, $3, $4, $5, $6, $7)
        ON CONFLICT (item_id) DO UPDATE SET
            content = EXCLUDED.content,
            severity = EXCLUDED.severity,
            related_problem_item_id = EXCLUDED.related_problem_item_id,
            evidence_refs = EXCLUDED.evidence_refs
        WHERE meeting_items.meeting_id = EXCLUDED.meeting_id
          AND meeting_items.item_type = EXCLUDED.item_type
        """,
        item.item_id,
        item.meeting_id,
        item.item_type.value,
        item.content,
        severity,
        item.related_problem_item_id,
        evidence_refs,
    )


async def fetch_items_for_meetings(
    meeting_ids: list[UUID],
    config: State,
    item_types: tuple[ItemType, ...] = DEFAULT_HISTORY_TYPES,
) -> list[HistoricalItem]:
    """Items recorded in the given meetings, newest meeting first."""
    if not meeting_ids:
        return []

    async with asyncio.timeout(5):
        rows = await config.postgres.fetch(
            """
            SELECT mi.item_id, mi.meeting_id, mi.item_type, mi.content,
                   mi.severity, m.meeting_date,
                   p.review_status, p.execution_status,
                   COALESCE(p.jira_issue_key, p.existing_issue_key) AS tracked_issue_key
            FROM meeting_items AS mi
            JOIN meetings AS m ON m.meeting_id = mi.meeting_id
            LEFT JOIN ticket_proposals AS p ON p.item_id = mi.item_id
            WHERE mi.meeting_id = ANY($1::uuid[])
              AND mi.item_type = ANY($2::text[])
            ORDER BY m.meeting_date DESC NULLS LAST
            """,
            meeting_ids,
            _type_values(item_types),
        )
    return _rows_to_items(rows)


async def search_items_by_keywords(
    keywords: list[str],
    exclude_meeting_id: UUID,
    config: State,
    *,
    since: datetime | None = None,
    limit: int = 15,
    item_types: tuple[ItemType, ...] = DEFAULT_HISTORY_TYPES,
) -> list[HistoricalItem]:
    """Full-text keyword search over items from other meetings.

    Complements vector search: it finds earlier items that use the same
    terms even when their meeting's transcript chunks ranked low.
    """
    terms = []
    for keyword in keywords:
        term = _keyword_to_tsquery_term(keyword)
        if term:
            terms.append(term)
    if not terms:
        return []

    # Any keyword may match; ts_rank orders by how well.
    ts_query = " | ".join(terms)

    async with asyncio.timeout(5):
        rows = await config.postgres.fetch(
            """
            SELECT mi.item_id, mi.meeting_id, mi.item_type, mi.content,
                   mi.severity, m.meeting_date,
                   p.review_status, p.execution_status,
                   COALESCE(p.jira_issue_key, p.existing_issue_key) AS tracked_issue_key
            FROM meeting_items AS mi
            JOIN meetings AS m ON m.meeting_id = mi.meeting_id
            LEFT JOIN ticket_proposals AS p ON p.item_id = mi.item_id
            WHERE mi.meeting_id <> $1
              AND mi.item_type = ANY($2::text[])
              AND ($3::timestamptz IS NULL OR m.meeting_date >= $3)
              AND to_tsvector('english', mi.content) @@ to_tsquery('english', $4)
            ORDER BY ts_rank(to_tsvector('english', mi.content),
                             to_tsquery('english', $4)) DESC
            LIMIT $5
            """,
            exclude_meeting_id,
            _type_values(item_types),
            since,
            ts_query,
            limit,
        )
    return _rows_to_items(rows)


async def fetch_meeting_dates(meeting_ids: list[UUID], config: State) -> dict[str, str]:
    """meeting_id -> ISO meeting date, for meetings that have one."""
    if not meeting_ids:
        return {}

    async with asyncio.timeout(5):
        rows = await config.postgres.fetch(
            """
            SELECT meeting_id, meeting_date
            FROM meetings
            WHERE meeting_id = ANY($1::uuid[]) AND meeting_date IS NOT NULL
            """,
            meeting_ids,
        )

    dates: dict[str, str] = {}
    for row in rows:
        dates[str(row["meeting_id"])] = row["meeting_date"].isoformat()
    return dates


def _keyword_to_tsquery_term(keyword: str) -> str | None:
    """One to_tsquery term. Hyphenated words ("sign-in") become a phrase
    ("sign <-> in") so they match the way PostgreSQL tokenizes the text."""
    parts = []
    for part in re.findall(r"\w+", keyword):
        if part:
            parts.append(part)
    if not parts:
        return None
    if len(parts) == 1:
        return parts[0]
    return "(" + " <-> ".join(parts) + ")"


def _type_values(item_types: tuple[ItemType, ...]) -> list[str]:
    return [item_type.value for item_type in item_types]


def _rows_to_items(rows) -> list[HistoricalItem]:
    items: list[HistoricalItem] = []
    for row in rows:
        meeting_date = row["meeting_date"]
        items.append(
            HistoricalItem(
                item_id=row["item_id"],
                meeting_id=row["meeting_id"],
                meeting_date=meeting_date.isoformat() if meeting_date else None,
                item_type=row["item_type"],
                content=row["content"],
                severity=row["severity"],
                review_status=row["review_status"],
                execution_status=row["execution_status"],
                tracked_issue_key=row["tracked_issue_key"],
            )
        )
    return items
