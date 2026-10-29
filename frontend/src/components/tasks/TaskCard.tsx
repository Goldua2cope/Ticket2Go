import { useEffect, useRef, useState } from "react";
import type {
  ParentOption,
  SprintOption,
  Task,
  TaskAction,
} from "../../lib/types";
import { ISSUE_TYPE_OPTIONS, PRIORITY_OPTIONS } from "../../lib/types";
import {
  isSuggestionRef,
  loadParentSprint,
  loadSprintOptions,
  loadSubtaskParents,
  placesInSprint,
} from "../../lib/taskApi";
import Icon from "../ui/Icon";
export default function TaskCard({
  task,
  onChange,
  meetingId,
}: {
  task: Task;
  onChange: (task: Task, action: TaskAction) => Promise<void>;
  // The meeting the card is shown under, used to describe revisions.
  meetingId?: string;
}) {
  const [editing, setEditing] = useState(false);
  const [draft, setDraft] = useState(task);
  const [saving, setSaving] = useState(false);
  const [error, setError] = useState("");
  const submitting = useRef(false);
  const [parents, setParents] = useState<ParentOption[] | null>(null);
  const [parentsError, setParentsError] = useState("");
  const isCreate = task.proposalType === "create";
  const needsParents = editing && isCreate && draft.issueType === "Subtask";
  // Load the parent options once, the first time Subtask is chosen.
  useEffect(() => {
    if (!needsParents || parents !== null) return;
    loadSubtaskParents(task.id)
      .then(setParents)
      .catch(() =>
        setParentsError("Could not load stories, tasks and bugs from Jira."),
      );
  }, [needsParents, parents, task.id]);
  const selectedParent = parents?.find(
    (parent) => parent.issue_key === draft.parentIssueKey,
  );
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
  const parentBlocker = pending ? subtaskBlocker(task) : "";
  const revisedNote = revisionNote(task, meetingId);
  // The sprint list is needed while editing a Story, Task or Bug, and to show
  // the name of a chosen sprint on the card.
  const draftPlacesInSprint = editing && placesInSprint(draft);
  const needsSprints =
    pending &&
    (draftPlacesInSprint ||
      (placesInSprint(task) && task.targetSprintId !== undefined));
  const [sprints, setSprints] = useState<SprintOption[] | null>(null);
  useEffect(() => {
    if (!needsSprints || sprints !== null) return;
    let cancelled = false;
    loadSprintOptions()
      .then((options) => {
        if (!cancelled) setSprints(options);
      })
      .catch(() => {
        if (!cancelled) setSprints([]); // "Backlog" still works without the list
      });
    return () => {
      cancelled = true;
    };
  }, [needsSprints, sprints]);
  // A subtask joins its parent's sprint; while editing, show which one.
  const parentKeyForSprint =
    editing &&
    isCreate &&
    draft.issueType === "Subtask" &&
    draft.parentIssueKey &&
    !isSuggestionRef(draft.parentIssueKey)
      ? draft.parentIssueKey
      : null;
  // sprint: null = the parent is in the backlog; undefined = couldn't load.
  const [parentSprint, setParentSprint] = useState<{
    key: string;
    sprint: SprintOption | null | undefined;
  } | null>(null);
  useEffect(() => {
    if (!parentKeyForSprint || parentSprint?.key === parentKeyForSprint) return;
    let cancelled = false;
    loadParentSprint(parentKeyForSprint)
      .then((sprint) => {
        if (!cancelled) setParentSprint({ key: parentKeyForSprint, sprint });
      })
      .catch(() => {
        if (!cancelled)
          setParentSprint({ key: parentKeyForSprint, sprint: undefined });
      });
    return () => {
      cancelled = true;
    };
  }, [parentKeyForSprint, parentSprint]);
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
          {isCreate && (
            <label>
              Work type
              <select
                value={draft.issueType || "Task"}
                onChange={(e) => {
                  const issueType = ISSUE_TYPE_OPTIONS.find(
                    (option) => option === e.target.value,
                  );
                  if (issueType === undefined) return;
                  const next = { ...draft, issueType };
                  // Only a subtask has a parent, and only a Story, Task or
                  // Bug has its own sprint.
                  if (issueType !== "Subtask") next.parentIssueKey = undefined;
                  if (!placesInSprint(next)) next.targetSprintId = undefined;
                  setDraft(next);
                }}>
                {ISSUE_TYPE_OPTIONS.map((issueType) => (
                  <option key={issueType} value={issueType}>
                    {issueType}
                  </option>
                ))}
              </select>
            </label>
          )}
          {isCreate && draft.issueType === "Subtask" && (
            <label>
              Parent story, task or bug
              <select
                required
                value={draft.parentIssueKey || ""}
                onChange={(e) =>
                  setDraft({
                    ...draft,
                    parentIssueKey: e.target.value || undefined,
                  })
                }>
                <option value="">Select a parent</option>
                {isSuggestionRef(task.parentIssueKey) && (
                  <option value={task.parentIssueKey}>
                    New (suggestion,{" "}
                    {task.parentProposalStatus === "approved"
                      ? "approved"
                      : "needs approval"}
                    ): {task.parentProposalSummary}
                  </option>
                )}
                {(parents || []).map((parent) => (
                  <option key={parent.issue_key} value={parent.issue_key}>
                    {parent.issue_key} · {parent.issue_type} · {parent.summary}
                    {parent.is_done ? ` (${parent.status})` : ""}
                  </option>
                ))}
              </select>
            </label>
          )}
          {needsParents && parentsError && (
            <p className="error">{parentsError}</p>
          )}
          {needsParents && selectedParent?.is_done && (
            <p className="subtle-note" role="status">
              <strong>Warning:</strong> {selectedParent.issue_key} is already{" "}
              {selectedParent.status}. Check that it's the right parent.
            </p>
          )}
          {draftPlacesInSprint && (
            <label>
              Destination
              <select
                value={draft.targetSprintId ?? ""}
                onChange={(e) =>
                  setDraft({
                    ...draft,
                    targetSprintId: e.target.value
                      ? Number(e.target.value)
                      : undefined,
                  })
                }>
                <option value="">Backlog</option>
                {(sprints || []).map((sprint) => (
                  <option key={sprint.id} value={sprint.id}>
                    {sprintLabel(sprint)}
                  </option>
                ))}
                {draft.targetSprintId !== undefined &&
                  sprints !== null &&
                  !sprints.some((sprint) => sprint.id === draft.targetSprintId) && (
                    <option value={draft.targetSprintId}>
                      Sprint {draft.targetSprintId} (closed or not found)
                    </option>
                  )}
              </select>
            </label>
          )}
          {editing && isCreate && draft.issueType === "Subtask" && draft.parentIssueKey && (
            <p className="muted">
              Destination: {subtaskDestination(draft.parentIssueKey, parentSprint)}
            </p>
          )}
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
                : `Create new ${(task.issueType || "Task").toLowerCase()}${
                    parentLabel(task) ? ` under ${parentLabel(task)}` : ""
                  }`}
            </p>
          )}
          {revisedNote && <p className="subtle-note">{revisedNote}</p>}
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
                ? destinationLabel(task, sprints)
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
                  disabled={saving || !task.reviewToken || Boolean(parentBlocker)}
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
          {parentBlocker && (
            <p className="subtle-note" role="status">
              {parentBlocker}
            </p>
          )}
          {pending && task.issueType === "Subtask" && task.parentIssueDone && (
            <p className="subtle-note" role="status">
              <strong>Warning:</strong> {task.parentIssueKey} is already{" "}
              {task.parentIssueStatus}. Check that it's the right parent before
              approving.
            </p>
          )}
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

