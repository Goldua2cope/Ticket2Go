import { useState } from "react";
import type { Meeting, Task } from "../lib/types";
import MeetingList from "../components/meeting/MeetingList";
import MeetingSummary from "../components/summary/MeetingSummary";
import TaskSuggestions from "../components/tasks/TaskSuggestions";
import EmptyState from "../components/ui/EmptyState";
export default function HistoryPage({
  meetings,
  selectedId,
  onSelect,
  onTaskChange,
  onStart,
}: {
  meetings: Meeting[];
  selectedId: string | null;
  onSelect: (id: string | null) => void;
  onTaskChange: (meetingId: string, task: Task) => void;
  onStart: () => void;
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
              {new Date(selected.startedAt).toLocaleString()}
            </p>
          </div>
          <span className="badge approved">Completed</span>
        </div>
        <MeetingSummary summary={selected.summary} />
        <TaskSuggestions
          tasks={selected.tasks}
          onChange={(task) => onTaskChange(selected.id, task)}
        />
      </>
    );
  const filtered = meetings.filter((m) =>
    `${m.title} ${m.transcript} ${m.summary}`
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
          placeholder="Search titles, transcripts, or summaries…"
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
                : "Your completed meetings will appear here, saved on this browser."
            }
            onStart={query ? undefined : onStart}
          />
        )}
      </section>
    </>
  );
}
