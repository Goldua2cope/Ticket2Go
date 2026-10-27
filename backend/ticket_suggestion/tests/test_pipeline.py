"""Tests for extraction, retrieval, assessment, proposal and orchestration.
No Azure, OpenAI, Jira, or database calls.

Run from the repository root:
    python -m pytest backend/ticket_suggestion/tests
"""

import unittest
from contextlib import asynccontextmanager
from datetime import datetime, timedelta, timezone
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch
from uuid import UUID

from backend.ticket_suggestion.context_retrieval import (
    ContextRetriever,
    extract_issue_keys,
    extract_keywords,
    reciprocal_rank_fusion,
)
from backend.ticket_suggestion.evidence import locate_quote, resolve_quotes
from backend.ticket_suggestion.item_extraction import (
    ExtractionReport,
    MeetingItemExtractor,
    make_item_id,
    pack_windows,
)
from backend.ticket_suggestion.problem_assessment import ProblemAssessor
from backend.ticket_suggestion.proposal import ProposalGenerator, make_proposal_id
from backend.ticket_suggestion.schemas import (
    Abstention,
    AssessmentOutput,
    EvidenceRef,
    EvidenceSource,
    ExtractedItemOutput,
    ExtractionOutput,
    HistoricalContext,
    HistoricalItem,
    ItemType,
    JiraCandidate,
    MeetingItem,
    OccurrenceOutput,
    ProblemAssessment,
    ProposalOutcome,
    ProposalOutput,
    ProposalType,
    QuoteOutput,
    Recurrence,
    Severity,
    SourceDocument,
    TicketProposal,
    Tracking,
)
from backend.ticket_suggestion.suggestion_service import (
    SuggestionService,
    build_current_documents,
    build_work_units,
)

MEETING_ID = "fb74dfd8-5f3a-4a82-9f5b-bb0c1c710817"
MEETING_UUID = UUID(MEETING_ID)
OLD_MEETING_A = "0d6c4f6e-2b7a-4a4e-8d1d-6a8f0c9e1b23"
OLD_MEETING_B = "7a1e2c3d-4b5f-4a6e-9c8d-1f2e3d4c5b6a"
OLD_MEETING_C = "9b8a7c6d-5e4f-4a3b-8c2d-1e0f9a8b7c6d"


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def make_client(*outputs):
    """Fake OpenAI client whose responses.parse returns the given outputs in order."""
    responses = []
    for output in outputs:
        responses.append(SimpleNamespace(output_parsed=output))
    return SimpleNamespace(responses=SimpleNamespace(parse=AsyncMock(side_effect=responses)))


def chunk(index: int, text: str) -> SourceDocument:
    return SourceDocument(
        document_id=f"meeting:{MEETING_ID}:chunk:{index:05d}",
        source=EvidenceSource.TRANSCRIPT,
        text=text,
        meeting_id=MEETING_ID,
        chunk_index=index,
    )


def extracted(ref, item_type, content, document_id, quote, related=None, severity=None,
              owner=None, deadline=None, component=None):
    return ExtractedItemOutput(
        ref=ref, item_type=item_type, content=content, owner=owner, deadline=deadline,
        component=component, severity=severity, related_problem_ref=related,
        evidence=[QuoteOutput(document_id=document_id, quote=quote)],
    )


def meeting_item(item_type: ItemType, content: str, document: SourceDocument, quote: str) -> MeetingItem:
    start = document.text.index(quote)
    return MeetingItem(
        item_id=make_item_id(MEETING_UUID, item_type, content),
        meeting_id=MEETING_UUID,
        item_type=item_type,
        content=content,
        evidence_refs=[
            EvidenceRef(
                document_id=document.document_id, source=document.source, quote=quote,
                meeting_id=document.meeting_id, start_char=start, end_char=start + len(quote),
            )
        ],
    )


def old_chunk(meeting_id: str, date: str, text: str) -> SourceDocument:
    return SourceDocument(
        document_id=f"meeting:{meeting_id}:chunk:00001",
        source=EvidenceSource.HISTORICAL_TRANSCRIPT,
        text=text,
        meeting_id=meeting_id,
        meeting_date=date,
    )


def jira(key: str, status: str = "In Progress", doc_id: str = "jira:10001") -> JiraCandidate:
    return JiraCandidate(
        document_id=doc_id, issue_key=key, summary="Payment timeouts", status=status,
        text=f"Issue key: {key}\nSummary: Payment timeouts under load",
    )


def history_context(*candidates: JiraCandidate) -> HistoricalContext:
    return HistoricalContext(
        documents=[
            old_chunk(OLD_MEETING_A, "2026-08-01T10:00:00+00:00", "The payment service timed out during the sale."),
            old_chunk(OLD_MEETING_B, "2026-08-08T10:00:00+00:00", "Payment timeouts are still happening since the sale."),
            old_chunk(OLD_MEETING_C, "2026-10-01T10:00:00+00:00", "After the October release payments time out again."),
        ],
        jira_candidates=list(candidates),
    )


def assessment_output(**overrides) -> AssessmentOutput:
    values = dict(
        is_problem=True, recurrence=Recurrence.NEW, earlier_occurrences=[],
        current_is_new_occurrence=True, unresolved=True, likely_existing_issue=None,
        existing_issue_covers_problem=False, sufficient_evidence=True, evidence=[],
        rationale="Because.",
    )
    values.update(overrides)
    return AssessmentOutput(**values)


