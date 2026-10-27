"""
Run the ticket suggestion workflow for one completed meeting:

1. Understand the meeting: extract problems, actions, decisions and
   questions, and save them to meeting_items.
2. Reason across history, per work unit:
   - a problem with its linked items: retrieve history and assess recurrence
     once, then decide create / update / none for each linked action (or for
     the problem itself when no action is linked);
   - an action not tied to a problem: retrieve history and decide.
3. Register create/update suggestions in ticket_proposals, save problem
   assessments, and return the registered proposals with a run status.
   'none' outcomes are logged, not registered.

Output:
    {
        "proposals": [ {ticket_proposals row}, ... ],
        "status": {"outcome": "completed" | "partial" | "failed" | "no_items"
                              | "already_running",
                   "candidates": ..., "failures": ..., "abstentions": ..., ...},
    }

This module only creates suggestions. It never approves them and never
writes to Jira; that authority lies with the user.
"""

import asyncio
import logging
from dataclasses import dataclass, field
from typing import Any
from uuid import UUID

from starlette.datastructures import State

from backend.meeting_ingestion import chunk_text
from backend.postgres.meeting_items import fetch_meeting_dates, upsert_meeting_items
from backend.postgres.problem_assessments import save_assessments
from backend.postgres.suggestion_runs import suggestion_run_lock
from backend.postgres.ticket_proposals import save_pending_proposals

from .context_retrieval import ContextRetriever
from .item_extraction import MeetingItemExtractor
from .problem_assessment import ProblemAssessor
from .proposal import ProposalGenerator
from .schemas import (
    Abstention,
    EvidenceSource,
    ItemType,
    MeetingItem,
    ProblemAssessment,
    RunOutcome,
    RunStatus,
    SourceDocument,
    TicketProposal,
)

logger = logging.getLogger(__name__)


def transcript_to_text(transcription: dict) -> str:
    """Join segment texts exactly as meeting_ingestion does before chunking."""
    if not isinstance(transcription, dict):
        raise TypeError("transcription must be a dictionary.")
    segments = transcription.get("segments")
    if not isinstance(segments, list):
        raise ValueError("transcription must contain a 'segments' list.")

    texts = []
    for segment in segments:
        if isinstance(segment, dict) and isinstance(segment.get("text"), str):
            text = segment["text"].strip()
            if text:
                texts.append(text)
    return " ".join(texts).strip()


def transcript_chunk_id(meeting_id: str, index: int) -> str:
    """Cosmos DB id of a transcript chunk (same format as meeting_ingestion)."""
    return f"meeting:{meeting_id}:chunk:{index:05d}"


def summary_document_id(meeting_id: str) -> str:
    """Cosmos DB id of a meeting summary (same format as meeting_ingestion)."""
    return f"meeting:{meeting_id}:summary"


@dataclass
class WorkUnit:
    """Items handled together: one retrieval and at most one assessment.

    problem set:   the problem, every item linked to it, and the actions to
                   propose for (empty -> propose for the problem itself).
    problem None:  a single standalone action.
    """

    problem: MeetingItem | None
    linked: list[MeetingItem] = field(default_factory=list)
    actions: list[MeetingItem] = field(default_factory=list)

    def focuses(self) -> list[MeetingItem]:
        """Items that each get their own create/update/none decision."""
        if self.problem is not None and not self.actions:
            return [self.problem]
        return list(self.actions)

    def all_items(self) -> list[MeetingItem]:
        items = []
        if self.problem is not None:
            items.append(self.problem)
        items.extend(self.linked)
        for action in self.actions:
            if action not in items:
                items.append(action)
        return items


@dataclass
class UnitOutcome:
    assessment: ProblemAssessment | None = None
    proposals: list[TicketProposal] = field(default_factory=list)
    abstentions: list[Abstention] = field(default_factory=list)
    failures: int = 0


