import type { Task, TaskAction } from "../../lib/types";
import TaskCard from "./TaskCard";
import EmptyState from "../ui/EmptyState";
export default function TaskSuggestions({
  tasks,
  onChange,
  loading = false,
  meetingId,
}: {
  tasks: Task[];
  onChange: (task: Task, action: TaskAction) => Promise<void>;
  loading?: boolean;
  meetingId?: string;
}) {
  return (
    <section className="suggestions">
      <div className="section-title">
        <div>
          <h2>Task suggestions</h2>
          <p className="muted">
            Your next steps, with you in control. Edit, approve, or reject.
          </p>
        </div>
        <span className="badge neutral">
          {tasks.filter((task) => task.status === "pending").length} to review
        </span>
      </div>
      {loading && <p role="status">Generating task suggestions… Your summary is ready above.</p>}
      {tasks.length ? (
        <div className="task-grid">
          {tasks.map((task) => (
            <TaskCard
              key={task.id}
              task={task}
              onChange={onChange}
              meetingId={meetingId}
            />
          ))}
        </div>
      ) : !loading ? (
        <EmptyState
          title="No task suggestions yet"
          description="No task suggestions are available for this meeting. Check the generation status above, or review the summary for next steps."
        />
      ) : null}
      <p className="subtle-note">
        Review before approving. Approval sends the task to Jira. Decisions are final; further changes happen in Jira.
      </p>
    </section>
  );
}
