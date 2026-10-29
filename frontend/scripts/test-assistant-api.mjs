import assert from "node:assert/strict";
import fs from "node:fs";
import ts from "typescript";
const source = fs.readFileSync(
  new URL("../src/lib/assistantApi.ts", import.meta.url),
  "utf8",
);
const js = ts.transpileModule(source, {
  compilerOptions: { module: ts.ModuleKind.ESNext },
}).outputText;
const { streamAssistant } = await import(
  `data:text/javascript;base64,${Buffer.from(js).toString("base64")}`
);
const originalFetch = globalThis.fetch;
const input = [{ role: "user", content: "What changed?" }];
const frame = (event) => `data: ${JSON.stringify(event)}\r\n\r\n`;
function streamResponse(text) {
  const bytes = new TextEncoder().encode(text);
  return new Response(
    new ReadableStream({
      start(controller) {
        for (let offset = 0; offset < bytes.length; offset++)
          controller.enqueue(bytes.slice(offset, offset + 1));
        controller.close();
      },
    }),
    { headers: { "Content-Type": "text/event-stream" } },
  );
}
try {
  const expected = [
    { type: "status", message: "Retrieving…" },
    { type: "delta", text: "Meeting café ☕" },
    {
      type: "context_sources",
      sources: [
        {
          source_id: "jira:SCRUM-1",
          source_type: "jira_issue",
          issue_key: "SCRUM-1",
        },
      ],
    },
    { type: "done" },
  ];
  globalThis.fetch = async (url, options) => {
    assert.equal(url, "/api/meeting-assistant/chat");
    assert.equal(options.method, "POST");
    assert.deepEqual(JSON.parse(options.body), { messages: input });
    return streamResponse(": keepalive\r\n\r\n" + expected.map(frame).join(""));
  };
  const events = [];
  await streamAssistant(input, new AbortController().signal, (event) =>
    events.push(event),
  );
  assert.deepEqual(events, expected);
  globalThis.fetch = async () =>
    streamResponse(frame({ type: "delta", text: "Partial" }));
  await assert.rejects(
    streamAssistant(input, new AbortController().signal, () => {}),
    /before the answer was complete/,
  );
  globalThis.fetch = async () =>
    streamResponse(frame({ type: "error", message: "Retrieval failed" }));
  await assert.rejects(
    streamAssistant(input, new AbortController().signal, () => {}),
    /Retrieval failed/,
  );
  globalThis.fetch = async () =>
    Response.json({ detail: "Invalid question" }, { status: 422 });
  await assert.rejects(
    streamAssistant(input, new AbortController().signal, () => {}),
    /Invalid question/,
  );
  globalThis.fetch = async () =>
    new Response("<html>Wrong server</html>", {
      headers: { "Content-Type": "text/html" },
    });
  await assert.rejects(
    streamAssistant(input, new AbortController().signal, () => {}),
    /unexpected response/,
  );
  globalThis.fetch = async (_url, options) => {
    options.signal.throwIfAborted();
  };
  const controller = new AbortController();
  controller.abort();
  await assert.rejects(
    streamAssistant(input, controller.signal, () => {}),
    { name: "AbortError" },
  );
  console.log(
    "Assistant API checks passed: request, streaming frames, Unicode, sources, interrupted stream, errors, and cancellation.",
  );
} finally {
  globalThis.fetch = originalFetch;
}
