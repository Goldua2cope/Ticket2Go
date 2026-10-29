import type { useRecorder } from "../../hooks/useRecorder";
export default function RecordingBanner({
  recorder,
  onOpen,
}: {
  recorder: ReturnType<typeof useRecorder>;
  onOpen: () => void;
}) {
  if (recorder.status === "idle") return null;
  return (
    <div className="recording-banner" role="status">
      <div>
        <span className="status-dot pulse" />
        <strong>
          {recorder.status === "recording"
            ? "Your meeting is recording"
            : "Meeting in progress"}
        </strong>
        <span className="banner-detail">
          {recorder.status === "recording"
            ? "Keep exploring. We're capturing the conversation."
            : "Connecting or processing your meeting…"}
        </span>
      </div>
      <div className="row">
        <button className="text-button" onClick={onOpen}>
          Open meeting →
        </button>
        {recorder.status === "recording" && (
          <button
            className="button danger small"
            onClick={recorder.stopRecording}>
            Stop
          </button>
        )}
      </div>
    </div>
  );
}
