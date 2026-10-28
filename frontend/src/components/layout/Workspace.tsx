import { useEffect, useRef, useState } from "react";
import { useRecorder } from "../../hooks/useRecorder";
import type { Meeting, Screen, Task, TaskAction } from "../../lib/types";
import AppShell from "./AppShell";
import RecordingBanner from "../meeting/RecordingBanner";
import LiveMeetingPanel from "../meeting/LiveMeetingPanel";
import DashboardPage from "../../pages/DashboardPage";
import MeetingPage from "../../pages/MeetingPage";
import HistoryPage from "../../pages/HistoryPage";
import TasksPage from "../../pages/TasksPage";
import { loadMeetings, syncSavedJiraData } from "../../lib/meetingApi";
import {
  mergeSuggestions,
  requestSuggestions,
  loadSuggestions,
  loadProposal,
  reviewTask,
} from "../../lib/taskApi";

export default function Workspace({
  dark,
  toggle,
  onLogout,
}: {
  dark: boolean;
  toggle: () => void;
  onLogout: () => void;
}) {
  const [screen, setScreen] = useState<Screen>("dashboard");
  const [selectedId, setSelectedId] = useState<string | null>(null);
  const [suggestionRuns, setSuggestionRuns] = useState<
    Record<string, { loading: boolean; message: string; retry?: boolean }>
  >({});
  const activeRuns = useRef(new Set<string>());
  const [meetings, setMeetings] = useState<Meeting[]>([]);
  const [historyLoading, setHistoryLoading] = useState(true);
  const [historyError, setHistoryError] = useState("");
  const [historyReload, setHistoryReload] = useState(0);
  const dataVersion = useRef(0);
  const [reviewError, setReviewError] = useState("");
  const reviewing = useRef(new Set<string>());
  useEffect(() => {
    const controller = new AbortController();
    let active = true;
    const timer = setTimeout(() => controller.abort(), 60_000);
    async function reload() {
      try {
        while (active) {
          const version = dataVersion.current;
          const saved = await loadMeetings(controller.signal);
          if (!active) return;
          // Do not overwrite an approval or newly finished meeting with an older read.
          if (version !== dataVersion.current) continue;
          // Retire the old cache only after successfully loading saved records.
          try {
            localStorage.removeItem("t2g-meetings-v1");
          } catch {
            /* Storage may be disabled. */
          }
          setMeetings((previous) =>
            saved.map((meeting) => {
              const session = previous.find((item) => item.id === meeting.id);
              return {
                ...meeting,
                summary: session?.summary || "",
                transcript: session?.transcript || "",
              };
            }),
          );
          break;
        }
      } catch (error) {
        if (active)
          setHistoryError(
            error instanceof Error && error.name !== "AbortError"
              ? error.message
              : "Loading saved meetings timed out. Please try again.",
          );
      } finally {
        clearTimeout(timer);
        if (active) setHistoryLoading(false);
      }
    }
    void reload();
    return () => {
      active = false;
      clearTimeout(timer);
      controller.abort();
    };
  }, [historyReload]);
  const refreshingHistory = useRef(false);
  async function refreshHistory() {
    if (refreshingHistory.current) return;
    refreshingHistory.current = true;
    setHistoryLoading(true);
    setHistoryError("");
    try {
      await syncSavedJiraData();
      setHistoryReload((version) => version + 1);
    } catch (error) {
      setHistoryError(error instanceof Error ? error.message : "Could not synchronize Jira.");
      setHistoryLoading(false);
    } finally {
      refreshingHistory.current = false;
    }
  }
  const historyVisible =
    meetings.length > 0 || (!historyLoading && !historyError);
  async function generateSuggestions(meetingId: string) {
    if (activeRuns.current.has(meetingId)) return;
    activeRuns.current.add(meetingId);
    setSuggestionRuns((previous) => ({
      ...previous,
      [meetingId]: { loading: true, message: "Generating task suggestions…" },
    }));
    try {
      const result = await requestSuggestions(meetingId);
      const saved = await loadSuggestions(meetingId);
      dataVersion.current += 1;
      setMeetings((previous) => mergeSuggestions(previous, saved));
      const partial = result.status.outcome === "partial";
      const revised = result.status.proposals_revised || 0;
      setSuggestionRuns((previous) => ({
        ...previous,
        [meetingId]: {
          loading: false,
          message: partial
            ? "Some suggestions could not be generated. Available results are shown."
            : revised > 0
              ? `Suggestions ready. ${revised} earlier suggestion(s) updated; view all results in Tasks.`
              : result.proposals.length
                ? "Task suggestions are ready to review."
                : "No task suggestions were found for this meeting.",
          retry: partial,
        },
      }));
    } catch (error) {
      setSuggestionRuns((previous) => ({
        ...previous,
        [meetingId]: {
          loading: false,
          message:
            error instanceof Error
              ? error.message
              : "Could not generate suggestions.",
          retry: true,
        },
      }));
    } finally {
      activeRuns.current.delete(meetingId);
    }
  }
  const recorder = useRecorder((meeting) => {
    dataVersion.current += 1;
    setMeetings((previous) => [
      meeting,
      ...previous.filter((m) => m.id !== meeting.id),
    ]);
    void generateSuggestions(meeting.id);
  });
  const busy = recorder.status !== "idle";
  const suggestionsPending = Object.values(suggestionRuns).some(
    (run) => run.loading,
  );
  useEffect(() => {
    if (!busy && !suggestionsPending) return;
    function guard(event: BeforeUnloadEvent) {
      event.preventDefault();
      event.returnValue = "";
    }
    window.addEventListener("beforeunload", guard);
    return () => window.removeEventListener("beforeunload", guard);
  }, [busy, suggestionsPending]);
  function navigate(next: Screen) {
    setScreen(next);
    setSelectedId(null);
  }
  function selectMeeting(id: string | null) {
    setSelectedId(id);
    setScreen("history");
  }
  async function updateTask(
    _meetingId: string,
    task: Task,
    action: TaskAction,
  ) {
    if (reviewing.current.has(task.id))
      throw new Error("This task is already being saved.");
    reviewing.current.add(task.id);
    const apply = (updated: Task) => {
      dataVersion.current += 1;
      setMeetings((previous) =>
        previous.map((meeting) => ({
          ...meeting,
          tasks: meeting.tasks.map((item) =>
            item.id === updated.id ? updated : item,
          ),
        })),
      );
    };
    try {
      const updated =
        action === "refresh"
          ? await loadProposal(task.id)
          : await reviewTask(task, action);
      apply(updated);
      setReviewError("");
    } catch (error) {
      // The backend may have committed approval even if the response was lost.
      try {
        apply(await loadProposal(task.id));
      } catch {
        /* Keep the error visible. */
      }
      throw error;
    } finally {
      reviewing.current.delete(task.id);
    }
  }
  return (
    <AppShell
      screen={screen}
      navigate={navigate}
      dark={dark}
      toggle={toggle}
      onLogout={onLogout}
      busy={busy}
      liveMeeting={
        <LiveMeetingPanel
          recorder={recorder}
          onOpenMeeting={() => navigate("meeting")}
        />
      }>
      <RecordingBanner recorder={recorder} onOpen={() => navigate("meeting")} />
      <div className="row between">
        <span role="status">
          {historyLoading
            ? "Loading saved meetings and suggestions…"
            : historyError
              ? "Saved data could not be refreshed."
              : "Meetings and suggestions loaded from your workspace."}
        </span>
        <button
          className="text-button"
          disabled={historyLoading}
          onClick={refreshHistory}>
          Refresh saved data
        </button>
      </div>
      {historyError && (
        <p className="error" role="alert">
          {historyError}
        </p>
      )}
      {reviewError && (
        <p className="error" role="alert">
          {reviewError}
        </p>
      )}
      {Object.entries(suggestionRuns).map(([meetingId, run]) => (
        <div
          key={meetingId}
          className={run.retry ? "error" : "subtle-note"}
          role="status"
          aria-live="polite">
          <strong>
            {meetings.find((meeting) => meeting.id === meetingId)?.title ||
              "Meeting"}
            :{" "}
          </strong>
          {run.message}{" "}
          {run.retry && (
            <button
              className="text-button"
              onClick={() => void generateSuggestions(meetingId)}>
              Retry suggestions
            </button>
          )}
          {!run.loading && (
            <button
              className="text-button"
              onClick={() => selectMeeting(meetingId)}>
              View meeting
            </button>
          )}
        </div>
      ))}
      {recorder.error && screen !== "meeting" && (
        <div className="error" role="alert">
          {recorder.error}{" "}
          <button className="text-button" onClick={() => navigate("meeting")}>
            Open meeting
          </button>
        </div>
      )}
      {screen === "dashboard" && historyVisible && (
        <DashboardPage
          meetings={meetings}
          navigate={navigate}
          onSelect={selectMeeting}
          busy={busy}
        />
      )}
      {screen === "meeting" && (
        <MeetingPage recorder={recorder} onReview={selectMeeting} />
      )}
      {screen === "history" && historyVisible && (
        <HistoryPage
          meetings={meetings}
          selectedId={selectedId}
          onSelect={selectMeeting}
          onTaskChange={updateTask}
          onStart={() => navigate("meeting")}
          suggestionsLoading={Boolean(
            selectedId && suggestionRuns[selectedId]?.loading,
          )}
        />
      )}
      {screen === "tasks" && historyVisible && (
        <TasksPage meetings={meetings} onTaskChange={updateTask} />
      )}
    </AppShell>
  );
}
