import type { Meeting, Task, TaskStatus } from "./types";

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
      data.detail ||
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
  };
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
  const knownIds = new Set(
    meetings.flatMap((meeting) => meeting.tasks.map((task) => task.id)),
  );
  return meetings.map((meeting) => ({
    ...meeting,
    tasks: [
      ...meeting.tasks.map((task) => incoming.get(task.id) || task),
      ...(meeting.id === result.meeting_id
        ? [...incoming.values()].filter((task) => !knownIds.has(task.id))
        : []),
    ],
  }));
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
      data.detail ||
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
