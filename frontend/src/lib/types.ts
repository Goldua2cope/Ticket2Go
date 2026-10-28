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
