import type { Meeting } from "./types";
import { proposalToTask } from "./taskApi";
import type { Proposal } from "./taskApi";

type SavedMeeting = {
  meeting_id: string;
  title: string;
  meeting_date: string | null;
  processing_status: Meeting["processingStatus"];
  proposals: Proposal[];
};

export async function loadMeetings(signal?: AbortSignal): Promise<Meeting[]> {
  const meetings = new Map<string, Meeting>();
  let offset: number | null = 0;
  while (offset !== null) {
    const response = await fetch(`/meetings?limit=100&offset=${offset}`, { signal });
    if (!response.ok) throw new Error("Could not load saved meetings. Please try again.");
    const data: { meetings: SavedMeeting[]; next_offset: number | null } = await response.json();
    if (!Array.isArray(data.meetings) || (data.next_offset !== null &&
        (!Number.isInteger(data.next_offset) || data.next_offset <= offset))) {
      throw new Error("The server returned an unexpected meeting history response.");
    }
    for (const saved of data.meetings) {
      meetings.set(saved.meeting_id, {
        id: saved.meeting_id, title: saved.title, startedAt: saved.meeting_date || "",
        processingStatus: saved.processing_status, transcript: "", summary: "",
        tasks: saved.proposals.map(proposalToTask),
      });
    }
    offset = data.next_offset;
  }
  return [...meetings.values()];
}

export async function loadMeetingSummary(meetingId: string, signal?: AbortSignal): Promise<string | null> {
  const response = await fetch(`/meetings/${encodeURIComponent(meetingId)}/summary`, { signal });
  if (!response.ok) throw new Error("Could not load the saved summary. Your suggestions are still available.");
  const data = await response.json();
  return data.summary;
}


export async function syncSavedJiraData(): Promise<void> {
  const response = await fetch("/meetings/refresh", {
    method: "POST",
    signal: AbortSignal.timeout(330_000),
  });
  if (!response.ok) {
    throw new Error("Could not synchronize Jira. Please try refreshing saved data again.");
  }
}
