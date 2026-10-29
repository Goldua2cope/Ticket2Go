export type ChatMessage = { role: "user" | "assistant"; content: string };
export type ContextSource = {
  source_id: string;
  source_type: string;
  meeting_id?: string;
  meeting_title?: string;
  meeting_date?: string;
  issue_key?: string;
  summary?: string;
  url?: string;
};
export type AssistantEvent =
  | { type: "status"; message: string }
  | { type: "delta"; text: string }
  | { type: "context_sources"; sources: ContextSource[] }
  | { type: "done" }
  | { type: "error"; message: string };

export async function streamAssistant(
  messages: ChatMessage[],
  signal: AbortSignal,
  onEvent: (event: AssistantEvent) => void,
): Promise<void> {
  const response = await fetch("/api/meeting-assistant/chat", {
    method: "POST",
    headers: {
      "Content-Type": "application/json",
      Accept: "text/event-stream",
    },
    body: JSON.stringify({ messages }),
    signal,
  });
  if (!response.ok) {
    const data = await response.json().catch(() => null);
    throw new Error(
      typeof data?.detail === "string"
        ? data.detail
        : "Could not reach the meeting assistant. Please try again.",
    );
  }
  if (
    !response.body ||
    !response.headers.get("content-type")?.includes("text/event-stream")
  ) {
    throw new Error("The assistant returned an unexpected response.");
  }
  const reader = response.body.getReader();
  const decoder = new TextDecoder();
  let buffer = "";
  let completed = false;
  function consume(frame: string) {
    const data = frame
      .split(/\r?\n/)
      .filter((line) => line.startsWith("data:"))
      .map((line) => line.slice(5).trimStart())
      .join("\n");
    if (!data) return;
    const event: AssistantEvent = JSON.parse(data);
    if (event.type === "error")
      throw new Error(
        event.message || "The assistant could not finish. Please try again.",
      );
    onEvent(event);
    if (event.type === "done") completed = true;
  }
  try {
    while (!completed) {
      const { value, done } = await reader.read();
      buffer += decoder.decode(value, { stream: !done });
      let boundary: RegExpExecArray | null;
      while ((boundary = /\r?\n\r?\n/.exec(buffer))) {
        consume(buffer.slice(0, boundary.index));
        buffer = buffer.slice(boundary.index + boundary[0].length);
        if (completed) break;
      }
      if (done) break;
    }
    if (!completed)
      throw new Error(
        "The connection ended before the answer was complete. Please retry.",
      );
  } finally {
    await reader.cancel().catch(() => {});
    reader.releaseLock();
  }
}
