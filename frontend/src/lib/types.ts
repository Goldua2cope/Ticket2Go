export type TaskAction = "edit" | "approve" | "reject" | "refresh";
export type TaskStatus = "pending" | "approved" | "rejected";
export const PRIORITY_OPTIONS = [
  "Not specified",
  "Lowest",
  "Low",
  "Medium",
  "High",
  "Highest",
] as const;
export type TaskPriority = (typeof PRIORITY_OPTIONS)[number];
export const ISSUE_TYPE_OPTIONS = [
  "Task",
  "Story",
  "Bug",
  "Epic",
  "Subtask",
] as const;
export type IssueType = (typeof ISSUE_TYPE_OPTIONS)[number];
// An existing Jira issue a subtask can go under.
export type ParentOption = {
  issue_key: string;
  summary: string;
  issue_type: "Story" | "Task" | "Bug";
  status: string;
  is_done: boolean;
};

// An active or future sprint a new issue can be placed in.
export type SprintOption = {
  id: number;
  name: string;
  state: "active" | "future";
  start_date: string | null;
  end_date: string | null;
};

// A later meeting that changed this suggestion while it was still pending.
export type TaskRevision = {
  meeting_id: string;
  meeting_title: string;
  change: string;
  created_at: string;
};

export type Task = {
  id: string;
  title: string;
  owner: string;
  priority: TaskPriority;
  status: TaskStatus;
  description?: string;
  rationale?: string;
  proposalType?: "create" | "update";
  existingIssueKey?: string;
  jiraUrl?: string;
  jiraStatus?: string;
  placementMessage?: string;
  executionStatus?: "not_started" | "in_progress" | "completed" | "blocked";
  reviewToken?: string;
  issueType?: IssueType;
  // A Jira key, or a parent suggestion's id until that suggestion is in Jira.
  parentIssueKey?: string;
  parentIssueStatus?: string;
  parentIssueDone?: boolean;
  parentProposalSummary?: string;
  parentProposalStatus?: TaskStatus | "edited";
  parentProposalIssueKey?: string;
  // The meeting that first suggested it; revisions list the later ones, oldest first.
  originMeetingId?: string;
  revisions?: TaskRevision[];
  // Sprint a new Story, Task or Bug goes into on approval; unset = backlog.
  targetSprintId?: number;
};
export type Meeting = {
  id: string;
  title: string;
  startedAt: string;
  processingStatus?: "pending" | "processing" | "completed" | "failed";
  transcript: string;
  summary: string;
  tasks: Task[];
};
export type Screen = "dashboard" | "meeting" | "history" | "tasks";
