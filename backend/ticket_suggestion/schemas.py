"""
Data models for the ticket suggestion feature.

Two kinds of models live here:
- LLM output models (suffix "Output"): structured-output schemas sent to the
  model. They avoid defaults and string-length constraints so they stay
  compatible with strict JSON-schema mode; limits are enforced in code.
- Application models: validated data passed between steps and persisted
  in PostgreSQL.
"""

from enum import StrEnum
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field


# ---------------------------------------------------------------------------
# Enums (values match the CHECK constraints in SQL-queries/table_creation.sql)
# ---------------------------------------------------------------------------

class ItemType(StrEnum):
    ACTION = "action"
    DECISION = "decision"
    QUESTION = "question"
    PROBLEM = "problem"


class Severity(StrEnum):
    LOW = "low"
    MEDIUM = "medium"
    HIGH = "high"
    CRITICAL = "critical"

class Priority(StrEnum):
    LOWEST = 'Lowest'
    LOW = 'Low'
    MEDIUM = 'Medium'
    HIGH = 'High'
    HIGHEST = 'Highest'

class EvidenceSource(StrEnum):
    TRANSCRIPT = "meeting_transcript"
    SUMMARY = "meeting_summary"
    HISTORICAL_TRANSCRIPT = "historical_transcript"
    HISTORICAL_SUMMARY = "historical_summary"
    JIRA_ISSUE = "jira_issue"


class Recurrence(StrEnum):
    NEW = "new"
    RECURRING = "recurring"
    UNCERTAIN = "uncertain"


class ProposalType(StrEnum):
    CREATE = "create"
    UPDATE = "update"


class IssueType(StrEnum):
    """Jira work types, named as in the SCRUM project."""

    EPIC = "Epic"
    STORY = "Story"
    TASK = "Task"
    BUG = "Bug"
    SUBTASK = "Subtask"


# The work types Jira allows as a Subtask's parent.
SUBTASK_PARENT_TYPES = frozenset({IssueType.STORY, IssueType.TASK, IssueType.BUG})
# The work types placed in a sprint or the backlog. A Subtask follows its
# parent's sprint, and epics are not sprint work.
SPRINT_ISSUE_TYPES = frozenset({IssueType.STORY, IssueType.TASK, IssueType.BUG, IssueType.EPIC})


def parent_suggestion_id(parent_issue_key: str | None) -> UUID | None:
    """ticket_proposals.parent_issue_key holds a Jira key, or the proposal_id of a
    parent suggestion until that suggestion is created in Jira. Returns the
    proposal_id in the second case, None otherwise."""
    if not parent_issue_key:
        return None
    try:
        return UUID(parent_issue_key)
    except ValueError:
        return None  # a Jira key such as "SCRUM-5"


class ProposalOutcome(StrEnum):
    CREATE = "create"
    UPDATE = "update"
    REVISE = "revise"   # change a pending suggestion from an earlier meeting
    NONE = "none"


# Jira status names treated as resolved when the status category is unknown.
RESOLVED_STATUS_NAMES = frozenset({
    "done", "closed", "resolved", "complete", "completed", "won't do", "wont do",
    "cancelled", "canceled", "rejected", "duplicate",
})


# ---------------------------------------------------------------------------
# Source documents: anything the LLM may quote as evidence
# ---------------------------------------------------------------------------

class SourceDocument(BaseModel):
    """Citable text: a transcript chunk, a summary, or a Jira issue.
    `document_id` matches the Cosmos DB document id."""

    model_config = ConfigDict(extra="ignore")

    document_id: str = Field(min_length=1)
    source: EvidenceSource
    text: str = Field(min_length=1)
    meeting_id: str | None = None
    meeting_date: str | None = None
    chunk_index: int | None = None
    similarity: float | None = None


class EvidenceRef(BaseModel):
    """A verified quote. Offsets are assigned by the application, never the LLM."""

    model_config = ConfigDict(extra="forbid")

    document_id: str
    source: EvidenceSource
    quote: str
    meeting_id: str | None = None
    start_char: int = Field(ge=0)
    end_char: int = Field(ge=0)


# ---------------------------------------------------------------------------
# LLM output models
# ---------------------------------------------------------------------------

