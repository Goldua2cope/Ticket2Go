import { useEffect, useId, useRef } from "react";
import type { useRecorder } from "../../hooks/useRecorder";
import Icon from "../ui/Icon";

const labels = {
  idle: "Ready for a meeting",
  starting: "Connecting…",
  recording: "Recording live",
  stopping: "Finishing transcript…",
  saving: "Saving transcript…",
  summarizing: "Creating summary…",
};

export default function LiveMeetingPanel({
  recorder,
  onOpenMeeting,
}: {
  recorder: ReturnType<typeof useRecorder>;
  onOpenMeeting: () => void;
}) {
  const panelId = useId();
  const headingId = useId();
  const panelRef = useRef<HTMLDivElement>(null);
  const textRef = useRef<HTMLDivElement>(null);
  const followLatest = useRef(true);
  const { transcript, partial, status } = recorder;
  const text = [transcript, partial].filter(Boolean).join(" ");
  const label =
    status === "idle" && text ? "Meeting transcript" : labels[status];

  useEffect(() => {
    const element = textRef.current;
    if (element && followLatest.current)
      element.scrollTop = element.scrollHeight;
  }, [transcript, partial]);

  return (
    <div className="live-meeting">
      <button className="live-meeting-preview" popoverTarget={panelId}>
        <span className="row between">
          <strong>
            <Icon name="mic" size={16} /> Live meeting
          </strong>
          <Icon name="arrow" size={16} />
        </span>
        <span className="live-meeting-status">
          {status === "recording" && <span className="status-dot pulse" />}
          {label}
        </span>
        <span className="live-meeting-excerpt">
          {text.slice(-180) ||
            "Your live transcript will appear here when a meeting starts."}
        </span>
        <span className="live-meeting-link">Open transcript</span>
      </button>
      <div
        ref={panelRef}
        id={panelId}
        popover="auto"
        className="live-transcript-popover"
        role="region"
        aria-labelledby={headingId}
        onToggle={(event) => {
          if (event.newState === "open" && textRef.current) {
            followLatest.current = true;
            textRef.current.scrollTop = textRef.current.scrollHeight;
          }
        }}>
        <div className="live-transcript-heading">
          <div>
            <h2 id={headingId}>Live transcript</h2>
            <p className="muted" role="status">
              {label}
            </p>
          </div>
          <button
            className="icon-button"
            popoverTarget={panelId}
            popoverTargetAction="hide"
            aria-label="Close transcript">
            <Icon name="close" size={18} />
          </button>
        </div>
        <div
          ref={textRef}
          className="transcript-text live-transcript-text"
          tabIndex={0}
          aria-label="Meeting transcript"
          onScroll={(event) => {
            const element = event.currentTarget;
            followLatest.current =
              element.scrollHeight - element.scrollTop - element.clientHeight <
              40;
          }}>
          {transcript ||
            (!partial && (
              <span className="muted">
                Your conversation will appear here once your meeting starts.
              </span>
            ))}
          {transcript && partial ? " " : ""}
          <span className="muted">{partial}</span>
        </div>
        <div className="live-transcript-footer">
          <button
            className="text-button"
            onClick={() => {
              panelRef.current?.hidePopover();
              onOpenMeeting();
            }}>
            {status === "idle" ? "Open meeting" : "Meeting controls"}
            <Icon name="arrow" size={16} />
          </button>
        </div>
      </div>
    </div>
  );
}