class SuggestionService:
    def __init__(
        self,
        state: State,
        *,
        extractor: MeetingItemExtractor | None = None,
        retriever: ContextRetriever | None = None,
        assessor: ProblemAssessor | None = None,
        generator: ProposalGenerator | None = None,
        project_key: str | None = None,
        max_concurrency: int = 3,
    ) -> None:
        """Extraction uses state.model_extraction when set (e.g. a cheaper
        model), otherwise state.model_response, like the other steps."""
        self._state = state
        if extractor is None:
            extraction_model = getattr(state, "model_extraction", None) or state.model_response
            extractor = MeetingItemExtractor(state.ai_client, extraction_model)
        self._extractor = extractor
        self._retriever = retriever or ContextRetriever(state)
        self._assessor = assessor or ProblemAssessor(state.ai_client, state.model_response)
        self._generator = generator or ProposalGenerator(state.ai_client, state.model_response)
        self._project_key = project_key or state.jira_project_key
        self._semaphore = asyncio.Semaphore(max_concurrency)

    async def run(
        self,
        *,
        meeting_id: str,
        transcription: dict,
        summary: str | None = None,
        meeting_date: str | None = None,
    ) -> dict[str, Any]:
        """Return {"proposals": [registered rows as dicts], "status": {...}}.

        A proposal the user already approved or rejected in an earlier run is
        not changed and not returned. Only one run per meeting at a time.
        """
        async with suggestion_run_lock(UUID(meeting_id), self._state) as acquired:
            if not acquired:
                logger.info("Suggestion run for meeting %s skipped: already running", meeting_id)
                return _output([], RunStatus(outcome=RunOutcome.ALREADY_RUNNING))
            return await self._run_locked(meeting_id, transcription, summary, meeting_date)

    async def _run_locked(
        self,
        meeting_id: str,
        transcription: dict,
        summary: str | None,
        meeting_date: str | None,
    ) -> dict[str, Any]:
        chunks, summary_document = build_current_documents(meeting_id, transcription, summary)
        if not chunks:
            raise ValueError("The transcript contains no text.")

        if meeting_date is None:
            dates = await fetch_meeting_dates([UUID(meeting_id)], self._state)
            meeting_date = dates.get(meeting_id)

        report = await self._extractor.extract(
            meeting_id=meeting_id,
            transcript_chunks=chunks,
            summary=summary_document,
            meeting_date=meeting_date,
        )
        status = RunStatus(
            outcome=RunOutcome.COMPLETED,
            items_extracted=len(report.items),
            extraction_windows=report.windows,
            extraction_windows_failed=report.windows_failed,
        )

        if report.windows and report.windows_failed == report.windows:
            status.outcome = RunOutcome.FAILED
            return _finish(meeting_id, [], status)
        if not report.items:
            status.outcome = RunOutcome.PARTIAL if report.windows_failed else RunOutcome.NO_ITEMS
            return _finish(meeting_id, [], status)

        items = report.items
        await upsert_meeting_items(items, self._state)

        current_documents: dict[str, SourceDocument] = {}
        for chunk in chunks:
            current_documents[chunk.document_id] = chunk
        if summary_document is not None:
            current_documents[summary_document.document_id] = summary_document

        units = build_work_units(items)
        tasks = []
        for unit in units:
            status.candidates += len(unit.focuses())
            tasks.append(self._process_unit(meeting_id, meeting_date, unit, items, current_documents))
        outcomes = await asyncio.gather(*tasks)

        assessments: list[ProblemAssessment] = []
        proposals: list[TicketProposal] = []
        for outcome in outcomes:
            status.failures += outcome.failures
            status.abstentions += len(outcome.abstentions)
            proposals.extend(outcome.proposals)
            if outcome.assessment is not None:
                assessments.append(outcome.assessment)
                _log_assessment(outcome.assessment)
                if outcome.assessment.recurrence_count > 1 or outcome.assessment.distinct_meeting_count > 1:
                    status.recurring_problems += 1

        status.assessments_saved = await save_assessments(assessments, self._state)
        registered = await save_pending_proposals(proposals, self._state)
        status.proposals_registered = len(registered)
        status.skipped_already_reviewed = len(proposals) - len(registered)

        if status.candidates and status.failures == status.candidates:
            status.outcome = RunOutcome.FAILED
        elif status.failures or report.windows_failed:
            status.outcome = RunOutcome.PARTIAL
        return _finish(meeting_id, registered, status)

    async def _process_unit(
        self,
        meeting_id: str,
        meeting_date: str | None,
        unit: WorkUnit,
        items: list[MeetingItem],
        current_documents: dict[str, SourceDocument],
    ) -> UnitOutcome:
        """Retrieve and assess once, then decide for each focus item.
        Failures are counted, never raised, so one bad unit doesn't stop the run."""
        outcome = UnitOutcome()
        focuses = unit.focuses()

        async with self._semaphore:
            try:
                context = await self._retriever.retrieve(
                    meeting_id=meeting_id,
                    query_text=_query_text(unit),
                    project_key=self._project_key,
                    item_ids=[item.item_id for item in unit.all_items()],
                    scan_text=_scan_text(unit),
                )
                if unit.problem is not None:
                    outcome.assessment = await self._assessor.assess(
                        unit.problem, unit.linked, context, current_meeting_date=meeting_date,
                    )
            except Exception:
                logger.exception("Retrieval or assessment failed for %s", _unit_label(unit))
                outcome.failures = len(focuses)
                return outcome

            for focus in focuses:
                try:
                    decision = await self._generator.propose(
                        item=focus,
                        related_items=_related_for(focus, unit),
                        meeting_items=items,
                        current_documents=current_documents,
                        context=context,
                        assessment=outcome.assessment,
                        parent_problem=unit.problem if focus is not unit.problem else None,
                    )
                except Exception:
                    logger.exception("Proposal failed for item %s", focus.item_id)
                    outcome.failures += 1
                    continue
                if isinstance(decision, TicketProposal):
                    outcome.proposals.append(decision)
                else:
                    outcome.abstentions.append(decision)

        return outcome


