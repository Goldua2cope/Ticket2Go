import type { Screen } from "./types";

const SESSION_KEY = "t2g-workspace-session-v1";
type WorkspaceSession = { loggedIn: boolean; screen: Screen; selectedId: string | null };
const defaultSession: WorkspaceSession = { loggedIn: false, screen: "dashboard", selectedId: null };

export function readWorkspaceSession(): WorkspaceSession {
  try {
    const saved = JSON.parse(sessionStorage.getItem(SESSION_KEY) || "null");
    if (!saved || saved.loggedIn !== true) return { ...defaultSession };
    const screen: Screen = ["dashboard", "meeting", "history", "tasks"].includes(saved.screen)
      ? saved.screen : "dashboard";
    return {
      loggedIn: true,
      screen,
      selectedId: screen === "history" && typeof saved.selectedId === "string" ? saved.selectedId : null,
    };
  } catch {
    return { ...defaultSession };
  }
}

export function saveWorkspaceSession(screen: Screen = "dashboard", selectedId: string | null = null) {
  try {
    sessionStorage.setItem(SESSION_KEY, JSON.stringify({ loggedIn: true, screen, selectedId }));
  } catch { /* The app remains usable when browser storage is unavailable. */ }
}

export function clearWorkspaceSession() {
  try {
    sessionStorage.removeItem(SESSION_KEY);
  } catch { /* Logout still clears the current React session. */ }
}
