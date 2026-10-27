"""
Retrieve historical context for one current-meeting item from three sources:

1. Historical meetings: transcript chunks and summaries (Cosmos DB) plus the
   problems, actions and decisions recorded for those meetings (PostgreSQL).
2. Jira: issues indexed in Cosmos DB by jira_sync, with status, assignee,
   project, last update and parent issue.
3. Previous proposals and outcomes: earlier suggestions, user edits,
   rejections and executed Jira changes (PostgreSQL).

Retrieval is hybrid: vector similarity and keyword matching each produce a
ranking, merged with reciprocal rank fusion, plus structured filters
(project, date window). Issue keys mentioned in the meeting are looked up
directly. Several candidates are returned; deciding whether they describe
the same problem is the assessment step's job, not this one's.
"""

import logging
import re
from datetime import datetime, timedelta, timezone
from operator import itemgetter
from typing import Any
from uuid import UUID

from starlette.datastructures import State

from backend.meeting_ingestion import _embed_texts
from backend.postgres.meeting_items import (
    fetch_items_for_meetings,
    fetch_meeting_dates,
    search_items_by_keywords,
)
from backend.postgres.ticket_proposals import fetch_related_proposals

from .evidence import format_documents
from .schemas import (
    RESOLVED_STATUS_NAMES,
    EvidenceSource,
    HistoricalContext,
    HistoricalItem,
    JiraCandidate,
    PriorProposal,
    SourceDocument,
    Tracking,
)

logger = logging.getLogger(__name__)

RRF_K = 60
MAX_JIRA_TEXT_CHARS = 2000
MAX_PRIOR_DESCRIPTION_CHARS = 600
ISSUE_KEY_PATTERN = re.compile(r"\b[A-Z][A-Z0-9_]+-\d+\b")

STOPWORDS = frozenset("""
about above after again against all also always among and another any are around
because been before being below between both but can cannot could did does doing done
down during each either else even every few for from further get gets getting going gonna
had has have having here how however into its itself just keep kind know last least like
made make many maybe might more most much must need needs never next not now off once
only other our ours out over own probably quite rather really said same say says should
since some something still such sure take than that the their them then there these they
thing things think this those though through too under until very want was way well were
what when where whether which while who whom why will with within without would yeah yes
you your
""".split())

HISTORY_FIELDS = (
    "c.id, c.content_type, c.meeting_id, c.meeting_title, c.meeting_date, "
    "c.chunk_index, c.text"
)
JIRA_FIELDS = (
    "c.id, c.issue_key, c.summary, c.description, c.status, c.issue_type, "
    "c.project_key, c.priority, c.assignee_display_name, c.jira_updated_at, "
    "c.parent_issue_key, c.labels, c.components, c.text, c.jira_url"
)


def is_resolved_status(status: str | None) -> bool:
    return (status or "").strip().casefold() in RESOLVED_STATUS_NAMES


def extract_keywords(text: str, limit: int = 8) -> list[str]:
    """Distinctive words from text, in order of first appearance."""
    keywords: dict[str, None] = {}
    for word in re.findall(r"[a-z0-9][a-z0-9_\-]*[a-z0-9]", text.casefold()):
        has_digit = any(character.isdigit() for character in word)
        if word in STOPWORDS or (len(word) < 4 and not has_digit):
            continue
        keywords[word] = None
        if len(keywords) >= limit:
            break
    return list(keywords)


def extract_issue_keys(text: str) -> list[str]:
    """Jira keys such as SCRUM-12 mentioned verbatim in text."""
    keys: dict[str, None] = {}
    for key in ISSUE_KEY_PATTERN.findall(text):
        keys[key] = None
    return list(keys)


def lexical_overlap(first: str, second: str) -> float:
    """Jaccard overlap of distinctive words, 0..1. A cheap, deterministic similarity."""
    first_words = set(extract_keywords(first, limit=50))
    second_words = set(extract_keywords(second, limit=50))
    if not first_words or not second_words:
        return 0.0
    return len(first_words & second_words) / len(first_words | second_words)


