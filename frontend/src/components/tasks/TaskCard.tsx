import { useState } from "react";
import type { Task } from "../../lib/types";
import { PRIORITY_OPTIONS } from "../../lib/types";
import Icon from "../ui/Icon";
export default function TaskCard({
  task,
  onChange,
}: {
  task: Task;
  onChange: (task: Task) => void;
}) {
  const [editing, setEditing] = useState(false);
  const [draft, setDraft] = useState(task);
  function edit() {
    setDraft(task);
    setEditing(true);
  }
  return (
    <article className={`task-card task-${task.status}`}>
      <div className="row between">
        <span className={`badge ${task.status}`}>
          {task.status === "pending"
            ? "Needs review"
            : task.status === "approved"
              ? "Approved"
              : "Rejected"}
        </span>
        <span className="mini-label">SUGGESTED TASK</span>
      </div>
      {editing ? (
        <form
          onSubmit={(e) => {
            e.preventDefault();
            if (!draft.title.trim()) return;
            onChange({ ...draft, title: draft.title.trim() });
            setEditing(false);
          }}>
          <label>
            Task
            <textarea
              required
              value={draft.title}
              onChange={(e) => setDraft({ ...draft, title: e.target.value })}
            />
          </label>
          <label>
            Owner
            <input
              value={draft.owner}
              onChange={(e) => setDraft({ ...draft, owner: e.target.value })}
            />
          </label>
          <label>
            Priority
            <select
              value={draft.priority}
              onChange={(e) => {
                const priority = PRIORITY_OPTIONS.find(
                  (option) => option === e.target.value,
                );
                if (priority !== undefined) setDraft({ ...draft, priority });
              }}>
              {PRIORITY_OPTIONS.map((priority) => (
                <option key={priority} value={priority}>
                  {priority || "Select priority"}
                </option>
              ))}
            </select>
          </label>
          <div className="row">
            <button className="button primary small" type="submit">
              Save changes
            </button>
            <button
              className="button secondary small"
              type="button"
              onClick={() => setEditing(false)}>
              Cancel
            </button>
          </div>
        </form>
      ) : (
        <>
          <h3>{task.title}</h3>
          <div className="task-meta">
            <span>
              <small>OWNER</small>
              {task.owner || "Not specified"}
            </span>
            <span>
              <small>PRIORITY</small>
              {task.priority || "Not specified"}
            </span>
          </div>
          <div className="task-actions">
            <button className="text-button" onClick={edit}>
              <Icon name="edit" size={15} /> Edit
            </button>
            {task.status === "pending" ? (
              <div className="row">
                <button
                  className="button secondary small"
                  onClick={() => onChange({ ...task, status: "rejected" })}>
                  <Icon name="close" size={15} /> Reject
                </button>
                <button
                  className="button primary small"
                  onClick={() => onChange({ ...task, status: "approved" })}>
                  <Icon name="check" size={15} /> Approve
                </button>
              </div>
            ) : (
              <button
                className="text-button"
                onClick={() => onChange({ ...task, status: "pending" })}>
                Move back to review
              </button>
            )}
          </div>
          {task.status === "rejected" && (
            <p className="rejection-note">
              Not proceeding with this suggestion. You can review it again
              anytime.
            </p>
          )}
        </>
      )}
    </article>
  );
}
