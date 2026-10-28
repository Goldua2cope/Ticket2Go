import type { Meeting, Screen } from "../lib/types";
import Icon from "../components/ui/Icon";
import EmptyState from "../components/ui/EmptyState";
import MeetingList from "../components/meeting/MeetingList";
export default function DashboardPage({
  meetings,
  navigate,
  onSelect,
  busy,
}: {
  meetings: Meeting[];
  navigate: (screen: Screen) => void;
  onSelect: (id: string) => void;
  busy: boolean;
}) {
  const tasks = meetings.flatMap((m) => m.tasks);
  return (
    <>
      <div className="page-heading">
        <div>
          <span className="eyebrow">A FRESH PERSPECTIVE</span>
          <h1>Let's move things forward.</h1>
          <p className="muted">
            Your conversations and next steps, all in one place.
          </p>
        </div>
        <span className="date-label">
          {new Date().toLocaleDateString(undefined, {
            weekday: "long",
            month: "short",
            day: "numeric",
          })}
        </span>
      </div>
      <section className="start-card">
        <div>
          <span className="eyebrow">GOOD IDEAS START HERE</span>
          <h2>
            A conversation today.
            <br />A clear plan for tomorrow.
          </h2>
          <p>Capture the details. Keep the decisions. Make your next move.</p>
          <button
            className="button primary"
            onClick={() => navigate("meeting")}>
            <Icon name="mic" size={18} />
            {busy ? "Return to meeting" : "Start a meeting"}
            <Icon name="arrow" size={17} />
          </button>
        </div>
        <div className="start-art" aria-hidden="true">
          <div className="orbit orbit-one" />
          <div className="orbit orbit-two" />
          <span className="art-center">
            <Icon name="mic" size={40} />
          </span>
          <span className="art-check">
            <Icon name="check" />
          </span>
          <span className="art-note">Ideas → action</span>
        </div>
      </section>
      <div className="stats-grid">
        {[
          {
            label: "Meetings captured",
            count: meetings.length,
            icon: "history" as const,
            screen: "history" as const,
          },
          {
            label: "Tasks to review",
            count: tasks.filter((t) => t.status === "pending").length,
            icon: "tasks" as const,
            screen: "tasks" as const,
          },
          {
            label: "Approved next steps",
            count: tasks.filter((t) => t.status === "approved").length,
            icon: "check" as const,
            screen: "tasks" as const,
          },
        ].map((stat) => (
          <button
            className="stat-card"
            key={stat.label}
            onClick={() => navigate(stat.screen)}>
            <div className="row between">
              <span>{stat.label}</span>
              <Icon name={stat.icon} />
            </div>
            <strong>{String(stat.count).padStart(2, "0")}</strong>
            <small>
              View details <Icon name="arrow" size={13} />
            </small>
          </button>
        ))}
      </div>
      <section className="panel">
        <div className="panel-heading">
          <h2>Recent meetings</h2>
          <button className="text-button" onClick={() => navigate("history")}>
            View all <Icon name="arrow" size={16} />
          </button>
        </div>
        {meetings.length ? (
          <MeetingList meetings={meetings.slice(0, 4)} onSelect={onSelect} />
        ) : (
          <EmptyState
            title="Your next meeting belongs here"
            description="Start your first meeting and we'll keep the transcript, summary, and next steps together."
            onStart={() => navigate("meeting")}
          />
        )}
      </section>
    </>
  );
}