class QuoteOutput(BaseModel):
    model_config = ConfigDict(extra="forbid")

    document_id: str = Field(
        description="Id of the document the quote is copied from, exactly as shown.",
    )
    quote: str = Field(
        description="Short verbatim excerpt copied from that document.",
    )


class ExtractedItemOutput(BaseModel):
    model_config = ConfigDict(extra="forbid")

    ref: str = Field(description="Reference for a new item: new-1, new-2, ...")
    item_type: ItemType
    content: str = Field(
        description="One concise sentence describing the item (max 500 characters).",
    )
    owner: str | None = Field(
        description="Person or team responsible, only if explicitly stated.",
    )
    deadline: str | None = Field(
        description="Deadline exactly as stated (e.g. 'Friday', '2026-11-01'), only if stated.",
    )
    component: str | None = Field(
        description="Affected system, service, or component, only if named.",
    )
    severity: Severity | None = Field(
        description="Only for problems, and only when the meeting supports it.",
    )
    related_problem_ref: str | None = Field(
        description=(
            "For actions, decisions and questions: the ref of the problem "
            "they address (a new-N ref or an earlier ref), otherwise null."
        ),
    )
    evidence: list[QuoteOutput] = Field(
        description="At least one verbatim quote from the transcript chunk.",
    )


class ExtractionOutput(BaseModel):
    model_config = ConfigDict(extra="forbid")

    items: list[ExtractedItemOutput]


class OccurrenceOutput(BaseModel):
    model_config = ConfigDict(extra="forbid")

    meeting_ids: list[str] = Field(
        description=(
            "Earlier meetings that discussed this one occurrence. Several "
            "meetings discussing the same ongoing incident form ONE occurrence."
        ),
    )
    description: str = Field(description="What happened in this occurrence.")


class AssessmentOutput(BaseModel):
    model_config = ConfigDict(extra="forbid")

    is_problem: bool = Field(
        description="False if the item is really an action, request, or discussion point.",
    )
    recurrence: Recurrence
    earlier_occurrences: list[OccurrenceOutput] = Field(
        description="Distinct earlier occurrences of the same underlying problem.",
    )
    current_is_new_occurrence: bool = Field(
        description=(
            "True if the current meeting reports a fresh occurrence; false if it "
            "continues discussing the latest earlier occurrence."
        ),
    )
    unresolved: bool
    likely_existing_issue: str | None = Field(
        description="Key of the listed Jira candidate most likely tracking this problem.",
    )
    existing_issue_covers_problem: bool = Field(
        description="True if that issue already covers this problem's scope.",
    )
    sufficient_evidence: bool = Field(
        description="True if the sources are specific enough to justify a recommendation.",
    )
    evidence: list[QuoteOutput] = Field(
        description="Verbatim quotes from earlier meetings or Jira supporting the assessment.",
    )
    rationale: str


class ProposalOutput(BaseModel):
    model_config = ConfigDict(extra="forbid")

    outcome: ProposalOutcome
    existing_issue_key: str | None = Field(
        description="Required for update: key of a listed Jira candidate. Null otherwise.",
    )
    summary: str | None = Field(
        description=(
            "create: the new issue's summary (max 255 characters). "
            "update: a short title for the change. Null for none."
        ),
    )
    description: str | None = Field(
        description=(
            "create: the new issue's description. update: ONLY the text to append "
            "to the existing issue; never a rewrite of it. Null for none."
        ),
    )
    new_information: str | None = Field(
        description=(
            "update: what this meeting adds that the issue lacks. revise: the change "
            "to record on the pending suggestions, stated concretely (e.g. 'Deadline "
            "changed to next Wednesday; owner confirmed as Peter'). Null otherwise."
        ),
    )
    revise_proposal_ids: list[str] = Field(
        description=(
            "revise: ids of pending suggestions listed under prior_ticket_proposals "
            "that this item changes. Empty for every other outcome."
        ),
    )
    rationale: str = Field(description="Why this outcome, for the human reviewer.")
    evidence: list[QuoteOutput]
    priority: Priority | None = Field(
        description="create: defines its importance in relation to other issues"
        )
    issue_type: IssueType | None = Field(
        description="create: the work type. Never Epic. Null for update, revise and none.",
    )
    parent_issue_key: str | None = Field(
        description=(
            "create with Subtask: the key of an OPEN Story, Task or Bug in jira_candidates, "
            "or the id of a Story, Task or Bug suggestion in prior_ticket_proposals. "
            "Null otherwise."
        ),
    )