function parentLabel(task: Task) {
  if (task.issueType !== "Subtask" || !task.parentIssueKey) return "";
  if (!isSuggestionRef(task.parentIssueKey)) return task.parentIssueKey;
  return task.parentProposalSummary
    ? `"${task.parentProposalSummary}" (suggestion)`
    : "a suggestion";
}

// Names only the latest meeting that revised the suggestion; the changes
// themselves are already in the description.
function revisionNote(task: Task, meetingId?: string) {
  const revisions = task.revisions || [];
  const latest = revisions[revisions.length - 1];
  if (!latest) return "";
  return latest.meeting_id === meetingId
    ? "Revised in this meeting"
    : `Revised in ${latest.meeting_title}`;
}

// Where a pending new issue will go when approved.
function destinationLabel(task: Task, sprints: SprintOption[] | null) {
  if (task.issueType === "Subtask") return "Follows its parent's sprint";
  if (task.issueType === "Epic") return "Not placed in a sprint";
  if (task.targetSprintId === undefined) return "Backlog";
  const sprint = sprints?.find((option) => option.id === task.targetSprintId);
  return sprint ? sprintLabel(sprint) : `Sprint ${task.targetSprintId}`;
}

function sprintLabel(sprint: SprintOption) {
  if (sprint.state === "active") return `${sprint.name} (active)`;
  return sprint.start_date
    ? `${sprint.name} (starts ${new Date(sprint.start_date).toLocaleDateString()})`
    : `${sprint.name} (future)`;
}

// A subtask always joins its parent's sprint, or the backlog if the parent has none.
function subtaskDestination(
  parentKey: string,
  loaded: { key: string; sprint: SprintOption | null | undefined } | null,
) {
  if (isSuggestionRef(parentKey))
    return "the parent suggestion's sprint, once it is created";
  if (loaded?.key !== parentKey) return `checking ${parentKey}'s sprint…`;
  if (loaded.sprint === undefined) return `${parentKey}'s sprint`;
  return loaded.sprint
    ? `${loaded.sprint.name} (same as ${parentKey})`
    : `Backlog (same as ${parentKey})`;
}

// Why a subtask can't be approved yet, or "" when it can.
function subtaskBlocker(task: Task) {
  if (task.proposalType !== "create" || task.issueType !== "Subtask") return "";
  if (!task.parentIssueKey)
    return "Edit this subtask to choose a parent story, task or bug.";
  if (isSuggestionRef(task.parentIssueKey) && !task.parentProposalIssueKey)
    return `Approve the parent suggestion "${task.parentProposalSummary || "unknown"}" first, or edit this subtask to choose an existing story, task or bug.`;
  return "";
}
