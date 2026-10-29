import type { Meeting } from "../../lib/types";
import Icon from "../ui/Icon";
import DeleteMeetingButton from "./DeleteMeetingButton";
export default function MeetingList({
  meetings,
  onSelect,
  onHide,
  onRestore,
}: {
  meetings: Meeting[];
  onSelect: (id: string) => void;
  onHide?: (id: string) => void;
  onRestore?: (id: string) => void;
}) {
  return (
    <div className="meeting-list">
      {meetings.map((meeting) => (
        <div className="meeting-list-entry" key={meeting.id}>
          <div className="meeting-row">
            <button
              type="button"
              className="meeting-row-open"
              onClick={() => onSelect(meeting.id)}>
              <span className="meeting-icon">
                <Icon name="mic" />
              </span>
              <span className="meeting-row-info">
                <strong>{meeting.title}</strong>
                <small>
                  {meeting.startedAt
                    ? new Date(meeting.startedAt).toLocaleString(undefined, {
                        month: "short",
                        day: "numeric",
                        hour: "2-digit",
                        minute: "2-digit",
                      })
                    : "Date not recorded"}{" "}
                  · {meeting.tasks.length} suggested tasks
                </small>
              </span>
              <Icon name="arrow" size={18} />
            </button>
            <div className="meeting-row-actions">
              <span className="badge neutral">
                {meeting.processingStatus || "completed"}
              </span>
              {onRestore ? (
                <button
                  type="button"
                  className="text-button"
                  onClick={() => onRestore(meeting.id)}
                  aria-label={`Restore ${meeting.title}`}>
                  Restore
                </button>
              ) : (
                onHide && (
                  <DeleteMeetingButton meeting={meeting} onDelete={onHide} />
                )
              )}
            </div>
          </div>
        </div>
      ))}
    </div>
  );
}
