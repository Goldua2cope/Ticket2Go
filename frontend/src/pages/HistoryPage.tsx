import { useState } from "react";
import type { Meeting, Task, TaskAction } from "../lib/types";
import MeetingList from "../components/meeting/MeetingList";
import SavedMeetingSummary from "../components/summary/SavedMeetingSummary";
import TaskSuggestions from "../components/tasks/TaskSuggestions";
import EmptyState from "../components/ui/EmptyState";
export default function HistoryPage({
  meetings,
  selectedId,
  onSelect,
  onTaskChange,
  onStart,
  suggestionsLoading = false,
}: {
  meetings: Meeting[];
  selectedId: string | null;
  onSelect: (id: string | null) => void;
  onTaskChange: (meetingId: string, task: Task, action: TaskAction) => Promise<void>;
  onStart: () => void;
  suggestionsLoading?: boolean;
}) {
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
              {selected.startedAt ? new Date(selected.startedAt).toLocaleString() : "Date not recorded"}
            </p>
          </div>
          <span className="badge neutral">{selected.processingStatus || "completed"}</span>
        </div>
        <SavedMeetingSummary key={selected.id} meeting={selected} />
        <TaskSuggestions
          tasks={selected.tasks}
          loading={suggestionsLoading}
          onChange={(task, action) => onTaskChange(selected.id, task, action)}
        />
      </>
    );
  const filtered = meetings.filter((m) =>
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
          <h2>All meetings</h2>
          <span className="badge neutral">{filtered.length} meetings</span>
        </div>
        {filtered.length ? (
          <MeetingList meetings={filtered} onSelect={onSelect} />
        ) : (
          <EmptyState
            title={query ? "No matching meetings" : "A fresh start"}
            description={
              query
                ? "Try another word from the title or conversation."
                : "Your saved meetings will appear here from the database."
            }
            onStart={query ? undefined : onStart}
          />
        )}
      </section>
    </>
  );
}
