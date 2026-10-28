import type { Meeting } from "./types";
export function isMeetings(value: unknown): value is Meeting[] {
  return (
    Array.isArray(value) &&
    value.every(
      (m) =>
        m &&
        typeof m.id === "string" &&
        typeof m.title === "string" &&
        typeof m.startedAt === "string" &&
        typeof m.transcript === "string" &&
        typeof m.summary === "string" &&
        Array.isArray(m.tasks) &&
        m.tasks.every(
          (t: Record<string, unknown>) =>
            t &&
            typeof t.id === "string" &&
            typeof t.title === "string" &&
            typeof t.owner === "string" &&
            typeof t.priority === "string" &&
            ["pending", "approved", "rejected"].includes(String(t.status)),
        ),
    )
  );
}
