import { useEffect, useRef, useState } from "react";
import type { Meeting } from "../lib/types";

export function useRecorder(onComplete: (meeting: Meeting) => void) {
  const [status, setStatus] = useState<
    "idle" | "starting" | "recording" | "stopping" | "saving" | "summarizing"
  >("idle");
  const [error, setError] = useState("");
  const [transcript, setTranscript] = useState("");
  const [partial, setPartial] = useState("");
  const [savedMeetingId, setSavedMeetingId] = useState("");
  const [summary, setSummary] = useState("");
  const recorderRef = useRef<MediaRecorder | null>(null);
  const streamRef = useRef<MediaStream | null>(null);
  const mountedRef = useRef(false);
  const disconnectRef = useRef<() => void>(() => {});

  useEffect(() => {
    mountedRef.current = true;
    return () => {
      mountedRef.current = false;
      disconnectRef.current();
      const recorder = recorderRef.current;
      if (recorder) {
        recorder.onstart = null;
        recorder.ondataavailable = null;
        recorder.onstop = null;
        recorder.onerror = null;
      }
      if (recorder && recorder.state !== "inactive") recorder.stop();
      streamRef.current?.getTracks().forEach((track) => track.stop());
    };
  }, []);

  async function startRecording() {
    setError("");
    if (
      !navigator.mediaDevices?.getUserMedia ||
      typeof MediaRecorder === "undefined"
    ) {
      setError("Recording requires a supported browser on localhost or HTTPS.");
      return;
    }

    const startedAt = new Date().toISOString();
    let receivedTranscript = "";
    setStatus("starting");
    setTranscript("");
    setPartial("");
    setSavedMeetingId("");
    setSummary("");
    try {
      const stream = await navigator.mediaDevices.getUserMedia({ audio: true });
      if (!mountedRef.current) {
        stream.getTracks().forEach((track) => track.stop());
        return;
      }
      streamRef.current = stream;
      const recorder = new MediaRecorder(stream);
      recorderRef.current = recorder;
      // Vite proxies this path to the local backend during development.
      const endpoint = new URL(
        import.meta.env.VITE_TRANSCRIPTION_WS_URL || "/ws/transcribe",
        window.location.href,
      );
      if (endpoint.protocol === "http:") endpoint.protocol = "ws:";
      if (endpoint.protocol === "https:") endpoint.protocol = "wss:";
      const socket = new WebSocket(endpoint);
      let failed = false;
      let stopping = false;
      let timer: ReturnType<typeof setTimeout>;

      function disconnect() {
        clearTimeout(timer);
        socket.onopen = null;
        socket.onmessage = null;
        socket.onerror = null;
        socket.onclose = null;
        socket.close();
      }
      disconnectRef.current = disconnect;

      function fail(message: string) {
        if (failed) return;
        failed = true;
        disconnect();
        setError(message);
        setPartial("");
        if (recorder.state !== "inactive") {
          setStatus("stopping");
          recorder.stop();
        } else {
          setStatus("idle");
        }
        stream.getTracks().forEach((track) => track.stop());
      }

      timer = setTimeout(
        () =>
          fail("The transcription server did not connect. Please try again."),
        10_000,
      );
      socket.onopen = () => {
        clearTimeout(timer);
        try {
          // Chunks belong to one continuous recording, not separate audio files.
          recorder.start(250);
        } catch {
          fail("Could not start streaming audio. Please try again.");
        }
      };
      socket.onmessage = (event) => {
        try {
          const message = JSON.parse(event.data);
          if (
            message.type === "transcript" &&
            typeof message.text === "string" &&
            typeof message.isFinal === "boolean"
          ) {
            if (message.isFinal) {
              receivedTranscript = [receivedTranscript, message.text]
                .filter(Boolean)
                .join(" ");
              setTranscript((previous) =>
                [previous, message.text].filter(Boolean).join(" "),
              );
              setPartial("");
            } else {
              setPartial(message.text);
            }
          } else if (message.type === "saving" && stopping) {
            clearTimeout(timer);
            setStatus("saving");
            timer = setTimeout(
              () =>
                fail(
                  "Saving took too long. Check Azure Storage before recording again; your transcript is still here.",
                ),
              30_000,
            );
          } else if (
            message.type === "summarizing" &&
            stopping &&
            typeof message.meeting_id === "string"
          ) {
            clearTimeout(timer);
            setSavedMeetingId(message.meeting_id);
            setStatus("summarizing");
            timer = setTimeout(
              () =>
                fail(
                  "Summary generation took too long. Your transcript was saved and is still on this page.",
                ),
              75_000,
            );
          } else if (
            message.type === "done" &&
            stopping &&
            typeof message.meeting_id === "string" &&
            typeof message.summary === "string" &&
            message.summary.trim()
          ) {
            disconnect();
            setPartial("");
            setSavedMeetingId(message.meeting_id);
            setSummary(message.summary);
            onComplete({
              id: message.meeting_id,
              title: `Meeting · ${new Date(startedAt).toLocaleDateString(undefined, { month: "short", day: "numeric" })}`,
              startedAt,
              processingStatus: "completed",
              transcript: receivedTranscript,
              summary: message.summary,
              tasks: [],
            });
            setStatus("idle");
          } else if (message.type === "error") {
            fail(
              typeof message.message === "string"
                ? message.message
                : "Transcription failed.",
            );
          } else {
            fail("The transcription server sent an unexpected message.");
          }
        } catch {
          fail("The transcription server sent an invalid message.");
        }
      };
      socket.onerror = () =>
        fail(
          "Cannot connect to the transcription server. Check that the backend is running.",
        );
      socket.onclose = () =>
        fail(
          "The transcription connection closed before it finished. Please try again.",
        );

      recorder.onstart = () => {
        if (failed || !mountedRef.current) return;
        try {
          // The browser has selected its actual format by the start event.
          // Send this control message before the first dataavailable event.
          socket.send(
            JSON.stringify({ type: "start", mimeType: recorder.mimeType }),
          );
          setStatus("recording");
        } catch {
          fail("Could not start streaming audio. Please try again.");
        }
      };
      recorder.ondataavailable = (event) => {
        if (event.data.size === 0) return;

        if (!failed && socket.readyState === WebSocket.OPEN) {
          // Stop instead of accumulating unbounded audio on a slow connection.
          if (socket.bufferedAmount > 1024 * 1024) {
            fail(
              "The connection is too slow to stream audio. Please try again.",
            );
          } else {
            socket.send(event.data);
          }
        }
      };
      recorder.onstop = () => {
        stream.getTracks().forEach((track) => track.stop());
        if (!mountedRef.current) return;

        if (failed) {
          setStatus("idle");
        } else if (socket.readyState === WebSocket.OPEN) {
          stopping = true;
          // The final dataavailable event is sent before this end marker.
          socket.send(JSON.stringify({ type: "stop" }));
          timer = setTimeout(() => {
            fail("Timed out waiting for the final transcript.");
            setStatus("idle");
          }, 30_000);
        } else {
          fail("The transcription connection is no longer available.");
        }
      };
      recorder.onerror = () => {
        fail("Recording failed. Please try again.");
      };
    } catch {
      disconnectRef.current();
      streamRef.current?.getTracks().forEach((track) => track.stop());
      if (mountedRef.current) {
        setError(
          "Could not access your microphone. Check your browser permissions and try again.",
        );
        setStatus("idle");
      }
    }
  }

  function stopRecording() {
    const recorder = recorderRef.current;
    if (recorder && recorder.state !== "inactive") {
      setStatus("stopping");
      recorder.stop();
    }
  }

  return {
    status,
    error,
    transcript,
    partial,
    savedMeetingId,
    summary,
    startRecording,
    stopRecording,
  };
}
