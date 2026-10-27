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
};
export type Meeting = {
  id: string;
  title: string;
  startedAt: string;
  transcript: string;
  summary: string;
  tasks: Task[];
};
export type Screen = "dashboard" | "meeting" | "history" | "tasks";
