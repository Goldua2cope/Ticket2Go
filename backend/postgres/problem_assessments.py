"""Store recurrence assessments of current-meeting problems.

Keeps recurrence visible even when no ticket is proposed for a problem, and
lets later analysis compare assessments with what users decided.
"""

# ---------------------------------------------------------------------------
# DATABASE CHANGE NEEDED (new table)
#
# CREATE TABLE IF NOT EXISTS problem_assessments (
#     item_id UUID PRIMARY KEY REFERENCES meeting_items(item_id) ON DELETE CASCADE,
#     is_problem BOOLEAN NOT NULL,
#     recurrence TEXT NOT NULL CHECK (recurrence IN ('new', 'recurring', 'uncertain')),
#     recurrence_count INTEGER NOT NULL CHECK (recurrence_count >= 1),
#     distinct_meeting_count INTEGER NOT NULL CHECK (distinct_meeting_count >= 1),
#     first_observed_at TIMESTAMPTZ,
#     last_observed_at TIMESTAMPTZ,
#     earlier_occurrences JSONB NOT NULL DEFAULT '[]'::jsonb,
#     likely_existing_issue TEXT,
#     existing_issue_covers_problem BOOLEAN NOT NULL DEFAULT FALSE,
#     existing_issue_resolved BOOLEAN,
#     regressed_after_resolution BOOLEAN NOT NULL DEFAULT FALSE,
#     unresolved BOOLEAN NOT NULL,
#     sufficient_evidence BOOLEAN NOT NULL,
#     evidence JSONB NOT NULL DEFAULT '[]'::jsonb,
#     rationale TEXT NOT NULL,
#     assessed_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
# );
#
# Until this table exists, saving is skipped with a warning and the run
# continues (status["assessments_saved"] is 0).
# ---------------------------------------------------------------------------

import asyncio
import json
import logging
from datetime import datetime

from starlette.datastructures import State

from backend.ticket_suggestion.schemas import ProblemAssessment

logger = logging.getLogger(__name__)


async def save_assessments(assessments: list[ProblemAssessment], config: State) -> int:
    """Insert or refresh assessments. Returns how many were saved (0 on failure)."""
    if not assessments:
        return 0

    try:
        async with asyncio.timeout(10):
            async with config.postgres.acquire() as connection:
                async with connection.transaction():
                    for assessment in assessments:
                        await _upsert_assessment(connection, assessment)
    except Exception as exc:
        logger.warning("Saving problem assessments failed (%s); continuing without them",
                       type(exc).__name__)
        return 0

    return len(assessments)


async def _upsert_assessment(connection, assessment: ProblemAssessment) -> None:
    occurrences = json.dumps(
        [occurrence.model_dump(mode="json") for occurrence in assessment.earlier_occurrences]
    )
    evidence = json.dumps([ref.model_dump(mode="json") for ref in assessment.evidence])

    await connection.execute(
        """
        INSERT INTO problem_assessments (
            item_id, is_problem, recurrence, recurrence_count, distinct_meeting_count,
            first_observed_at, last_observed_at, earlier_occurrences,
            likely_existing_issue, existing_issue_covers_problem, existing_issue_resolved,
            regressed_after_resolution, unresolved, sufficient_evidence, evidence,
            rationale, assessed_at
        )
        VALUES ($1, $2, $3, $4, $5, $6, $7, $8::jsonb, $9, $10, $11, $12, $13, $14,
                $15::jsonb, $16, NOW())
        ON CONFLICT (item_id) DO UPDATE SET
            is_problem = EXCLUDED.is_problem,
            recurrence = EXCLUDED.recurrence,
            recurrence_count = EXCLUDED.recurrence_count,
            distinct_meeting_count = EXCLUDED.distinct_meeting_count,
            first_observed_at = EXCLUDED.first_observed_at,
            last_observed_at = EXCLUDED.last_observed_at,
            earlier_occurrences = EXCLUDED.earlier_occurrences,
            likely_existing_issue = EXCLUDED.likely_existing_issue,
            existing_issue_covers_problem = EXCLUDED.existing_issue_covers_problem,
            existing_issue_resolved = EXCLUDED.existing_issue_resolved,
            regressed_after_resolution = EXCLUDED.regressed_after_resolution,
            unresolved = EXCLUDED.unresolved,
            sufficient_evidence = EXCLUDED.sufficient_evidence,
            evidence = EXCLUDED.evidence,
            rationale = EXCLUDED.rationale,
            assessed_at = NOW()
        """,
        assessment.item_id,
        assessment.is_problem,
        assessment.recurrence.value,
        assessment.recurrence_count,
        assessment.distinct_meeting_count,
        _parse_timestamp(assessment.first_observed_at),
        _parse_timestamp(assessment.last_observed_at),
        occurrences,
        assessment.likely_existing_issue,
        assessment.existing_issue_covers_problem,
        assessment.existing_issue_resolved,
        assessment.regressed_after_resolution,
        assessment.unresolved,
        assessment.sufficient_evidence,
        evidence,
        assessment.rationale,
    )


def _parse_timestamp(value: str | None) -> datetime | None:
    if not value:
        return None
    try:
        return datetime.fromisoformat(value)
    except ValueError:
        return None
