import type { ReactNode } from "react";
import type { Screen } from "../../lib/types";
import Brand from "../ui/Brand";
import Icon from "../ui/Icon";
import ThemeToggle from "../ui/ThemeToggle";
const items = [
  { id: "dashboard" as const, label: "Overview", icon: "grid" as const },
  { id: "meeting" as const, label: "Record a meeting", icon: "mic" as const },
  {
    id: "history" as const,
    label: "Meeting History",
    icon: "history" as const,
  },
  { id: "tasks" as const, label: "Tasks", icon: "tasks" as const },
];
export default function AppShell({
  screen,
  navigate,
  dark,
  toggle,
  onLogout,
  busy,
  liveMeeting,
  children,
}: {
  screen: Screen;
  navigate: (screen: Screen) => void;
  dark: boolean;
  toggle: () => void;
  onLogout: () => void;
  busy: boolean;
  liveMeeting: ReactNode;
  children: ReactNode;
}) {
  return (
    <div className="app-shell">
      <aside className="sidebar">
        <Brand />
        <div className="workspace-label">YOUR WORKSPACE</div>
        <nav aria-label="Main navigation">
          {items.map((item) => (
            <button
              key={item.id}
              className={`nav-item ${screen === item.id ? "active" : ""}`}
              aria-current={screen === item.id ? "page" : undefined}
              onClick={() => navigate(item.id)}>
              <Icon name={item.icon} />
              <span>{item.label}</span>
              {item.id === "meeting" && busy && (
                <span className="status-dot pulse" />
              )}
            </button>
          ))}
        </nav>
        <div className="sidebar-bottom">
          {liveMeeting}
          <div className="profile">
            <span className="avatar">D</span>
            <div>
              <strong>Demo workspace</strong>
              <small>Personal account</small>
            </div>
            <button
              className="icon-button"
              aria-label="Sign out"
              title={
                busy ? "Finish your meeting before signing out" : "Sign out"
              }
              disabled={busy}
              onClick={onLogout}>
              <Icon name="logout" size={18} />
            </button>
          </div>
        </div>
      </aside>
      <div className="workspace">
        <header className="app-header">
          <div>
            <span className="muted">Workspace</span>
            <span className="breadcrumb">/</span>
            <strong>{items.find((i) => i.id === screen)?.label}</strong>
          </div>
          <div className="row">
            <ThemeToggle dark={dark} toggle={toggle} />
            <button
              className="icon-button mobile-logout"
              disabled={busy}
              onClick={onLogout}
              aria-label="Sign out">
              <Icon name="logout" />
            </button>
          </div>
        </header>
        <main className="workspace-main">{children}</main>
      </div>
    </div>
  );
}