# ---------------------------------------------------------------------------
# Meeting items and historical context
# ---------------------------------------------------------------------------

class MeetingItem(BaseModel):
    """A validated item from the current meeting.

    owner, deadline and component have no meeting_items column yet; they are
    used within the pipeline (prompts and proposals) but not persisted.
    """

    item_id: UUID
    meeting_id: UUID
    item_type: ItemType
    content: str = Field(min_length=1, max_length=500)
    owner: str | None = None
    deadline: str | None = None
    component: str | None = None
    severity: Severity | None = None
    related_problem_item_id: UUID | None = None
    evidence_refs: list[EvidenceRef] = Field(min_length=1)


class Tracking(StrEnum):
    """What happened to an earlier item, derived from its proposal and Jira."""

    UNTRACKED = "untracked"      # no proposal was registered
    SUGGESTED = "suggested"      # proposal pending, or approved but not yet in Jira
    REJECTED = "rejected"        # the user rejected the proposal
    OPEN = "open"                # tracked in a Jira issue that is still open
    DONE = "done"                # tracked in a Jira issue that is resolved


class HistoricalItem(BaseModel):
    """An item stored for an earlier meeting, with what became of it."""

    item_id: UUID
    meeting_id: UUID
    meeting_date: str | None = None
    item_type: ItemType
    content: str
    severity: Severity | None = None
    review_status: str | None = None
    execution_status: str | None = None
    tracked_issue_key: str | None = None
    tracking: Tracking = Tracking.UNTRACKED


class PriorProposal(BaseModel):
    """An earlier suggestion and what the user did with it."""

    proposal_id: UUID
    item_id: UUID
    proposal_type: ProposalType | None = None
    summary: str
    description: str | None = None
    review_status: str
    execution_status: str
    existing_issue_key: str | None = None
    jira_issue_key: str | None = None
    priority: Priority | None = None
    issue_type: str | None = None
    edited_by_user: bool = False


class JiraCandidate(BaseModel):
    """A Jira issue from the Cosmos DB index that may already track the work."""

    document_id: str
    issue_key: str
    summary: str
    description: str | None = None
    status: str | None = None
    issue_type: str | None = None
    project_key: str | None = None
    priority: str | None = None
    assignee: str | None = None
    updated_at: str | None = None
    parent_issue_key: str | None = None
    labels: list[str] = Field(default_factory=list)
    components: list[str] = Field(default_factory=list)
    text: str
    similarity: float | None = None
    jira_url: str | None = None

    @property
    def is_resolved(self) -> bool:
        return (self.status or "").strip().casefold() in RESOLVED_STATUS_NAMES


class HistoricalContext(BaseModel):
    """Everything retrieved about earlier meetings and Jira for one item."""

    documents: list[SourceDocument] = Field(default_factory=list)
    historical_items: list[HistoricalItem] = Field(default_factory=list)
    prior_proposals: list[PriorProposal] = Field(default_factory=list)
    jira_candidates: list[JiraCandidate] = Field(default_factory=list)

    @property
    def meeting_ids(self) -> list[str]:
        """Distinct historical meeting ids from documents, most relevant first."""
        seen: dict[str, None] = {}
        for document in self.documents:
            if document.meeting_id:
                seen[document.meeting_id] = None
        return list(seen)

    def known_meeting_ids(self) -> set[str]:
        """Every historical meeting id present in documents or items."""
        known = set(self.meeting_ids)
        for item in self.historical_items:
            known.add(str(item.meeting_id))
        return known

    def meeting_dates(self) -> dict[str, str]:
        """meeting_id -> date. PostgreSQL dates win over Cosmos ones."""
        dates: dict[str, str] = {}
        for document in self.documents:
            if document.meeting_id and document.meeting_date:
                dates[document.meeting_id] = document.meeting_date
        for item in self.historical_items:
            if item.meeting_date:
                dates[str(item.meeting_id)] = item.meeting_date
        return dates

    def candidate(self, issue_key: str | None) -> JiraCandidate | None:
        for candidate in self.jira_candidates:
            if candidate.issue_key == issue_key:
                return candidate
        return None


