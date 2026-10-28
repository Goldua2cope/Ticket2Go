import { useRef, useState } from "react";
import type { Task, TaskAction } from "../../lib/types";
import { PRIORITY_OPTIONS } from "../../lib/types";
import Icon from "../ui/Icon";
export default function TaskCard({
  task,
  onChange,
}: {
  task: Task;
  onChange: (task: Task, action: TaskAction) => Promise<void>;
}) {
  const [editing, setEditing] = useState(false);
  const [draft, setDraft] = useState(task);
  const [saving, setSaving] = useState(false);
  const [error, setError] = useState("");
  const submitting = useRef(false);
  async function submit(updated: Task, action: TaskAction) {
    if (submitting.current) return;
    submitting.current = true;
    setSaving(true);
    setError("");
    try {
      await onChange(updated, action);
      setEditing(false);
    } catch (failure) {
      setError(
        failure instanceof Error
          ? failure.message
          : "Could not save the decision.",
      );
    } finally {
      submitting.current = false;
      setSaving(false);
    }
  }
  const pending = task.status === "pending";
  const jiraUrl =
    task.jiraUrl && /^https:\/\//i.test(task.jiraUrl)
      ? task.jiraUrl
      : undefined;
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
      {editing && pending ? (
        <form
          onSubmit={(e) => {
            e.preventDefault();
            if (!draft.title.trim()) return;
            void submit({ ...draft, title: draft.title.trim() }, "edit");
          }}>
          <label>
            Title
            <textarea
              required
              value={draft.title}
              onChange={(e) => setDraft({ ...draft, title: e.target.value })}
            />
          </label>
          <label>
            Description
            <textarea
              placeholder="Optional description"
              value={draft.description || ""}
              onChange={(e) =>
                setDraft({ ...draft, description: e.target.value })
              }
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
          <p className="muted">Assign the task in Jira after approval.</p>
          <div className="row">
            <button
              className="button primary small"
              type="submit"
              disabled={saving}>
              Save changes
            </button>
            <button
              className="button secondary small"
              type="button"
              disabled={saving}
              onClick={() => setEditing(false)}>
              Cancel
            </button>
          </div>
        </form>
      ) : (
        <>
          <h3>{task.title}</h3>
          {task.proposalType && (
            <p className="mini-label">
              {task.proposalType === "update"
                ? `Update ${task.existingIssueKey || "existing issue"}`
                : "Create new task"}
            </p>
          )}
          {task.description && (
            <p style={{ whiteSpace: "pre-wrap", overflowWrap: "anywhere" }}>
              {task.description
                .replace(/\[Ticket2Go update from item [^\]]+\]/g, "")
                .trim()}
            </p>
          )}
          {task.rationale && (
            <details>
              <summary>Why this was suggested</summary>
              <p>{task.rationale}</p>
            </details>
          )}
          <div className="task-meta mt-2">
            <span>
              <small>
                {pending && task.proposalType === "create"
                  ? "DESTINATION"
                  : "JIRA STATUS"}
              </small>
              {pending && task.proposalType === "create"
                ? "Active sprint (backlog if none)"
                : task.jiraStatus ||
                  (task.status === "rejected" && task.proposalType === "create"
                    ? "No Jira ticket"
                    : "Unknown")}
            </span>
            <span>
              <small>PRIORITY</small>
              {task.priority || "Not specified"}
            </span>
          </div>
          <div className="task-actions">
            {pending && (
              <button
                className="text-button"
                onClick={edit}
                disabled={saving || !task.reviewToken}>
                <Icon name="edit" size={15} /> Edit
              </button>
            )}
            {pending ? (
              <div className="row">
                <button
                  className="button secondary small"
                  disabled={saving || !task.reviewToken}
                  onClick={() => void submit(task, "reject")}>
                  <Icon name="close" size={15} /> Reject
                </button>
                <button
                  className="button primary small"
                  disabled={saving || !task.reviewToken}
                  onClick={() => void submit(task, "approve")}>
                  <Icon name="check" size={15} /> Approve
                </button>
              </div>
            ) : jiraUrl ? (
              <a
                className="button primary small"
                href={jiraUrl}
                target="_blank"
                rel="noopener noreferrer">
                Open in Jira <Icon name="arrow" size={15} />
              </a>
            ) : null}
          </div>
          {task.status === "rejected" && (
            <p className="rejection-note">
              Rejected. No changes were sent to Jira.
            </p>
          )}
          {task.status === "approved" &&
            task.executionStatus === "in_progress" && (
              <p role="status">
                Approval saved. Jira completion is not yet confirmed. Refresh
                status; if it remains unchanged, check Jira before taking
                further action.
              </p>
            )}
          {task.status === "approved" && task.executionStatus === "blocked" && (
            <p className="error">
              Approval saved, but Jira completion could not be confirmed. Check
              Jira before taking further action.
            </p>
          )}
          {pending && task.proposalType === "update" && (
            <p className="subtle-note">
              Approval updates the existing Jira issue's description and any
              selected priority. Its sprint assignment stays unchanged.
            </p>
          )}
        </>
      )}
      {task.placementMessage && (
        <p className="subtle-note" role="status">
          {task.placementMessage}
        </p>
      )}
      {saving && <p role="status">Saving…</p>}
      {error && (
        <p className="error" role="alert">
          {error}
        </p>
      )}
    </article>
  );
}