def proposal_output(**overrides) -> ProposalOutput:
    values = dict(
        outcome=ProposalOutcome.CREATE, existing_issue_key=None, summary="Fix payment timeouts",
        description="Context: ...", new_information=None, rationale="Recurring.", evidence=[],
    )
    values.update(overrides)
    return ProposalOutput(**values)


# ---------------------------------------------------------------------------
# Evidence
# ---------------------------------------------------------------------------

class EvidenceTests(unittest.TestCase):
    def test_exact_and_whitespace_tolerant_matches(self):
        text = "We saw Payment   timeouts\nagain yesterday."
        self.assertIsNotNone(locate_quote(text, "Payment   timeouts"))
        span = locate_quote(text, "payment timeouts again")
        self.assertEqual(text[span[0]:span[1]], "Payment   timeouts\nagain")

    def test_typographic_quotes_and_dashes_still_match(self):
        text = "I\u2019ll fix the log\u2011in page \u2014 today."
        span = locate_quote(text, "I'll fix the log-in page - today")
        self.assertIsNotNone(span)
        self.assertEqual(text[span[0]:span[1]], "I\u2019ll fix the log\u2011in page \u2014 today")

    def test_invented_or_tiny_quotes_are_rejected(self):
        text = "We saw payment timeouts again yesterday."
        self.assertIsNone(locate_quote(text, "the database is down"))
        self.assertIsNone(locate_quote(text, "We"))

    def test_quote_citing_unknown_document_is_dropped(self):
        document = chunk(0, "We saw payment timeouts again yesterday.")
        refs = resolve_quotes(
            [QuoteOutput(document_id="made-up", quote="payment timeouts again"),
             QuoteOutput(document_id=document.document_id, quote="payment timeouts again")],
            {document.document_id: document},
        )
        self.assertEqual([ref.document_id for ref in refs], [document.document_id])


# ---------------------------------------------------------------------------
# Step 1: extraction
# ---------------------------------------------------------------------------

class ExtractionTests(unittest.IsolatedAsyncioTestCase):
    async def test_links_across_chunks_deduplicates_and_keeps_attributes(self):
        chunk_0 = chunk(0, "The payment service keeps timing out at peak hours.")
        chunk_1 = chunk(1, "Payment service keeps timing out. Anna will add retries to the client by Friday.")

        first = ExtractionOutput(items=[
            extracted("new-1", ItemType.PROBLEM, "Payment service times out at peak hours",
                      chunk_0.document_id, "payment service keeps timing out",
                      severity=Severity.HIGH, component="payment service"),
        ])
        second = ExtractionOutput(items=[
            extracted("new-1", ItemType.PROBLEM, "payment service times out at  PEAK hours",
                      chunk_1.document_id, "Payment service keeps timing out"),
            extracted("new-2", ItemType.ACTION, "Add retries to the payment client",
                      chunk_1.document_id, "Anna will add retries to the client by Friday",
                      related="item-1", severity=Severity.LOW, owner="Anna", deadline="by Friday"),
        ])
        client = make_client(first, second)

        # Tiny windows force one call per chunk, to test linking across calls.
        report = await MeetingItemExtractor(client, "test", window_chars=10).extract(
            meeting_id=MEETING_ID, transcript_chunks=[chunk_0, chunk_1],
        )
        items = report.items

        self.assertEqual(len(items), 2)
        problem, action = items
        self.assertEqual(len(problem.evidence_refs), 2)
        self.assertEqual(problem.severity, Severity.HIGH)
        self.assertEqual(problem.component, "payment service")
        self.assertEqual(action.related_problem_item_id, problem.item_id)
        self.assertEqual((action.owner, action.deadline), ("Anna", "by Friday"))
        self.assertIsNone(action.severity)  # severity only applies to problems

        second_prompt = client.responses.parse.await_args_list[1].kwargs["input"][1]["content"]
        self.assertIn("item-1 [problem]", second_prompt)

    async def test_item_without_verifiable_transcript_quote_is_skipped(self):
        chunk_0 = chunk(0, "The payment service keeps timing out at peak hours.")
        output = ExtractionOutput(items=[
            extracted("new-1", ItemType.PROBLEM, "Payment timeouts", chunk_0.document_id,
                      "payment service keeps timing out"),
            extracted("new-2", ItemType.ACTION, "Rewrite the database layer", chunk_0.document_id,
                      "we will rewrite the database layer"),
        ])
        items = (await MeetingItemExtractor(make_client(output), "test").extract(
            meeting_id=MEETING_ID, transcript_chunks=[chunk_0])).items
        self.assertEqual([item.item_type for item in items], [ItemType.PROBLEM])

    async def test_placeholder_owner_is_treated_as_missing(self):
        chunk_0 = chunk(0, "Someone should look at the flaky login test.")
        output = ExtractionOutput(items=[
            extracted("new-1", ItemType.ACTION, "Investigate flaky login test", chunk_0.document_id,
                      "look at the flaky login test", owner="Not specified"),
        ])
        items = (await MeetingItemExtractor(make_client(output), "test").extract(
            meeting_id=MEETING_ID, transcript_chunks=[chunk_0])).items
        self.assertIsNone(items[0].owner)

    def test_chunks_are_packed_into_windows(self):
        chunks = [chunk(0, "a" * 40), chunk(1, "b" * 40), chunk(2, "c" * 40)]
        windows = pack_windows(chunks, window_chars=100)
        self.assertEqual([len(window) for window in windows], [2, 1])

    async def test_short_meeting_is_extracted_in_one_call(self):
        chunk_0 = chunk(0, "The payment service keeps timing out at peak hours.")
        chunk_1 = chunk(1, "Anna will add retries to the client by Friday.")
        client = make_client(ExtractionOutput(items=[]))
        report = await MeetingItemExtractor(client, "test").extract(
            meeting_id=MEETING_ID, transcript_chunks=[chunk_0, chunk_1])
        self.assertEqual((report.windows, report.windows_failed), (1, 0))
        client.responses.parse.assert_awaited_once()
        prompt = client.responses.parse.await_args.kwargs["input"][1]["content"]
        self.assertIn(chunk_0.document_id, prompt)
        self.assertIn(chunk_1.document_id, prompt)

    async def test_failed_window_is_retried_then_skipped(self):
        chunk_0 = chunk(0, "The payment service keeps timing out at peak hours.")
        chunk_1 = chunk(1, "Anna will add retries to the client by Friday.")
        second_window = ExtractionOutput(items=[
            extracted("new-1", ItemType.ACTION, "Add retries to the client", chunk_1.document_id,
                      "Anna will add retries to the client"),
        ])
        parse = AsyncMock(side_effect=[
            RuntimeError("timeout"), RuntimeError("timeout again"),
            SimpleNamespace(output_parsed=second_window),
        ])
        client = SimpleNamespace(responses=SimpleNamespace(parse=parse))

        report = await MeetingItemExtractor(client, "test", window_chars=10, attempts=2).extract(
            meeting_id=MEETING_ID, transcript_chunks=[chunk_0, chunk_1])

        self.assertEqual(parse.await_count, 3)
        self.assertEqual((report.windows, report.windows_failed), (2, 1))
        self.assertEqual([item.content for item in report.items], ["Add retries to the client"])


