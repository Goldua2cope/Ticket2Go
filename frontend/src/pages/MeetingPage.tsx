import type { useRecorder } from "../hooks/useRecorder";
import Icon from "../components/ui/Icon";
const labels = {
  idle: "Ready when you are",
  starting: "Connecting your microphone…",
  recording: "Recording your conversation",
  stopping: "Finishing the transcript…",
  saving: "Saving your transcript…",
  summarizing: "Putting your summary together…",
};
export default function MeetingPage({
  recorder,
  onReview,
}: {
  recorder: ReturnType<typeof useRecorder>;
  onReview: (id: string) => void;
}) {
  return (
    <>
      <div className="page-heading">
        <div>
          <span className="eyebrow">STAY IN THE MOMENT</span>
          <h1>Your meeting space.</h1>
          <p className="muted">
            We'll capture the conversation. You bring the ideas.
          </p>
        </div>
      </div>
      <section className="panel recording-panel">
        <div
          className={`recorder-orb ${recorder.status === "recording" ? "is-recording" : ""}`}>
          <Icon name="mic" size={35} />
        </div>
        <h2 role="status">{labels[recorder.status]}</h2>
        <p className="muted">
          {recorder.status === "recording"
            ? "You can visit History or Tasks while this meeting keeps recording."
            : "Use your microphone to capture a live transcript and generate a summary."}
        </p>
        <div className="row centered">
          <button
            className="button primary"
            onClick={recorder.startRecording}
            disabled={recorder.status !== "idle"}>
            <Icon name="mic" size={18} />
            Start meeting
          </button>
          <button
            className="button secondary"
            onClick={recorder.stopRecording}
            disabled={recorder.status !== "recording"}>
            <span className="stop-square" />
            Finish meeting
          </button>
        </div>
        <p className="subtle-note">
          Keep this tab open while recording. Live capture requires your
          transcription backend.
        </p>
        {recorder.error && (
          <p className="error" role="alert">
            {recorder.error}
          </p>
        )}
        {recorder.summary && (
          <button
            className="button secondary"
            onClick={() => onReview(recorder.savedMeetingId)}>
            View completed meeting & tasks <Icon name="arrow" size={17} />
          </button>
        )}
      </section>
    </>
  );
}
