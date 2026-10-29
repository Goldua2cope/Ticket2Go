import { useId, useRef } from "react";
import { createPortal } from "react-dom";
import type { Meeting } from "../../lib/types";
import Icon from "../ui/Icon";

export default function DeleteMeetingButton({
  meeting,
  onDelete,
}: {
  meeting: Meeting;
  onDelete: (id: string) => void;
}) {
  const dialog = useRef<HTMLDialogElement>(null);
  const titleId = useId();
  const descriptionId = useId();
  return (
    <>
      <button
        type="button"
        className="icon-button meeting-delete-button"
        title="Delete from history"
        aria-label={`Delete ${meeting.title} from history`}
        onClick={() => dialog.current?.showModal()}>
        <Icon name="trash" size={19} />
      </button>
      {createPortal(
        <dialog
          ref={dialog}
          className="delete-meeting-dialog"
          aria-labelledby={titleId}
          aria-describedby={descriptionId}>
          <h2 id={titleId}>Delete from history?</h2>
          <p id={descriptionId}>
            Are you sure you want to delete <strong>“{meeting.title}”</strong>{" "}
            from your history?
          </p>
          {meeting.startedAt && (
            <p className="muted">
              {new Date(meeting.startedAt).toLocaleString()}
            </p>
          )}
          <p className="muted">
            This removes it from history in this browser. Its saved content
            stays intact, and you can restore it from Deleted meetings.
          </p>
          <div className="delete-meeting-dialog-actions">
            <button
              type="button"
              className="button secondary"
              autoFocus
              onClick={() => dialog.current?.close()}>
              Cancel
            </button>
            <button
              type="button"
              className="button danger"
              onClick={() => {
                dialog.current?.close();
                onDelete(meeting.id);
              }}>
              Delete from history
            </button>
          </div>
        </dialog>,
        document.body,
      )}
    </>
  );
}