# ---------------------------------------------------------------------------
# Step 2: retrieval
# ---------------------------------------------------------------------------

class FakeContainer:
    """Returns canned rows depending on which kind of query is sent."""

    def __init__(self, rows: dict[str, list[dict]]):
        self.rows = rows
        self.queries: list[tuple[str, list]] = []

    def query_items(self, query: str, parameters: list):
        self.queries.append((query, parameters))
        source = "jira" if "jira_issues" in query else "history"
        if query.startswith("SELECT c.issue_key, c.status"):
            kind = "tracking"
        elif "ARRAY_CONTAINS" in query:
            kind = "mentioned"
        elif "VectorDistance" in query:
            kind = "vector"
        else:
            kind = "keyword"
        return _iterate(self.rows.get(f"{source}_{kind}", []))


async def _iterate(rows):
    for row in rows:
        yield row


def history_row(meeting_id: str, index: int, text: str) -> dict:
    return {"id": f"meeting:{meeting_id}:chunk:{index:05d}", "content_type": "transcript_chunk",
            "meeting_id": meeting_id, "meeting_date": "2026-09-01T10:00:00+00:00",
            "chunk_index": index, "text": text}


def jira_row(number: int, key: str) -> dict:
    return {"id": f"jira:{number}", "issue_key": key, "summary": f"Issue {key}", "status": "To Do",
            "project_key": "SCRUM", "assignee_display_name": "Bo", "jira_updated_at": "2026-10-01",
            "text": f"Issue key: {key}"}


