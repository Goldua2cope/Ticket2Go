import { useState } from "react";
import type { Meeting, Task, TaskStatus, TaskAction } from "../lib/types";
import TaskCard from "../components/tasks/TaskCard";
import EmptyState from "../components/ui/EmptyState";
import { uniqueTasks } from "../lib/taskApi";
export default function TasksPage({
  meetings,
  onTaskChange,
}: {
  meetings: Meeting[];
  onTaskChange: (meetingId: string, task: Task, action: TaskAction) => Promise<void>;
}) {
  const [filter, setFilter] = useState<TaskStatus | "all">("all");
  // Each suggestion once, even when a later meeting revised it.
  const tasks = uniqueTasks(meetings);
  const filtered = tasks.filter(
    ({ task }) => filter === "all" || task.status === filter,
  );
  return (
    <>
      <div className="page-heading">
        <div>
          <span className="eyebrow">FROM CONVERSATION TO ACTION</span>
          <h1>Your next moves.</h1>
          <p className="muted">Review the suggestions. Make the plan yours.</p>
        </div> 
      </div>
      <div className="filter-tabs" aria-label="Filter tasks">
        {(["all", "pending", "approved", "rejected"] as const).map((status) => (
          <button
            key={status}
            className={filter === status ? "selected" : ""}
            aria-pressed={filter === status}
            onClick={() => setFilter(status)}>
            {status === "all"
              ? "All tasks"
              : status === "pending"
                ? "Needs review"
                : status === "approved"
                  ? "Approved"
                  : "Rejected"}
            <span>
              {
                tasks.filter(
                  ({ task }) => status === "all" || task.status === status,
                ).length
              }
            </span>
          </button>
        ))}
      </div>
      {filtered.length ? (
        <div className="task-grid">
          {filtered.map(({ task, meeting }) => (
            <div key={task.id}>
              <p className="task-source">{meeting.title}</p>
              <TaskCard
                task={task}
                meetingId={meeting.id}
                onChange={(updated, action) => onTaskChange(meeting.id, updated, action)}
              />
            </div>
          ))}
        </div>
      ) : (
        <EmptyState
          title={
            tasks.length
              ? "Nothing in this view"
              : "Make room for your next steps"
          }
          description={
            tasks.length
              ? "Choose another filter to see your task suggestions."
              : "Task suggestions appear here after a completed meeting has been analyzed."
          }
        />
      )}
      <p className="subtle-note">
        Decisions are saved to your workspace. Approval sends the task to Jira; further changes happen there.
      </p>
    </>
  );
}
