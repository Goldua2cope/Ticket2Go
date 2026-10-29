import type {
  IssueType,
  Meeting,
  ParentOption,
  SprintOption,
  Task,
  TaskRevision,
  TaskStatus,
} from "./types";

// parent_issue_key holds a parent suggestion's id until that suggestion is in Jira.
export function isSuggestionRef(parent?: string): boolean {
  return (
    !!parent &&
    /^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$/i.test(parent)
  );
}

export type Proposal = {
  proposal_id: string;
  summary: string;
  review_status: TaskStatus | "edited";
  description: string | null;
  rationale: string;
  proposal_type: "create" | "update";
  jira_url?: string | null;
  jira_status?: string | null;
  placement_message?: string;
  jira_issue_key?: string | null;
  priority?: Task["priority"] | null;
  assignee_account_id?: string | null;
  execution_status?: Task["executionStatus"];
  review_token?: string;
  issue_type?: IssueType | null;
  parent_issue_key?: string | null;
  parent_issue_status?: string | null;
  parent_issue_done?: boolean;
  parent_proposal_summary?: string | null;
  parent_proposal_review_status?: TaskStatus | "edited" | null;
  parent_proposal_issue_key?: string | null;
  origin_meeting_id?: string | null;
  revisions?: TaskRevision[];
  target_sprint_id?: number | null;
};

export type SuggestionResponse = {
  meeting_id: string;
  proposals: Proposal[];
  status: {
    outcome: "completed" | "partial" | "no_items";
    proposals_revised?: number;
  };
  revisions: { proposal_id: string; change: string }[];
};

// The backend's error text. For invalid input FastAPI sends a list of
// problems instead of a sentence; join their messages.
function errorDetail(detail: unknown): string | undefined {
  if (typeof detail === "string") return detail;
  if (Array.isArray(detail)) {
    const messages = detail.map((problem) =>
      problem && typeof problem === "object" && "msg" in problem
        ? String(problem.msg)
        : String(problem),
    );
    return messages.length ? `Invalid request: ${messages.join("; ")}` : undefined;
  }
  return undefined;
}

export async function requestSuggestions(
  meetingId: string,
): Promise<SuggestionResponse> {
  const response = await fetch(
    `/meetings/${encodeURIComponent(meetingId)}/suggestions`,
    {
      method: "POST",
      signal: AbortSignal.timeout(360_000),
    },
  );
  let data;
  try {
    data = await response.json();
  } catch {
    throw new Error(
      "Could not read the suggestions response. Check the backend connection and try again.",
    );
  }
  if (!response.ok) {
    throw new Error(
      errorDetail(data.detail) ||
        (data.status?.outcome === "already_running"
          ? "Suggestions are already being generated for this meeting. Try again later."
          : "Could not generate task suggestions. Please try again."),
    );
  }
  if (
    !data ||
    data.meeting_id !== meetingId ||
    !Array.isArray(data.proposals) ||
    !data.status ||
    !["completed", "partial", "no_items"].includes(data.status.outcome)
  ) {
    throw new Error("The server returned an unexpected suggestions response.");
  }
  return data;
}

export function proposalToTask(proposal: Proposal): Task {
  return {
    id: proposal.proposal_id,
    title: proposal.summary,
    status:
      proposal.review_status === "edited" ? "pending" : proposal.review_status,
    owner: proposal.assignee_account_id || "Not specified",
    priority: proposal.priority || "Not specified",
    description: proposal.description || "",
    rationale: proposal.rationale,
    proposalType: proposal.proposal_type,
    existingIssueKey:
      proposal.proposal_type === "update"
        ? proposal.jira_issue_key || undefined
        : undefined,
    jiraUrl: proposal.jira_url || undefined,
    jiraStatus: proposal.jira_status || undefined,
    placementMessage: proposal.placement_message,
    executionStatus: proposal.execution_status,
    reviewToken: proposal.review_token,
    issueType: proposal.issue_type || undefined,
    parentIssueKey: proposal.parent_issue_key || undefined,
    parentIssueStatus: proposal.parent_issue_status || undefined,
    parentIssueDone: proposal.parent_issue_done,
    parentProposalSummary: proposal.parent_proposal_summary || undefined,
    parentProposalStatus: proposal.parent_proposal_review_status || undefined,
    parentProposalIssueKey: proposal.parent_proposal_issue_key || undefined,
    originMeetingId: proposal.origin_meeting_id || undefined,
    revisions: proposal.revisions || [],
    targetSprintId: proposal.target_sprint_id ?? undefined,
  };
}

