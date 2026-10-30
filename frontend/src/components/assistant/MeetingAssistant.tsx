import { useEffect, useId, useRef, useState } from "react";
import Markdown from "react-markdown";
import Icon from "../ui/Icon";
import { streamAssistant } from "../../lib/assistantApi";
import type { ChatMessage, ContextSource } from "../../lib/assistantApi";

type Turn = {
  question: string;
  answer: string;
  sources: ContextSource[];
  complete: boolean;
};

export default function MeetingAssistant({
  onOpenMeeting,
}: {
  onOpenMeeting: (id: string) => void;
}) {
  const [open, setOpen] = useState(false);
  const [draft, setDraft] = useState("");
  const [turns, setTurns] = useState<Turn[]>([]);
  const [loading, setLoading] = useState(false);
  const [status, setStatus] = useState("");
  const [error, setError] = useState("");
  const request = useRef<AbortController | null>(null);
  const launcher = useRef<HTMLButtonElement>(null);
  const input = useRef<HTMLTextAreaElement>(null);
  const messages = useRef<HTMLDivElement>(null);
  const follow = useRef(true);
  const panelId = useId();
  const headingId = useId();

  useEffect(() => () => request.current?.abort(), []);
  useEffect(() => {
    if (open) input.current?.focus();
  }, [open]);
  useEffect(() => {
    if (open && follow.current && messages.current)
      messages.current.scrollTop = messages.current.scrollHeight;
  }, [turns, status, open, error]);

  function close() {
    setOpen(false);
    launcher.current?.focus();
  }
  async function send(question: string, previous = turns) {
    if (request.current || !question.trim()) return;
    const controller = new AbortController();
    request.current = controller;
    const timer = setTimeout(() => controller.abort("timeout"), 300_000);
    const history: ChatMessage[] = previous
      .filter((turn) => turn.complete)
      .slice(-10)
      .flatMap((turn) => [
        { role: "user" as const, content: turn.question },
        { role: "assistant" as const, content: turn.answer.slice(0, 6000) },
      ]);
    history.push({ role: "user", content: question.trim() });
    setTurns([
      ...previous,
      { question: question.trim(), answer: "", sources: [], complete: false },
    ]);
    setDraft("");
    setError("");
    setLoading(true);
    setStatus("Connecting to your meeting assistant…");
    follow.current = true;
    const update = (change: (turn: Turn) => Turn) =>
      setTurns((current) =>
        current.map((turn, index) =>
          index === current.length - 1 ? change(turn) : turn,
        ),
      );
    try {
      await streamAssistant(history, controller.signal, (event) => {
        if (event.type === "delta")
          update((turn) => ({ ...turn, answer: turn.answer + event.text }));
        if (event.type === "status") setStatus(event.message);
        if (event.type === "context_sources")
          update((turn) => ({ ...turn, sources: event.sources }));
        if (event.type === "done")
          update((turn) => ({ ...turn, complete: true }));
      });
    } catch (failure) {
      setError(
        controller.signal.aborted
          ? controller.signal.reason === "timeout"
            ? "The assistant timed out. Please retry."
            : "Response stopped. Any text above may be incomplete."
          : failure instanceof Error
            ? failure.message
            : "Could not complete your request.",
      );
    } finally {
      clearTimeout(timer);
      request.current = null;
      setLoading(false);
      setStatus("");
    }
  }

  return (
    <div className="meeting-assistant">
      {open && (
        <section
          id={panelId}
          className="assistant-panel"
          role="dialog"
          aria-labelledby={headingId}
          onKeyDown={(event) => {
            if (event.key === "Escape") {
              event.stopPropagation();
              close();
            }
          }}>
          <header className="assistant-heading">
            <span className="assistant-avatar">
              <Icon name="bot" size={24} />
            </span>
            <div>
              <h2 id={headingId}>Meeting assistant</h2>
              <p>Meetings, decisions & Jira</p>
            </div>
            <button
              type="button"
              className="icon-button"
              onClick={close}
              aria-label="Close meeting assistant">
              <Icon name="close" />
            </button>
          </header>
          <div
            className="assistant-messages"
            ref={messages}
            onScroll={(event) => {
              const element = event.currentTarget;
              follow.current =
                element.scrollHeight -
                  element.scrollTop -
                  element.clientHeight <
                60;
            }}>
            {!turns.length && (
              <div className="assistant-welcome">
                <h3>What would you like to catch up on?</h3>
                <p>
                  Ask about saved meetings, decisions, or Jira tickets. I can
                  look things up, but won’t change your tickets.
                </p>
                {[
                  "What were the key decisions in our latest meeting?",
                  "Which open Jira tickets need attention?",

                  "What did we discuss last meeting? Are there relevant tickets for that?",
                ].map((question) => (
                  <button
                    type="button"
                    key={question}
                    onClick={() => void send(question)}>
                    {question}
                    <Icon name="arrow" size={16} />
                  </button>
                ))}
              </div>
            )}
            {turns.map((turn, index) => (
              <div className="assistant-turn" key={index}>
                <div className="assistant-question">
                  <span className="sr-only">You: </span>
                  {turn.question}
                </div>
                {turn.answer && (
                  <div className="assistant-answer">
                    <span className="sr-only">Assistant: </span>
                    <Markdown
                      components={{
                        a: ({ href, children }) =>
                          href && /^https?:\/\//i.test(href) ? (
                            <a
                              href={href}
                              target="_blank"
                              rel="noopener noreferrer">
                              {children}
                            </a>
                          ) : (
                            <span>{children}</span>
                          ),
                      }}>
                      {turn.answer}
                    </Markdown>
                  </div>
                )}
                {turn.sources.length > 0 && (
                  <details className="assistant-sources">
                    <summary>Records retrieved ({turn.sources.length})</summary>
                    <ul>
                      {turn.sources.map((source) => (
                        <li key={source.source_id}>
                          {source.source_type === "meeting" &&
                          source.meeting_id ? (
                            <button
                              type="button"
                              className="text-button"
                              onClick={() => {
                                onOpenMeeting(source.meeting_id!);
                                close();
                              }}>
                              {source.meeting_title || "Meeting"}
                              {source.meeting_date
                                ? ` · ${source.meeting_date.slice(0, 10)}`
                                : ""}
                            </button>
                          ) : source.url && /^https:\/\//i.test(source.url) ? (
                            <a
                              href={source.url}
                              target="_blank"
                              rel="noopener noreferrer">
                              {source.issue_key || "Jira issue"}
                              {source.summary ? ` · ${source.summary}` : ""}
                            </a>
                          ) : (
                            <span>
                              {source.issue_key ||
                                source.meeting_title ||
                                "Retrieved record"}
                            </span>
                          )}
                        </li>
                      ))}
                    </ul>
                  </details>
                )}
              </div>
            ))}
            {loading && (
              <p className="assistant-status" role="status">
                {status || "Writing an answer…"}
              </p>
            )}
            {error && (
              <div className="assistant-error" role="alert">
                <p>{error}</p>
                <button
                  type="button"
                  className="text-button"
                  disabled={loading}
                  onClick={() => {
                    const last = turns.at(-1);
                    if (last) void send(last.question, turns.slice(0, -1));
                  }}>
                  Retry answer
                </button>
              </div>
            )}
          </div>
          <form
            className="assistant-composer"
            onSubmit={(event) => {
              event.preventDefault();
              void send(draft);
            }}>
            <label className="sr-only" htmlFor={`${panelId}-input`}>
              Ask the meeting assistant
            </label>
            <textarea
              ref={input}
              id={`${panelId}-input`}
              rows={2}
              maxLength={6000}
              value={draft}
              placeholder="Ask about your meetings…"
              onChange={(event) => setDraft(event.target.value)}
              onKeyDown={(event) => {
                if (
                  event.key === "Enter" &&
                  !event.shiftKey &&
                  !event.nativeEvent.isComposing
                ) {
                  event.preventDefault();
                  if (!loading) void send(draft);
                }
              }}
            />
            <div className="row between">
              <small>Saved meetings & Jira · Chat clears on reload</small>
              {loading ? (
                <button
                  type="button"
                  className="button secondary small"
                  onClick={() => request.current?.abort()}>
                  Stop
                </button>
              ) : (
                <button
                  type="submit"
                  className="button primary small"
                  disabled={!draft.trim()}>
                  Send <Icon name="arrow" size={16} />
                </button>
              )}
            </div>
          </form>
        </section>
      )}
      <button
        type="button"
        ref={launcher}
        className="assistant-launcher"
        aria-label={open ? "Close meeting assistant" : "Open meeting assistant"}
        aria-expanded={open}
        aria-controls={panelId}
        onClick={() => (open ? close() : setOpen(true))}>
        <Icon name={open ? "close" : "bot"} size={27} />
      </button>
    </div>
  );
}