def reciprocal_rank_fusion(rankings: list[list[str]], k: int = RRF_K) -> list[str]:
    """Merge rankings of ids; ids ranked well in several lists rise to the top."""
    scores: dict[str, float] = {}
    for ranking in rankings:
        for rank, key in enumerate(ranking):
            scores[key] = scores.get(key, 0.0) + 1.0 / (k + rank + 1)
    ordered = sorted(scores.items(), key=itemgetter(1), reverse=True)
    return [key for key, _ in ordered]


class ContextRetriever:
    def __init__(
        self,
        state: State,
        *,
        max_meetings: int = 5,
        max_chunks_per_meeting: int = 2,
        jira_limit: int = 6,
        keyword_item_limit: int = 15,
        lookback_days: int | None = 365,
        min_similarity: float | None = None,
    ) -> None:
        """
        All limits are starting values, not tuned results. Tune them on a set
        of labelled meetings.

        lookback_days limits history to recent meetings (None = no limit).
        min_similarity assumes the container's vector policy uses cosine
        (higher is more similar). Leave it None to rely on ranking only.
        """
        self._state = state
        self._max_meetings = max_meetings
        self._max_chunks_per_meeting = max_chunks_per_meeting
        self._jira_limit = jira_limit
        self._keyword_item_limit = keyword_item_limit
        self._lookback_days = lookback_days
        self._min_similarity = min_similarity

    async def retrieve(
        self,
        *,
        meeting_id: str,
        query_text: str,
        project_key: str,
        item_ids: list[UUID] | None = None,
        scan_text: str = "",
    ) -> HistoricalContext:
        """Gather history for query_text, excluding the current meeting.

        item_ids: current-meeting items whose proposals from an earlier run
            should be returned too.
        scan_text: extra current-meeting text (e.g. evidence quotes) searched
            for explicit Jira issue keys.
        """
        vectors = await _embed_texts([query_text], self._state)
        embedding = vectors[0]
        keywords = extract_keywords(query_text)
        mentioned_keys = extract_issue_keys(f"{query_text}\n{scan_text}")
        since = self._since()

        documents = await self._search_history(meeting_id, embedding, keywords, since)
        documents = await self._apply_meeting_dates(documents, since)
        jira_candidates = await self._search_jira(project_key, embedding, keywords, mentioned_keys)

        historical_items = await self._historical_items(
            meeting_id, HistoricalContext(documents=documents).meeting_ids, keywords, since,
        )
        historical_items = await self._apply_tracking(historical_items, jira_candidates, project_key)

        proposal_item_ids = list(item_ids or [])
        for historical_item in historical_items:
            proposal_item_ids.append(historical_item.item_id)
        issue_keys = []
        for candidate in jira_candidates:
            issue_keys.append(candidate.issue_key)
        prior_proposals = await fetch_related_proposals(proposal_item_ids, issue_keys, self._state)
        jira_candidates = await self._add_proposal_issues(jira_candidates, prior_proposals, project_key)

        return HistoricalContext(
            documents=documents,
            historical_items=historical_items,
            prior_proposals=prior_proposals,
            jira_candidates=jira_candidates,
        )

    async def _add_proposal_issues(
        self,
        candidates: list[JiraCandidate],
        prior_proposals: list[PriorProposal],
        project_key: str,
    ) -> list[JiraCandidate]:
        """Add Jira issues that earlier proposals created or targeted, so a
        change to already-approved work can become an update of that issue."""
        known = set()
        for candidate in candidates:
            known.add(candidate.issue_key)
        missing: list[str] = []
        for proposal in prior_proposals:
            key = proposal.jira_issue_key or proposal.existing_issue_key
            if key and key not in known and key not in missing:
                missing.append(key)
        if not missing:
            return candidates

        rows = await self._query(
            f"SELECT {JIRA_FIELDS} FROM c WHERE c.data_source = 'jira_issues' "
            "AND c.project_key = @project_key AND ARRAY_CONTAINS(@keys, c.issue_key)",
            [
                {"name": "@project_key", "value": project_key},
                {"name": "@keys", "value": missing},
            ],
        )
        extended = list(candidates)
        for row in rows:
            candidate = _jira_row_to_candidate(row)
            if candidate is not None:
                extended.append(candidate)
        return extended

    def _since(self) -> datetime | None:
        if self._lookback_days is None:
            return None
        return datetime.now(timezone.utc) - timedelta(days=self._lookback_days)

    # -- Historical meetings ------------------------------------------------

    async def _apply_meeting_dates(
        self,
        documents: list[SourceDocument],
        since: datetime | None,
    ) -> list[SourceDocument]:
        """Replace Cosmos dates (set when a meeting was indexed) with the real
        meeting dates from PostgreSQL, then re-apply the date window."""
        meeting_ids = _parse_uuids(HistoricalContext(documents=documents).meeting_ids)
        dates = await fetch_meeting_dates(meeting_ids, self._state)

        kept: list[SourceDocument] = []
        for document in documents:
            real_date = dates.get(document.meeting_id or "")
            if real_date is not None:
                if since is not None and datetime.fromisoformat(real_date) < since:
                    continue
                document = document.model_copy(update={"meeting_date": real_date})
            kept.append(document)
        return kept

    async def _apply_tracking(
        self,
        items: list[HistoricalItem],
        jira_candidates: list[JiraCandidate],
        project_key: str,
    ) -> list[HistoricalItem]:
        """Work out what became of each earlier item: rejected, only suggested,
        or tracked in a Jira issue that is still open or already resolved."""
        resolved_by_key: dict[str, bool] = {}
        for candidate in jira_candidates:
            resolved_by_key[candidate.issue_key] = candidate.is_resolved

        missing_keys: list[str] = []
        for item in items:
            key = item.tracked_issue_key
            if key and key not in resolved_by_key and key not in missing_keys:
                missing_keys.append(key)
        if missing_keys:
            rows = await self._query(
                "SELECT c.issue_key, c.status FROM c "
                "WHERE c.data_source = 'jira_issues' AND c.project_key = @project_key "
                "AND ARRAY_CONTAINS(@keys, c.issue_key)",
                [
                    {"name": "@project_key", "value": project_key},
                    {"name": "@keys", "value": missing_keys},
                ],
            )
            for row in rows:
                resolved_by_key[row["issue_key"]] = is_resolved_status(row.get("status"))

        tracked: list[HistoricalItem] = []
        for item in items:
            tracked.append(item.model_copy(update={"tracking": _tracking_for(item, resolved_by_key)}))
        return tracked

    async def _search_history(
        self,
        meeting_id: str,
        embedding: list[float],
        keywords: list[str],
        since: datetime | None,
    ) -> list[SourceDocument]:
        # Over-fetch so a single talkative meeting can't fill every slot.
        top = self._max_meetings * self._max_chunks_per_meeting * 3
        base_filter = (
            "c.data_source = 'meeting_history' "
            "AND c.content_type IN ('transcript_chunk', 'meeting_summary') "
            "AND c.meeting_id != @meeting_id"
        )
        parameters: list[dict[str, Any]] = [
            {"name": "@top", "value": top},
            {"name": "@meeting_id", "value": meeting_id},
        ]
        if since is not None:
            # Meeting dates are stored as UTC ISO strings, so string order is time order.
            base_filter += " AND c.meeting_date >= @since"
            parameters.append({"name": "@since", "value": since.isoformat()})

        vector_rows = await self._query(
            f"SELECT TOP @top {HISTORY_FIELDS}, "
            "VectorDistance(c.embedding, @embedding) AS similarity "
            f"FROM c WHERE {base_filter} "
            "ORDER BY VectorDistance(c.embedding, @embedding)",
            parameters + [{"name": "@embedding", "value": embedding}],
        )
        vector_rows = self._filter_similarity(vector_rows)

        keyword_rows = await self._keyword_query(HISTORY_FIELDS, base_filter, parameters, keywords)

        rows_by_id = _index_rows(vector_rows, keyword_rows)
        fused_ids = reciprocal_rank_fusion([_row_ids(vector_rows), _row_ids(keyword_rows)])

        per_meeting: dict[str, list[SourceDocument]] = {}
        for document_id in fused_ids:
            document = _history_row_to_document(rows_by_id[document_id])
            if document is None or document.meeting_id is None:
                continue
            meeting_documents = per_meeting.get(document.meeting_id)
            if meeting_documents is None:
                if len(per_meeting) >= self._max_meetings:
                    continue
                meeting_documents = []
                per_meeting[document.meeting_id] = meeting_documents
            if len(meeting_documents) < self._max_chunks_per_meeting:
                meeting_documents.append(document)

        documents: list[SourceDocument] = []
        for meeting_documents in per_meeting.values():
            documents.extend(meeting_documents)
        return documents

    async def _historical_items(
        self,
        meeting_id: str,
        retrieved_meeting_ids: list[str],
        keywords: list[str],
        since: datetime | None,
    ) -> list[HistoricalItem]:
        """Items from retrieved meetings plus keyword matches from any earlier meeting."""
        from_meetings = await fetch_items_for_meetings(_parse_uuids(retrieved_meeting_ids), self._state)
        from_keywords = await search_items_by_keywords(
            keywords, UUID(meeting_id), self._state,
            since=since, limit=self._keyword_item_limit,
        )

        merged: dict[UUID, HistoricalItem] = {}
        for item in from_keywords + from_meetings:
            merged[item.item_id] = item
        return list(merged.values())

    # -- Jira ---------------------------------------------------------------

    async def _search_jira(
        self,
        project_key: str,
        embedding: list[float],
        keywords: list[str],
        mentioned_keys: list[str],
    ) -> list[JiraCandidate]:
        base_filter = "c.data_source = 'jira_issues' AND c.project_key = @project_key"
        parameters: list[dict[str, Any]] = [
            {"name": "@top", "value": self._jira_limit * 2},
            {"name": "@project_key", "value": project_key},
        ]

        vector_rows = await self._query(
            f"SELECT TOP @top {JIRA_FIELDS}, "
            "VectorDistance(c.embedding, @embedding) AS similarity "
            f"FROM c WHERE {base_filter} "
            "ORDER BY VectorDistance(c.embedding, @embedding)",
            parameters + [{"name": "@embedding", "value": embedding}],
        )
        vector_rows = self._filter_similarity(vector_rows)
        keyword_rows = await self._keyword_query(JIRA_FIELDS, base_filter, parameters, keywords)

        mentioned_rows: list[dict[str, Any]] = []
        if mentioned_keys:
            mentioned_rows = await self._query(
                f"SELECT {JIRA_FIELDS} FROM c WHERE {base_filter} "
                "AND ARRAY_CONTAINS(@keys, c.issue_key)",
                [
                    {"name": "@project_key", "value": project_key},
                    {"name": "@keys", "value": mentioned_keys},
                ],
            )

        rows_by_id = _index_rows(vector_rows, keyword_rows, mentioned_rows)
        fused_ids = reciprocal_rank_fusion([_row_ids(vector_rows), _row_ids(keyword_rows)])

        # Issues named explicitly in the meeting always come first.
        ordered_ids = _row_ids(mentioned_rows)
        for document_id in fused_ids:
            if document_id not in ordered_ids:
                ordered_ids.append(document_id)

        candidates: list[JiraCandidate] = []
        for document_id in ordered_ids:
            candidate = _jira_row_to_candidate(rows_by_id[document_id])
            if candidate is not None:
                candidates.append(candidate)
            if len(candidates) >= self._jira_limit + len(mentioned_rows):
                break
        return candidates

    # -- Cosmos helpers -----------------------------------------------------

    async def _keyword_query(
        self,
        fields: str,
        base_filter: str,
        parameters: list[dict[str, Any]],
        keywords: list[str],
    ) -> list[dict[str, Any]]:
        """Rows containing any keyword, ranked by how many distinct keywords match."""
        if not keywords:
            return []

        conditions = []
        keyword_parameters = []
        for index, keyword in enumerate(keywords):
            name = f"@kw{index}"
            conditions.append(f"CONTAINS(c.text, {name}, true)")
            keyword_parameters.append({"name": name, "value": keyword})

        rows = await self._query(
            f"SELECT TOP @top {fields} FROM c WHERE {base_filter} "
            f"AND ({' OR '.join(conditions)})",
            parameters + keyword_parameters,
        )

        scored = []
        for position, row in enumerate(rows):
            text = str(row.get("text") or "").casefold()
            matches = 0
            for keyword in keywords:
                if keyword in text:
                    matches += 1
            # Position breaks ties, so dict rows are never compared.
            scored.append((-matches, position, row))
        scored.sort()

        ranked = []
        for _, _, row in scored:
            ranked.append(row)
        return ranked

    async def _query(self, query: str, parameters: list[dict[str, Any]]) -> list[dict[str, Any]]:
        results = self._state.cosmos_container.query_items(query=query, parameters=parameters)
        rows: list[dict[str, Any]] = []
        async for row in results:
            rows.append(row)
        return rows

    def _filter_similarity(self, rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
        if self._min_similarity is None:
            return rows
        kept = []
        for row in rows:
            similarity = row.get("similarity")
            if similarity is None or similarity >= self._min_similarity:
                kept.append(row)
        return kept


# ---------------------------------------------------------------------------
# Row conversion
# ---------------------------------------------------------------------------

def _row_ids(rows: list[dict[str, Any]]) -> list[str]:
    ids = []
    for row in rows:
        if row.get("id"):
            ids.append(row["id"])
    return ids


def _index_rows(*row_lists: list[dict[str, Any]]) -> dict[str, dict[str, Any]]:
    """id -> row. The first list wins, so vector rows keep their similarity."""
    indexed: dict[str, dict[str, Any]] = {}
    for rows in row_lists:
        for row in rows:
            if row.get("id") and row["id"] not in indexed:
                indexed[row["id"]] = row
    return indexed


def _history_row_to_document(row: dict[str, Any]) -> SourceDocument | None:
    if not row.get("id") or not row.get("text") or not row.get("meeting_id"):
        return None

    if row.get("content_type") == "meeting_summary":
        source = EvidenceSource.HISTORICAL_SUMMARY
    else:
        source = EvidenceSource.HISTORICAL_TRANSCRIPT

    meeting_date = row.get("meeting_date")
    chunk_index = row.get("chunk_index")

    return SourceDocument(
        document_id=row["id"],
        source=source,
        text=row["text"],
        meeting_id=str(row["meeting_id"]),
        meeting_date=str(meeting_date) if meeting_date is not None else None,
        chunk_index=chunk_index if isinstance(chunk_index, int) and chunk_index >= 0 else None,
        similarity=row.get("similarity"),
    )


def _jira_row_to_candidate(row: dict[str, Any]) -> JiraCandidate | None:
    if not row.get("id") or not row.get("issue_key") or not row.get("text"):
        return None
    return JiraCandidate(
        document_id=row["id"],
        issue_key=row["issue_key"],
        summary=row.get("summary") or "",
        description=row.get("description"),
        status=row.get("status"),
        issue_type=row.get("issue_type"),
        project_key=row.get("project_key"),
        priority=row.get("priority"),
        assignee=row.get("assignee_display_name"),
        updated_at=row.get("jira_updated_at"),
        parent_issue_key=row.get("parent_issue_key"),
        labels=row.get("labels") or [],
        components=row.get("components") or [],
        text=str(row["text"])[:MAX_JIRA_TEXT_CHARS],
        similarity=row.get("similarity"),
        jira_url=row.get("jira_url"),
    )


def _tracking_for(item: HistoricalItem, resolved_by_key: dict[str, bool]) -> Tracking:
    if item.review_status == "rejected":
        return Tracking.REJECTED
    if item.tracked_issue_key and item.tracked_issue_key in resolved_by_key:
        if resolved_by_key[item.tracked_issue_key]:
            return Tracking.DONE
        return Tracking.OPEN
    if item.review_status is not None:
        return Tracking.SUGGESTED
    return Tracking.UNTRACKED


def _parse_uuids(values: list[str]) -> list[UUID]:
    parsed: list[UUID] = []
    for value in values:
        try:
            parsed.append(UUID(value))
        except ValueError:
            logger.info("Ignoring non-UUID meeting id %r", value)
    return parsed


# ---------------------------------------------------------------------------
# Prompt rendering
# ---------------------------------------------------------------------------

def jira_documents(candidates: list[JiraCandidate]) -> list[SourceDocument]:
    """Jira candidates as citable documents."""
    documents: list[SourceDocument] = []
    for candidate in candidates:
        documents.append(
            SourceDocument(
                document_id=candidate.document_id,
                source=EvidenceSource.JIRA_ISSUE,
                text=candidate.text,
                similarity=candidate.similarity,
            )
        )
    return documents


def render_jira_candidates(candidates: list[JiraCandidate]) -> str:
    if not candidates:
        return "<jira_candidates>(none found)</jira_candidates>"

    blocks = []
    for candidate in candidates:
        state = "RESOLVED" if candidate.is_resolved else "OPEN"
        header = [
            f'key="{candidate.issue_key}"',
            f'id="{candidate.document_id}"',
            f'status="{candidate.status or "unknown"}"',
            f'state="{state}"',
            f'assignee="{candidate.assignee or "unassigned"}"',
            f'updated="{candidate.updated_at or "unknown"}"',
        ]
        if candidate.parent_issue_key:
            header.append(f'parent="{candidate.parent_issue_key}"')
        blocks.append(f"<issue {' '.join(header)}>\n{candidate.text}\n</issue>")
    return "<jira_candidates>\n" + "\n\n".join(blocks) + "\n</jira_candidates>"


def describe_historical_item(item: HistoricalItem) -> str:
    severity = f", severity {item.severity.value}" if item.severity else ""
    issue = f" {item.tracked_issue_key}" if item.tracked_issue_key else ""
    return (
        f"meeting {item.meeting_id} ({item.meeting_date or 'date unknown'}) "
        f"[{item.item_type.value}{severity}, status: {item.tracking.value}{issue}] {item.content}"
    )


def render_context(context: HistoricalContext) -> str:
    """Render retrieved history as prompt sections."""
    sections = []

    if context.documents:
        sections.append(
            "<historical_meeting_excerpts>\n"
            "Similar wording is a lead, not proof of the same problem.\n"
            f"{format_documents(context.documents)}\n"
            "</historical_meeting_excerpts>"
        )
    else:
        sections.append("<historical_meeting_excerpts>(none found)</historical_meeting_excerpts>")

    item_lines = []
    for item in context.historical_items:
        item_lines.append(f"- {describe_historical_item(item)}")
    sections.append(
        "<historical_items>\n"
        "status: done = its Jira issue is resolved; open = tracked in an open issue; "
        "suggested = proposed but not in Jira yet; rejected = user declined the ticket; "
        "untracked = never proposed.\n"
        + ("\n".join(item_lines) or "(none)")
        + "\n</historical_items>"
    )

    proposal_lines = []
    for proposal in context.prior_proposals:
        proposal_type = proposal.proposal_type.value if proposal.proposal_type else "unknown"
        jira_key = proposal.jira_issue_key or proposal.existing_issue_key or "no issue"
        edited = ", edited by user" if proposal.edited_by_user else ""
        proposal_lines.append(
            f"- id={proposal.proposal_id} [{proposal_type}, review {proposal.review_status}{edited}, "
            f"execution {proposal.execution_status}, {jira_key}] {proposal.summary}"
        )
        if proposal.description:
            description = " ".join(proposal.description.split())
            if len(description) > MAX_PRIOR_DESCRIPTION_CHARS:
                description = description[:MAX_PRIOR_DESCRIPTION_CHARS].rstrip() + "..."
            proposal_lines.append(f"  description: {description}")
    sections.append(
        "<prior_ticket_proposals>\n"
        "Pending suggestions are drafts the user hasn't reviewed yet; they can be "
        "revised. A rejection means the user didn't want that ticket then. It is "
        "not proof the problem doesn't exist.\n"
        + ("\n".join(proposal_lines) or "(none)")
        + "\n</prior_ticket_proposals>"
    )

    sections.append(render_jira_candidates(context.jira_candidates))
    return "\n\n".join(sections)