// Only a new Story, Task or Bug is placed in a sprint or the backlog: a
// subtask follows its parent, and epics are not sprint work.
export function placesInSprint(task: Task): boolean {
  return (
    task.proposalType === "create" &&
    task.issueType !== "Subtask" &&
    task.issueType !== "Epic"
  );
}

// A revised suggestion is listed under its own meeting and under each meeting
// that revised it. Lists that span meetings show it once, under its own meeting.
export function uniqueTasks(
  meetings: Meeting[],
): { task: Task; meeting: Meeting }[] {
  const byId = new Map<string, { task: Task; meeting: Meeting }>();
  for (const meeting of meetings)
    for (const task of meeting.tasks) {
      const known = byId.get(task.id);
      if (!known || meeting.id === task.originMeetingId)
        byId.set(task.id, { task, meeting });
    }
  return [...byId.values()];
}

export function mergeSuggestions(
  meetings: Meeting[],
  result: { meeting_id: string; proposals: Proposal[] },
): Meeting[] {
  const incoming = new Map(
    result.proposals.map((proposal) => [
      proposal.proposal_id,
      proposalToTask(proposal),
    ]),
  );
  return meetings.map((meeting) => {
    // Update the suggestion wherever it is already listed.
    const tasks = meeting.tasks.map((task) => incoming.get(task.id) || task);
    if (meeting.id !== result.meeting_id) return { ...meeting, tasks };
    // This meeting also lists earlier suggestions it revised.
    const listed = new Set(tasks.map((task) => task.id));
    return {
      ...meeting,
      tasks: [
        ...tasks,
        ...[...incoming.values()].filter((task) => !listed.has(task.id)),
      ],
    };
  });
}

async function reviewRequest(path: string, method = "GET", body?: unknown) {
  const response = await fetch(path, {
    method,
    headers: body ? { "Content-Type": "application/json" } : undefined,
    body: body ? JSON.stringify(body) : undefined,
    signal: AbortSignal.timeout(120_000),
  });
  const data = await response.json();
  if (!response.ok)
    throw new Error(
      errorDetail(data.detail) ||
        "Could not save the decision. Refresh its status before trying again.",
    );
  return data;
}

export async function loadSuggestions(
  meetingId: string,
): Promise<{ meeting_id: string; proposals: Proposal[] }> {
  return reviewRequest(
    `/meetings/${encodeURIComponent(meetingId)}/suggestions`,
  );
}

export async function loadProposal(id: string): Promise<Task> {
  return proposalToTask(
    await reviewRequest(`/proposals/${encodeURIComponent(id)}`),
  );
}

export async function loadSubtaskParents(id: string): Promise<ParentOption[]> {
  const data = await reviewRequest(
    `/proposals/${encodeURIComponent(id)}/parent-options`,
  );
  return data.issues;
}

// Every pending card asks for the sprints; share one request for a minute.
let sprintCache: { at: number; request: Promise<SprintOption[]> } | null = null;

export function loadSprintOptions(): Promise<SprintOption[]> {
  if (!sprintCache || Date.now() - sprintCache.at > 60_000) {
    const request = reviewRequest("/proposals/sprint-options").then(
      (data) => data.sprints as SprintOption[],
    );
    request.catch(() => {
      sprintCache = null; // try again next time
    });
    sprintCache = { at: Date.now(), request };
  }
  return sprintCache.request;
}

// The sprint a subtask under this issue would join, or null for the backlog.
export async function loadParentSprint(
  issueKey: string,
): Promise<SprintOption | null> {
  const data = await reviewRequest(
    `/proposals/parent-sprint?issue_key=${encodeURIComponent(issueKey)}`,
  );
  return data.sprint;
}

export async function reviewTask(
  task: Task,
  action: "edit" | "approve" | "reject",
): Promise<Task> {
  if (!task.reviewToken)
    throw new Error("Reload suggestions before reviewing this task.");
  const path = `/proposals/${encodeURIComponent(task.id)}`;
  const body =
    action === "edit"
      ? {
          review_token: task.reviewToken,
          summary: task.title,
          description: task.description || "",
          priority: task.priority === "Not specified" ? null : task.priority,
          issue_type: task.issueType ?? null,
          // Only a subtask has a parent.
          parent_issue_key:
            task.issueType === "Subtask" ? (task.parentIssueKey ?? null) : null,
          target_sprint_id: placesInSprint(task)
            ? (task.targetSprintId ?? null)
            : null,
        }
      : { review_token: task.reviewToken };
  return proposalToTask(
    await reviewRequest(
      action === "edit" ? path : `${path}/${action}`,
      action === "edit" ? "PATCH" : "POST",
      body,
    ),
  );
}
