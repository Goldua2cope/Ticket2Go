import { useState } from "react";
import type { Meeting, Task, TaskAction } from "../lib/types";
import DeleteMeetingButton from "../components/meeting/DeleteMeetingButton";
import MeetingList from "../components/meeting/MeetingList";
import SavedMeetingSummary from "../components/summary/SavedMeetingSummary";
import TaskSuggestions from "../components/tasks/TaskSuggestions";
import EmptyState from "../components/ui/EmptyState";
export default function HistoryPage({
  meetings,
  hiddenMeetingIds,
  onHide,
  onRestore,
  selectedId,
  onSelect,
  onTaskChange,
  onStart,
  suggestionsLoading = false,
}: {
  meetings: Meeting[];
  hiddenMeetingIds: string[];
  onHide: (id: string) => void;
  onRestore: (id: string) => void;
  selectedId: string | null;
  onSelect: (id: string | null) => void;
  onTaskChange: (
    meetingId: string,
    task: Task,
    action: TaskAction,
  ) => Promise<void>;
  onStart: () => void;
  suggestionsLoading?: boolean;
}) {
  const [showHidden, setShowHidden] = useState(false);
  const hiddenCount = meetings.filter((meeting) =>
    hiddenMeetingIds.includes(meeting.id),
  ).length;
  const [query, setQuery] = useState("");
  const selected = meetings.find((m) => m.id === selectedId);
  if (selected)
    return (
      <>
        <button
          className="text-button back-button"
          onClick={() => onSelect(null)}>
          ← All meetings
        </button>
        <div className="page-heading">
          <div>
            <span className="eyebrow">CONVERSATION CAPTURED</span>
            <h1>{selected.title}</h1>
            <p className="muted">
              {selected.startedAt
                ? new Date(selected.startedAt).toLocaleString()
                : "Date not recorded"}
            </p>
          </div>
          <div className="meeting-row-actions">
            <span className="badge neutral">
              {selected.processingStatus || "completed"}
            </span>
            {hiddenMeetingIds.includes(selected.id) ? (
              <button
                type="button"
                className="text-button"
                onClick={() => onRestore(selected.id)}>
                Restore to history
              </button>
            ) : (
              <DeleteMeetingButton meeting={selected} onDelete={onHide} />
            )}
          </div>
        </div>
        <SavedMeetingSummary key={selected.id} meeting={selected} />
        <TaskSuggestions
          tasks={selected.tasks}
          loading={suggestionsLoading}
          onChange={(task, action) => onTaskChange(selected.id, task, action)}
        />
      </>
    );
  const filtered = meetings
    .filter((meeting) => hiddenMeetingIds.includes(meeting.id) === showHidden)
    .filter((m) =>
      `${m.title} ${m.tasks.map((task) => task.title).join(" ")}`
        .toLowerCase()
        .includes(query.toLowerCase()),
    );
  return (
    <>
      <div className="page-heading">
        <div>
          <span className="eyebrow">NOTHING LOST IN THE CONVERSATION</span>
          <h1>Meeting history.</h1>
          <p className="muted">
            Revisit the details. Pick up where you left off.
          </p>
        </div>
      </div>
      <div className="meeting-visibility-note">
        <p className="muted">
          Meetings deleted from history stay saved and can be restored here.
          This applies only to this browser.
        </p>
        <button
          type="button"
          className="text-button"
          aria-pressed={showHidden}
          onClick={() => {
            setShowHidden(!showHidden);
            setQuery("");
          }}>
          {showHidden
            ? "Back to meeting history"
            : `Deleted meetings (${hiddenCount})`}
        </button>
      </div>
      <label className="search-field">
        Search meetings
        <input
          type="search"
          placeholder="Search meeting titles or suggested tasks…"
          value={query}
          onChange={(e) => setQuery(e.target.value)}
        />
      </label>
      <section className="panel">
        <div className="panel-heading">
          <h2>{showHidden ? "Deleted meetings" : "All meetings"}</h2>
          <span className="badge neutral">{filtered.length} meetings</span>
        </div>
        {filtered.length ? (
          <MeetingList
            meetings={filtered}
            onSelect={onSelect}
            onHide={showHidden ? undefined : onHide}
            onRestore={showHidden ? onRestore : undefined}
          />
        ) : (
          <EmptyState
            title={
              query
                ? "No matching meetings"
                : showHidden
                  ? "No deleted meetings"
                  : "A fresh start"
            }
            description={
              query
                ? "Try another word from the title or conversation."
                : showHidden
                  ? "Meetings you delete from history will appear here so you can restore them."
                  : "Your visible saved meetings will appear here."
            }
            onStart={query || showHidden ? undefined : onStart}
          />
        )}
      </section>
    </>
  );
}
