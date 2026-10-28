import { useEffect, useState } from "react";
import type { Meeting } from "../../lib/types";
import { loadMeetingSummary } from "../../lib/meetingApi";
import MeetingSummary from "./MeetingSummary";

export default function SavedMeetingSummary({ meeting }: { meeting: Meeting }) {
  const [summary, setSummary] = useState<string | null>(meeting.summary || null);
  const [loading, setLoading] = useState(!meeting.summary);
  const [error, setError] = useState("");
  const [attempt, setAttempt] = useState(0);
  useEffect(() => {
    if (meeting.summary) return;
    const controller = new AbortController();
    let active = true;
    const timer = setTimeout(() => controller.abort(), 20_000);
    loadMeetingSummary(meeting.id, controller.signal).then((text) => {
      if (active) setSummary(text);
    }).catch(() => {
      if (active) setError("Could not load the saved summary. Your suggestions are still available.");
    }).finally(() => {
      clearTimeout(timer);
      if (active) setLoading(false);
    });
    return () => { active = false; clearTimeout(timer); controller.abort(); };
  }, [meeting.id, meeting.summary, attempt]);
  if (meeting.summary || summary) return <MeetingSummary summary={meeting.summary || summary || ""} />;
  return <section className="panel">
    <h2>Meeting summary</h2>
    {loading ? <p role="status">Loading saved summary…</p> : error ? <>
      <p className="error" role="alert">{error}</p>
      <button className="text-button" onClick={() => { setError(""); setLoading(true); setAttempt((value) => value + 1); }}>Retry summary</button>
    </> : <p>No saved summary is available for this meeting.</p>}
  </section>;
}
