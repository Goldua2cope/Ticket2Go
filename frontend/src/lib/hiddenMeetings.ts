// Only visibility preferences live here; meeting content stays in the backend.
const KEY = "t2g-hidden-meetings-v1";

export function readHiddenMeetings(): string[] {
  try {
    const value: unknown = JSON.parse(localStorage.getItem(KEY) || "[]");
    return Array.isArray(value) ? [...new Set(value.filter((id): id is string => typeof id === "string" && id.length > 0))] : [];
  } catch {
    return [];
  }
}

export function saveHiddenMeetings(ids: string[]): void {
  localStorage.setItem(KEY, JSON.stringify([...new Set(ids)]));
}
