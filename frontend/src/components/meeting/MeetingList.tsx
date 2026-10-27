import type { Meeting } from "../../lib/types";
import Icon from "../ui/Icon";
export default function MeetingList({
  meetings,
  onSelect,
}: {
  meetings: Meeting[];
  onSelect: (id: string) => void;
}) {
  return (
    <div className="meeting-list">
      {meetings.map((meeting) => (
        <button
          className="meeting-row"
          onClick={() => onSelect(meeting.id)}
          key={meeting.id}>
          <span className="meeting-icon">
            <Icon name="mic" />
          </span>
          <span className="meeting-row-info">
            <strong>{meeting.title}</strong>
            <small>
              {new Date(meeting.startedAt).toLocaleString(undefined, {
                month: "short",
                day: "numeric",
                hour: "2-digit",
                minute: "2-digit",
              })}{" "}
              · {meeting.tasks.length} suggested tasks
            </small>
          </span>
          <span className="badge approved">Completed</span>
          <Icon name="arrow" size={18} />
        </button>
      ))}
    </div>
  );
}