# ---------------------------------------------------------------------------
# Assessment and proposals
# ---------------------------------------------------------------------------

class Occurrence(BaseModel):
    meeting_ids: list[str]
    description: str
    first_observed_at: str | None = None
    last_observed_at: str | None = None


class ProblemAssessment(BaseModel):
    """Validated recurrence assessment for one current-meeting problem."""

    item_id: UUID
    is_problem: bool = True
    recurrence: Recurrence
    earlier_occurrences: list[Occurrence] = Field(default_factory=list)
    recurrence_count: int = Field(default=1, ge=1, description="Distinct occurrences, including the current one.")
    distinct_meeting_count: int = Field(default=1, ge=1, description="Meetings that raised it, including this one.")
    first_observed_at: str | None = None
    last_observed_at: str | None = None
    likely_existing_issue: str | None = None
    existing_issue_covers_problem: bool = False
    existing_issue_resolved: bool | None = None
    regressed_after_resolution: bool = False
    unresolved: bool = True
    sufficient_evidence: bool = True
    evidence: list[EvidenceRef] = Field(default_factory=list)
    rationale: str

    @property
    def matching_meeting_ids(self) -> list[str]:
        seen: dict[str, None] = {}
        for occurrence in self.earlier_occurrences:
            for meeting_id in occurrence.meeting_ids:
                seen[meeting_id] = None
        return list(seen)


class TicketProposal(BaseModel):
    """A ticket suggestion to register in ticket_proposals. Only a user can approve it.

    For create, `description` is the new issue's description. For update, it
    is the text to append to the existing issue; nothing is replaced.
    """

    proposal_id: UUID
    item_id: UUID
    proposal_type: ProposalType
    summary: str = Field(min_length=1, max_length=255)
    description: str | None = None
    existing_issue_key: str | None = None
    rationale: str
    evidence: list[EvidenceRef] = Field(min_length=1)
    related_meeting_ids: list[UUID] = Field(default_factory=list)
    priority: Priority | None = None
    issue_type: IssueType | None = None
    # A Jira key, or a parent suggestion's proposal_id (see parent_suggestion_id).
    parent_issue_key: str | None = None


class Abstention(BaseModel):
    """A 'none' outcome: no ticket suggested for this item, and why.
    Returned for transparency and evaluation; never registered as a proposal."""

    item_id: UUID
    item_type: ItemType | None = None
    item_content: str | None = None
    reason: str
    model_outcome: ProposalOutcome


class ProposalRevision(BaseModel):
    """A change to one pending suggestion from an earlier meeting, appended to
    its description. Pending suggestions are drafts the user hasn't reviewed,
    so a later meeting may refine them; reviewed ones are never touched."""

    proposal_id: UUID
    item_id: UUID          # the current-meeting item that caused the change
    item_content: str
    meeting_id: UUID       # the current meeting
    change: str
    evidence: list[EvidenceRef] = Field(default_factory=list)


class RevisionSet(BaseModel):
    """A 'revise' outcome: one item can revise several pending suggestions
    (e.g. one decision moving two deadlines)."""

    item_id: UUID
    revisions: list[ProposalRevision] = Field(min_length=1)


class RunOutcome(StrEnum):
    COMPLETED = "completed"              # every step succeeded
    PARTIAL = "partial"                  # some extraction windows or items failed
    FAILED = "failed"                    # nothing could be processed
    NO_ITEMS = "no_items"                # the meeting had nothing to extract
    ALREADY_RUNNING = "already_running"  # another run holds this meeting's lock


class RunStatus(BaseModel):
    """Counts describing one suggestion run, returned next to the proposals."""

    outcome: RunOutcome
    items_extracted: int = 0
    extraction_windows: int = 0
    extraction_windows_failed: int = 0
    candidates: int = 0
    proposals_registered: int = 0
    proposals_revised: int = 0
    # Revisions the model chose but that were not written: the target was no
    # longer pending, already had this item's note, came from a later meeting,
    # or saving failed. Counts proposal changes, not items.
    revisions_skipped: int = 0
    skipped_already_reviewed: int = 0
    abstentions: int = 0
    failures: int = 0
    recurring_problems: int = 0
    assessments_saved: int = 0