class RetrievalTests(unittest.IsolatedAsyncioTestCase):
    def test_keywords_and_issue_keys(self):
        self.assertEqual(extract_keywords("The payment service keeps timing out at peak"),
                         ["payment", "service", "keeps", "timing", "peak"])
        self.assertEqual(extract_issue_keys("See SCRUM-12 and SCRUM-12, also PAY-3."), ["SCRUM-12", "PAY-3"])

    def test_rank_fusion_rewards_agreement(self):
        # "b" is second in one list and first in the other, so it beats "a" (first in one only).
        fused = reciprocal_rank_fusion([["a", "b", "c"], ["b", "d"]])
        self.assertEqual(fused[:2], ["b", "a"])

    async def test_hybrid_retrieval_merges_sources_and_respects_caps(self):
        rows = {
            "history_vector": [
                history_row(OLD_MEETING_A, 1, "payment timeouts"),
                history_row(OLD_MEETING_A, 2, "more payment timeouts"),
                history_row(OLD_MEETING_A, 3, "even more payment timeouts"),
            ],
            # Only found by keyword: must still be included.
            "history_keyword": [history_row(OLD_MEETING_B, 4, "payment gateway timeout")],
            "jira_vector": [jira_row(1, "SCRUM-1"), jira_row(2, "SCRUM-2")],
            "jira_keyword": [jira_row(2, "SCRUM-2")],
            "jira_mentioned": [jira_row(9, "SCRUM-9")],
        }
        container = FakeContainer(rows)
        state = SimpleNamespace(cosmos_container=container)
        retriever = ContextRetriever(state, max_chunks_per_meeting=2, lookback_days=30)

        now = datetime.now(timezone.utc)
        real_dates = {
            OLD_MEETING_A: (now - timedelta(days=3)).isoformat(),
            OLD_MEETING_B: (now - timedelta(days=5)).isoformat(),
            OLD_MEETING_C: (now - timedelta(days=90)).isoformat(),  # outside the 30-day window
        }
        rows["history_keyword"].append(history_row(OLD_MEETING_C, 7, "payment timeout long ago"))

        module = "backend.ticket_suggestion.context_retrieval"
        with patch(f"{module}._embed_texts", new=AsyncMock(return_value=[[0.1, 0.2]])), \
             patch(f"{module}.fetch_meeting_dates", new=AsyncMock(return_value=real_dates)), \
             patch(f"{module}.fetch_items_for_meetings", new=AsyncMock(return_value=[])), \
             patch(f"{module}.search_items_by_keywords", new=AsyncMock(return_value=[])) as keyword_items, \
             patch(f"{module}.fetch_related_proposals", new=AsyncMock(return_value=[])) as proposals:
            context = await retriever.retrieve(
                meeting_id=MEETING_ID, query_text="Payment timeouts at checkout",
                project_key="SCRUM", scan_text="as discussed in SCRUM-9",
            )

        meetings = [document.meeting_id for document in context.documents]
        self.assertEqual(meetings.count(OLD_MEETING_A), 2)  # capped per meeting
        self.assertIn(OLD_MEETING_B, meetings)  # keyword-only hit kept
        self.assertNotIn(OLD_MEETING_C, meetings)  # real meeting date is outside the window
        self.assertEqual(context.documents[0].meeting_date, real_dates[OLD_MEETING_A])

        keys = [candidate.issue_key for candidate in context.jira_candidates]
        self.assertEqual(keys[0], "SCRUM-9")  # explicitly mentioned issue first
        self.assertEqual(keys[1], "SCRUM-2")  # found by both vector and keyword search
        self.assertEqual(context.jira_candidates[0].assignee, "Bo")

        history_query, parameters = container.queries[0]
        self.assertIn("c.meeting_date >= @since", history_query)
        self.assertIn("payment", keyword_items.await_args.args[0])
        self.assertEqual(sorted(proposals.await_args.args[1]), ["SCRUM-1", "SCRUM-2", "SCRUM-9"])

    async def test_earlier_items_get_a_tracking_status(self):
        def item(content, review=None, key=None):
            return HistoricalItem(
                item_id=make_item_id(UUID(OLD_MEETING_A), ItemType.ACTION, content),
                meeting_id=UUID(OLD_MEETING_A), item_type=ItemType.ACTION, content=content,
                review_status=review, tracked_issue_key=key,
            )

        earlier = [
            item("Add retries", "approved", "SCRUM-2"),   # SCRUM-2 is an open candidate
            item("Fix login", "approved", "SCRUM-50"),    # not a candidate; Done in Cosmos
            item("Rewrite docs", "rejected"),
            item("Tidy wiki", "pending"),
            item("Rename service"),
        ]
        rows = {
            "history_vector": [history_row(OLD_MEETING_A, 1, "payment timeouts")],
            "jira_vector": [jira_row(2, "SCRUM-2")],
            "jira_tracking": [{"issue_key": "SCRUM-50", "status": "Done"}],
        }
        retriever = ContextRetriever(SimpleNamespace(cosmos_container=FakeContainer(rows)))

        module = "backend.ticket_suggestion.context_retrieval"
        with patch(f"{module}._embed_texts", new=AsyncMock(return_value=[[0.1]])), \
             patch(f"{module}.fetch_meeting_dates", new=AsyncMock(return_value={})), \
             patch(f"{module}.fetch_items_for_meetings", new=AsyncMock(return_value=earlier)), \
             patch(f"{module}.search_items_by_keywords", new=AsyncMock(return_value=[])), \
             patch(f"{module}.fetch_related_proposals", new=AsyncMock(return_value=[])):
            context = await retriever.retrieve(meeting_id=MEETING_ID, query_text="retries",
                                               project_key="SCRUM")

        statuses = {}
        for historical in context.historical_items:
            statuses[historical.content] = historical.tracking
        self.assertEqual(statuses, {
            "Add retries": Tracking.OPEN,
            "Fix login": Tracking.DONE,
            "Rewrite docs": Tracking.REJECTED,
            "Tidy wiki": Tracking.SUGGESTED,
            "Rename service": Tracking.UNTRACKED,
        })


# ---------------------------------------------------------------------------
# Step 3: assessment
# ---------------------------------------------------------------------------

class AssessmentTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        self.document = chunk(0, "The payment service keeps timing out at peak hours.")
        self.problem = meeting_item(ItemType.PROBLEM, "Payment timeouts", self.document,
                                    "payment service keeps timing out")

    async def assess(self, output, context, date="2026-10-20T10:00:00+00:00"):
        return await ProblemAssessor(make_client(output), "test").assess(
            self.problem, [], context, current_meeting_date=date)

    async def test_mentions_are_not_counted_as_incidents(self):
        # Meetings A and B discuss one ongoing incident; C is a fresh one after a release.
        output = assessment_output(
            recurrence=Recurrence.RECURRING,
            earlier_occurrences=[
                OccurrenceOutput(meeting_ids=[OLD_MEETING_A, OLD_MEETING_B], description="Sale outage"),
                OccurrenceOutput(meeting_ids=[OLD_MEETING_C, OLD_MEETING_A], description="October release"),
            ],
            current_is_new_occurrence=False,
        )
        result = await self.assess(output, history_context())

        self.assertEqual(result.recurrence_count, 2)
        self.assertEqual(result.distinct_meeting_count, 4)
        self.assertEqual(result.earlier_occurrences[1].meeting_ids, [OLD_MEETING_C])  # A used once
        self.assertEqual(result.first_observed_at, "2026-08-01T10:00:00+00:00")
        self.assertEqual(result.last_observed_at, "2026-10-20T10:00:00+00:00")

    async def test_unknown_ids_removed_and_unsupported_recurrence_downgraded(self):
        output = assessment_output(
            recurrence=Recurrence.RECURRING,
            earlier_occurrences=[OccurrenceOutput(meeting_ids=["invented"], description="?")],
            likely_existing_issue="SCRUM-999",
        )
        result = await self.assess(output, history_context(jira("SCRUM-12")))
        self.assertEqual(result.recurrence, Recurrence.UNCERTAIN)
        self.assertEqual(result.recurrence_count, 1)
        self.assertIsNone(result.likely_existing_issue)

    async def test_problem_returning_after_resolution_is_flagged(self):
        output = assessment_output(
            recurrence=Recurrence.RECURRING,
            earlier_occurrences=[OccurrenceOutput(meeting_ids=[OLD_MEETING_A], description="Sale")],
            likely_existing_issue="SCRUM-12", existing_issue_covers_problem=True,
            evidence=[QuoteOutput(document_id=f"meeting:{OLD_MEETING_A}:chunk:00001",
                                  quote="payment service timed out during the sale")],
        )
        result = await self.assess(output, history_context(jira("SCRUM-12", status="Done")))
        self.assertTrue(result.existing_issue_resolved)
        self.assertTrue(result.regressed_after_resolution)
        self.assertEqual(len(result.evidence), 1)
        self.assertEqual(result.evidence[0].meeting_id, OLD_MEETING_A)


# ---------------------------------------------------------------------------
# Step 4: proposal
# ---------------------------------------------------------------------------

class ProposalTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        self.document = chunk(0, "The payment service keeps timing out at peak hours.")
        self.problem = meeting_item(ItemType.PROBLEM, "Payment timeouts", self.document,
                                    "payment service keeps timing out")

    def assessment(self, **overrides) -> ProblemAssessment:
        values = dict(item_id=self.problem.item_id, recurrence=Recurrence.RECURRING, rationale="Seen.")
        values.update(overrides)
        return ProblemAssessment(**values)

    async def propose(self, output, context, assessment=None, item=None, parent_problem=None, client=None):
        item = item or self.problem
        return await ProposalGenerator(client or make_client(output), "test").propose(
            item=item, related_items=[], meeting_items=[item],
            current_documents={self.document.document_id: self.document},
            context=context, assessment=assessment, parent_problem=parent_problem,
        )

    async def test_not_a_genuine_problem_without_action_is_blocked(self):
        result = await self.propose(proposal_output(), history_context(),
                                    self.assessment(is_problem=False))
        self.assertIsInstance(result, Abstention)
        self.assertIn("not a genuine problem", result.reason)

    async def test_standalone_action_duplicating_open_issue_is_blocked(self):
        document = chunk(0, "Anna will add retries to the payment client.")
        action = meeting_item(ItemType.ACTION, "Add retries to the payment client", document,
                              "Anna will add retries to the payment client")
        output = proposal_output(summary="Add retries to the payment client")

        open_issue = JiraCandidate(document_id="jira:5", issue_key="SCRUM-5", status="To Do",
                                   summary="Add retries to payment client", text="Issue key: SCRUM-5")
        done_issue = open_issue.model_copy(update={"status": "Done"})

        blocked = await self.propose(output, HistoricalContext(jira_candidates=[open_issue]), item=action)
        allowed = await self.propose(output, HistoricalContext(jira_candidates=[done_issue]), item=action)
        self.assertIsInstance(blocked, Abstention)
        self.assertIn("SCRUM-5", blocked.reason)
        self.assertIsInstance(allowed, TicketProposal)

    async def test_action_prompt_shows_its_problem_and_earlier_similar_actions(self):
        document = chunk(0, "Payments time out. Anna will add retries to the payment client.")
        action = meeting_item(ItemType.ACTION, "Add retries to the payment client", document,
                              "Anna will add retries to the payment client")
        earlier = HistoricalItem(
            item_id=make_item_id(UUID(OLD_MEETING_A), ItemType.ACTION, "Add payment client retries"),
            meeting_id=UUID(OLD_MEETING_A), item_type=ItemType.ACTION,
            content="Add payment client retries", tracking=Tracking.SUGGESTED,
        )
        client = make_client(proposal_output(outcome=ProposalOutcome.NONE, summary=None))
        await self.propose(None, HistoricalContext(historical_items=[earlier]), item=action,
                           parent_problem=self.problem, client=client)

        prompt = client.responses.parse.await_args.kwargs["input"][1]["content"]
        self.assertIn("<problem_this_action_addresses>", prompt)
        self.assertIn("Payment timeouts", prompt)
        self.assertIn("status: suggested", prompt.split("<earlier_similar_actions>")[1])

    async def test_none_is_an_abstention(self):
        result = await self.propose(proposal_output(outcome=ProposalOutcome.NONE, summary=None),
                                    history_context())
        self.assertIsInstance(result, Abstention)

    async def test_update_needs_retrieved_target_and_new_information(self):
        unknown = await self.propose(
            proposal_output(outcome=ProposalOutcome.UPDATE, existing_issue_key="SCRUM-999",
                            new_information="New repro"), history_context(jira("SCRUM-12")))
        nothing_new = await self.propose(
            proposal_output(outcome=ProposalOutcome.UPDATE, existing_issue_key="SCRUM-12",
                            new_information=" "), history_context(jira("SCRUM-12")))
        self.assertIsInstance(unknown, Abstention)
        self.assertIsInstance(nothing_new, Abstention)

    async def test_update_is_an_append_patch_with_history(self):
        output = proposal_output(
            outcome=ProposalOutcome.UPDATE, existing_issue_key="SCRUM-12",
            summary="Timeouts seen again at peak", description="Seen again on 20 Oct at peak hours.",
            new_information="New occurrence at peak hours",
            evidence=[
                QuoteOutput(document_id=self.document.document_id, quote="payment service keeps timing out"),
                QuoteOutput(document_id=f"meeting:{OLD_MEETING_C}:chunk:00001",
                            quote="After the October release payments time out again"),
            ],
        )
        proposal = await self.propose(output, history_context(jira("SCRUM-12")))

        self.assertIsInstance(proposal, TicketProposal)
        self.assertEqual(proposal.proposal_type, ProposalType.UPDATE)
        self.assertEqual(proposal.existing_issue_key, "SCRUM-12")
        self.assertEqual(proposal.description, "Seen again on 20 Oct at peak hours.")
        self.assertEqual(proposal.proposal_id, make_proposal_id(self.problem.item_id))
        self.assertEqual(proposal.related_meeting_ids, [UUID(OLD_MEETING_C)])

    async def test_create_duplicating_open_issue_is_blocked(self):
        assessment = self.assessment(likely_existing_issue="SCRUM-12", existing_issue_covers_problem=True)
        result = await self.propose(proposal_output(), history_context(jira("SCRUM-12")), assessment)
        self.assertIsInstance(result, Abstention)
        self.assertIn("SCRUM-12", result.reason)

    async def test_create_allowed_when_tracked_issue_was_resolved(self):
        assessment = self.assessment(likely_existing_issue="SCRUM-12", existing_issue_covers_problem=True,
                                     existing_issue_resolved=True, regressed_after_resolution=True)
        result = await self.propose(proposal_output(), history_context(jira("SCRUM-12", status="Done")),
                                    assessment)
        self.assertIsInstance(result, TicketProposal)
        self.assertEqual(result.proposal_type, ProposalType.CREATE)

    async def test_create_with_insufficient_evidence_is_blocked(self):
        result = await self.propose(proposal_output(), history_context(),
                                    self.assessment(sufficient_evidence=False))
        self.assertIsInstance(result, Abstention)

    async def test_create_without_valid_quotes_falls_back_to_item_evidence(self):
        output = proposal_output(
            existing_issue_key="SCRUM-12", summary="  Investigate   payment timeouts  " + "x" * 300,
            evidence=[QuoteOutput(document_id=self.document.document_id, quote="invented words here")],
        )
        proposal = await self.propose(output, history_context())
        self.assertIsNone(proposal.existing_issue_key)  # only kept for updates
        self.assertTrue(proposal.summary.startswith("Investigate payment timeouts"))
        self.assertLessEqual(len(proposal.summary), 255)
        self.assertEqual(proposal.evidence, self.problem.evidence_refs)


# ---------------------------------------------------------------------------
# Orchestration
# ---------------------------------------------------------------------------

def fake_lock(acquired: bool):
    @asynccontextmanager
    async def lock(meeting_id, config):
        yield acquired
    return lock


class ServiceTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        self.document = chunk(0, "Payments time out. Anna will add retries. Bo will add alerts. "
                                 "We agreed to fix it this sprint. Carol will tidy the wiki.")
        self.problem = meeting_item(ItemType.PROBLEM, "Payments time out", self.document, "Payments time out")
        self.retries = self.linked(meeting_item(ItemType.ACTION, "Add retries", self.document,
                                                "Anna will add retries"))
        self.alerts = self.linked(meeting_item(ItemType.ACTION, "Add alerts", self.document,
                                               "Bo will add alerts"))
        self.decision = self.linked(meeting_item(ItemType.DECISION, "Fix this sprint", self.document,
                                                 "We agreed to fix it this sprint"))
        self.wiki = meeting_item(ItemType.ACTION, "Tidy wiki", self.document, "Carol will tidy the wiki")

    def linked(self, item: MeetingItem) -> MeetingItem:
        return item.model_copy(update={"related_problem_item_id": self.problem.item_id})

    def test_document_ids_match_cosmos_ingestion(self):
        transcription = {"segments": [{"text": " Hello team. "}, {"text": ""}, {"text": "Payments time out."}]}
        chunks, summary = build_current_documents(MEETING_ID, transcription, "Summary text")
        self.assertEqual(chunks[0].document_id, f"meeting:{MEETING_ID}:chunk:00000")
        self.assertEqual(chunks[0].text, "Hello team. Payments time out.")
        self.assertEqual(summary.document_id, f"meeting:{MEETING_ID}:summary")

    def test_each_linked_action_gets_its_own_decision(self):
        units = build_work_units([self.problem, self.retries, self.alerts, self.decision, self.wiki])
        self.assertEqual(len(units), 2)
        self.assertEqual(units[0].focuses(), [self.retries, self.alerts])
        self.assertEqual(units[0].linked, [self.retries, self.alerts, self.decision])
        self.assertEqual(units[1].focuses(), [self.wiki])

        lone_problem = build_work_units([self.problem, self.decision])[0]
        self.assertEqual(lone_problem.focuses(), [self.problem])

    def service(self, extractor, generator, assessor=None):
        assessment = ProblemAssessment(item_id=self.problem.item_id, recurrence=Recurrence.RECURRING,
                                       recurrence_count=2, distinct_meeting_count=3, rationale="Seen.")
        self.retriever = SimpleNamespace(retrieve=AsyncMock(return_value=HistoricalContext()))
        self.assessor = assessor or SimpleNamespace(assess=AsyncMock(return_value=assessment))
        self.state = SimpleNamespace(jira_project_key="SCRUM")
        return SuggestionService(self.state, extractor=extractor, retriever=self.retriever,
                                 assessor=self.assessor, generator=generator)

    async def run_service(self, service, lock=True, registered=None):
        module = "backend.ticket_suggestion.suggestion_service"
        with patch(f"{module}.suggestion_run_lock", new=fake_lock(lock)), \
             patch(f"{module}.upsert_meeting_items", new=AsyncMock()) as self.upsert, \
             patch(f"{module}.save_assessments", new=AsyncMock(return_value=1)), \
             patch(f"{module}.save_pending_proposals",
                   new=AsyncMock(return_value=registered or [])) as self.save, \
             patch(f"{module}.fetch_meeting_dates", new=AsyncMock(return_value={})):
            return await service.run(meeting_id=MEETING_ID,
                                     transcription={"segments": [{"text": self.document.text}]})

    async def test_returns_registered_proposals_with_status(self):
        items = [self.problem, self.retries, self.alerts, self.decision, self.wiki]
        extractor = SimpleNamespace(extract=AsyncMock(
            return_value=ExtractionReport(items=items, windows=1, windows_failed=0)))
        proposal = TicketProposal(
            proposal_id=make_proposal_id(self.retries.item_id), item_id=self.retries.item_id,
            proposal_type=ProposalType.CREATE, summary="Add retries",
            rationale="Anna committed to it.", evidence=self.retries.evidence_refs,
        )
        abstention = Abstention(item_id=self.wiki.item_id, reason="Informational.",
                                model_outcome=ProposalOutcome.NONE)
        decisions = {self.retries.item_id: proposal, self.wiki.item_id: abstention}

        def decide(**kwargs):
            focus = kwargs["item"]
            if focus.item_id not in decisions:
                raise RuntimeError("model down")
            return decisions[focus.item_id]

        generator = SimpleNamespace(propose=AsyncMock(side_effect=decide))
        service = self.service(extractor, generator)
        registered_row = {"proposal_id": str(proposal.proposal_id), "review_status": "pending"}

        result = await self.run_service(service, registered=[registered_row])

        self.assertEqual(result["proposals"], [registered_row])
        self.save.assert_awaited_once_with([proposal], self.state)
        self.assertEqual(result["status"], {
            "outcome": "partial", "items_extracted": 5, "extraction_windows": 1,
            "extraction_windows_failed": 0, "candidates": 3, "proposals_registered": 1,
            "skipped_already_reviewed": 0, "abstentions": 1, "failures": 1,
            "recurring_problems": 1, "assessments_saved": 1,
        })
        # One retrieval and one assessment per problem, shared by its actions.
        self.assertEqual(self.retriever.retrieve.await_count, 2)
        self.assessor.assess.assert_awaited_once()
        for call in generator.propose.await_args_list:
            if call.kwargs["item"].item_id in (self.retries.item_id, self.alerts.item_id):
                self.assertEqual(call.kwargs["parent_problem"], self.problem)
                self.assertIsNotNone(call.kwargs["assessment"])

    async def test_total_failure_is_reported_as_failed(self):
        extractor = SimpleNamespace(extract=AsyncMock(
            return_value=ExtractionReport(items=[self.wiki], windows=1, windows_failed=0)))
        generator = SimpleNamespace(propose=AsyncMock(side_effect=RuntimeError("model down")))
        result = await self.run_service(self.service(extractor, generator))
        self.assertEqual(result["proposals"], [])
        self.assertEqual(result["status"]["outcome"], "failed")
        self.assertEqual(result["status"]["failures"], 1)

    async def test_failed_extraction_saves_nothing(self):
        extractor = SimpleNamespace(extract=AsyncMock(
            return_value=ExtractionReport(items=[], windows=2, windows_failed=2)))
        generator = SimpleNamespace(propose=AsyncMock())
        result = await self.run_service(self.service(extractor, generator))
        self.assertEqual(result["status"]["outcome"], "failed")
        self.upsert.assert_not_awaited()
        generator.propose.assert_not_awaited()

    async def test_concurrent_run_for_same_meeting_is_skipped(self):
        extractor = SimpleNamespace(extract=AsyncMock())
        result = await self.run_service(self.service(extractor, SimpleNamespace()), lock=False)
        self.assertEqual(result, {"proposals": [], "status": {
            "outcome": "already_running", "items_extracted": 0, "extraction_windows": 0,
            "extraction_windows_failed": 0, "candidates": 0, "proposals_registered": 0,
            "skipped_already_reviewed": 0, "abstentions": 0, "failures": 0,
            "recurring_problems": 0, "assessments_saved": 0,
        }})
        extractor.extract.assert_not_awaited()


