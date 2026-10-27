import type { Task } from "../../lib/types";
import TaskCard from "./TaskCard";
import EmptyState from "../ui/EmptyState";
export default function TaskSuggestions({
  tasks,
  onChange,
}: {
  tasks: Task[];
  onChange: (task: Task) => void;
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
      {tasks.length ? (
        <div className="task-grid">
          {tasks.map((task) => (
            <TaskCard key={task.id} task={task} onChange={onChange} />
          ))}
        </div>
      ) : (
        <EmptyState
          title="No task suggestions yet"
          description="Tasks appear here after a meeting when its AI summary includes structured action items. You can also review the full summary for next steps."
        />
      )}
      <p className="subtle-note">
        Review AI suggestions for accuracy. Approvals are saved in this browser;
        they do not create Jira tickets.
      </p>
    </section>
  );
}
