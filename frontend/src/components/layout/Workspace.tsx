import { useEffect, useState } from "react";
import { useRecorder } from "../../hooks/useRecorder";
import { useStoredState } from "../../hooks/useStoredState";
import { isMeetings } from "../../lib/storage";
import type { Meeting, Screen, Task } from "../../lib/types";
import AppShell from "./AppShell";
import RecordingBanner from "../meeting/RecordingBanner";
import LiveMeetingPanel from "../meeting/LiveMeetingPanel";
import DashboardPage from "../../pages/DashboardPage";
import MeetingPage from "../../pages/MeetingPage";
import HistoryPage from "../../pages/HistoryPage";
import TasksPage from "../../pages/TasksPage";

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
  const {
    value: meetings,
    update: setMeetings,
    warning,
  } = useStoredState<Meeting[]>("t2g-meetings-v1", [], isMeetings);
  const recorder = useRecorder((meeting) =>
    setMeetings((previous) => [
      meeting,
      ...previous.filter((m) => m.id !== meeting.id),
    ]),
  );
  const busy = recorder.status !== "idle";
  useEffect(() => {
    if (!busy) return;
    function guard(event: BeforeUnloadEvent) {
      event.preventDefault();
      event.returnValue = "";
    }
    window.addEventListener("beforeunload", guard);
    return () => window.removeEventListener("beforeunload", guard);
  }, [busy]);
  function navigate(next: Screen) {
    setScreen(next);
    setSelectedId(null);
  }
  function selectMeeting(id: string | null) {
    setSelectedId(id);
    setScreen("history");
  }
  function updateTask(meetingId: string, task: Task) {
    setMeetings((previous) =>
      previous.map((m) =>
        m.id === meetingId
          ? { ...m, tasks: m.tasks.map((t) => (t.id === task.id ? task : t)) }
          : m,
      ),
    );
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
        <LiveMeetingPanel recorder={recorder} onOpenMeeting={() => navigate("meeting")} />
      }>
      <RecordingBanner recorder={recorder} onOpen={() => navigate("meeting")} />
      {warning && (
        <p className="error" role="alert">
          {warning}
        </p>
      )}
      {recorder.error && screen !== "meeting" && (
        <div className="error" role="alert">
          {recorder.error}{" "}
          <button className="text-button" onClick={() => navigate("meeting")}>
            Open meeting
          </button>
        </div>
      )}
      {screen === "dashboard" && (
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
      {screen === "history" && (
        <HistoryPage
          meetings={meetings}
          selectedId={selectedId}
          onSelect={selectMeeting}
          onTaskChange={updateTask}
          onStart={() => navigate("meeting")}
        />
      )}
      {screen === "tasks" && (
        <TasksPage meetings={meetings} onTaskChange={updateTask} />
      )}
    </AppShell>
  );
}