# ---------------------------------------------------------------------------
# Registering proposals in ticket_proposals (fake asyncpg pool)
# ---------------------------------------------------------------------------

class _NullContext:
    def __init__(self, value=None):
        self.value = value

    async def __aenter__(self):
        return self.value

    async def __aexit__(self, *exc_info):
        return False


class FakeConnection:
    """Returns the given rows from fetchrow, in order. None means the upsert was skipped."""

    def __init__(self, rows):
        self.rows = list(rows)
        self.queries = []

    async def fetchrow(self, query, *args):
        self.queries.append(" ".join(query.split()))
        return self.rows.pop(0)

    def transaction(self):
        return _NullContext()


class FakePool:
    def __init__(self, connection):
        self.connection = connection

    def acquire(self):
        return _NullContext(self.connection)


class ProposalStorageTests(unittest.IsolatedAsyncioTestCase):
    def make_proposal(self, content: str) -> TicketProposal:
        document = chunk(0, f"{content} was discussed today.")
        item = meeting_item(ItemType.ACTION, content, document, f"{content} was discussed")
        return TicketProposal(
            proposal_id=make_proposal_id(item.item_id), item_id=item.item_id,
            proposal_type=ProposalType.CREATE, summary=content, rationale="Committed.",
            evidence=item.evidence_refs, related_meeting_ids=[UUID(OLD_MEETING_A)],
        )

    async def test_returns_registered_rows_and_skips_reviewed_ones(self):
        from backend.postgres.ticket_proposals import save_pending_proposals

        fresh = self.make_proposal("Update the docs")
        reviewed = self.make_proposal("Tidy the wiki")
        stored_row = {
            "proposal_id": fresh.proposal_id, "item_id": fresh.item_id, "proposal_type": "create",
            "summary": "Update the docs", "description": None, "assignee_account_id": None,
            "review_status": "pending", "execution_status": "not_started",
            "existing_issue_key": None, "jira_issue_key": None, "rationale": "Committed.",
            "evidence": '[{"quote": "Update the docs was discussed"}]',
            "related_meeting_ids": [UUID(OLD_MEETING_A)],
        }
        # The second upsert hits a proposal the user already reviewed: no row comes back.
        connection = FakeConnection([stored_row, None])

        rows = await save_pending_proposals([fresh, reviewed], SimpleNamespace(postgres=FakePool(connection)))

        self.assertEqual(len(rows), 1)
        row = rows[0]
        self.assertEqual(row["proposal_id"], str(fresh.proposal_id))
        self.assertEqual(row["review_status"], "pending")
        self.assertEqual(row["evidence"], [{"quote": "Update the docs was discussed"}])
        self.assertEqual(row["related_meeting_ids"], [OLD_MEETING_A])

        # The suggestion step never sets review or execution status, and only
        # refreshes proposals that are still pending.
        query = connection.queries[0]
        self.assertIn("WHERE ticket_proposals.review_status = 'pending'", query)
        self.assertNotIn("review_status =", query.split("WHERE")[0])
        self.assertNotIn("execution_status =", query)




class PostgresHelperTests(unittest.IsolatedAsyncioTestCase):
    def test_hyphenated_keywords_become_phrases(self):
        from backend.postgres.meeting_items import _keyword_to_tsquery_term
        self.assertEqual(_keyword_to_tsquery_term("sign-in"), "(sign <-> in)")
        self.assertEqual(_keyword_to_tsquery_term("timeout"), "timeout")
        self.assertIsNone(_keyword_to_tsquery_term("--"))

    async def test_assessment_save_failure_does_not_break_the_run(self):
        from backend.postgres.problem_assessments import save_assessments

        class BrokenPool:
            def acquire(self):
                raise RuntimeError("relation problem_assessments does not exist")

        assessment = ProblemAssessment(item_id=UUID(OLD_MEETING_A), recurrence=Recurrence.NEW,
                                       rationale="New.")
        saved = await save_assessments([assessment], SimpleNamespace(postgres=BrokenPool()))
        self.assertEqual(saved, 0)


if __name__ == "__main__":
    unittest.main()