def build_current_documents(
    meeting_id: str,
    transcription: dict,
    summary: str | None,
) -> tuple[list[SourceDocument], SourceDocument | None]:
    """Chunk the transcript exactly as ingestion does, so document ids match Cosmos DB."""
    chunks: list[SourceDocument] = []
    for index, text in enumerate(chunk_text(transcript_to_text(transcription))):
        chunks.append(
            SourceDocument(
                document_id=transcript_chunk_id(meeting_id, index),
                source=EvidenceSource.TRANSCRIPT,
                text=text,
                meeting_id=meeting_id,
                chunk_index=index,
            )
        )

    summary_document = None
    if summary and summary.strip():
        summary_document = SourceDocument(
            document_id=summary_document_id(meeting_id),
            source=EvidenceSource.SUMMARY,
            text=summary.strip(),
            meeting_id=meeting_id,
        )

    return chunks, summary_document


def build_work_units(items: list[MeetingItem]) -> list[WorkUnit]:
    """One unit per problem (with everything linked to it), plus one unit per
    action not tied to a problem. Decisions and questions never get their own
    suggestion; they are context for the problem they're linked to.

    Extraction alone never implies a ticket: every focus still goes through
    the create/update/none decision.
    """
    units: list[WorkUnit] = []
    for item in items:
        if item.item_type == ItemType.PROBLEM:
            linked = []
            actions = []
            for other in items:
                if other.related_problem_item_id == item.item_id:
                    linked.append(other)
                    if other.item_type == ItemType.ACTION:
                        actions.append(other)
            units.append(WorkUnit(problem=item, linked=linked, actions=actions))
        elif item.item_type == ItemType.ACTION and item.related_problem_item_id is None:
            units.append(WorkUnit(problem=None, actions=[item]))
    return units


def _related_for(focus: MeetingItem, unit: WorkUnit) -> list[MeetingItem]:
    """Items shown next to the focus: everything else in its unit, minus the problem
    (the problem is passed separately as parent_problem)."""
    related = []
    for item in unit.linked:
        if item is not focus:
            related.append(item)
    return related


def _query_text(unit: WorkUnit) -> str:
    parts = []
    for item in unit.all_items():
        parts.append(item.content)
    return "\n".join(parts)


def _scan_text(unit: WorkUnit) -> str:
    quotes = []
    for item in unit.all_items():
        for ref in item.evidence_refs:
            quotes.append(ref.quote)
    return "\n".join(quotes)


def _unit_label(unit: WorkUnit) -> str:
    if unit.problem is not None:
        return f"problem {unit.problem.item_id}"
    return f"action {unit.actions[0].item_id}"


def _log_assessment(assessment: ProblemAssessment) -> None:
    """Record the recurrence assessment for evaluation and observability."""
    logger.info(
        "Problem assessed: item=%s is_problem=%s recurrence=%s occurrences=%d meetings=%d "
        "first=%s last=%s issue=%s covers=%s regressed=%s unresolved=%s sufficient=%s",
        assessment.item_id, assessment.is_problem, assessment.recurrence.value,
        assessment.recurrence_count, assessment.distinct_meeting_count,
        assessment.first_observed_at, assessment.last_observed_at,
        assessment.likely_existing_issue, assessment.existing_issue_covers_problem,
        assessment.regressed_after_resolution, assessment.unresolved,
        assessment.sufficient_evidence,
    )


def _output(proposals: list[dict[str, Any]], status: RunStatus) -> dict[str, Any]:
    return {"proposals": proposals, "status": status.model_dump(mode="json")}


def _finish(meeting_id: str, proposals: list[dict[str, Any]], status: RunStatus) -> dict[str, Any]:
    logger.info("Suggestion run for meeting %s: %s", meeting_id, status.model_dump(mode="json"))
    return _output(proposals, status)


async def suggest_tickets(
    *,
    meeting_id: str,
    transcription: dict,
    summary: str | None,
    state: State,
    meeting_date: str | None = None,
) -> dict[str, Any]:
    """Convenience entry point using the app's shared clients.

    Returns {"proposals": [registered ticket_proposals rows], "status": {...}}.
    """
    service = SuggestionService(state)
    return await service.run(
        meeting_id=meeting_id,
        transcription=transcription,
        summary=summary,
        meeting_date=meeting_date,
    )
