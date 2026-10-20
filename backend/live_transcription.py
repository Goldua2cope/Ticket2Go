"""Bridge a browser's encoded audio stream to Azure's Realtime transcription API."""

import asyncio
import base64
import json
from collections import deque
from contextlib import suppress
from urllib.parse import urlsplit, urlunsplit

import imageio_ffmpeg
from fastapi import WebSocket, WebSocketDisconnect
from websockets.asyncio.client import connect

PCM_BYTES_PER_SECOND = 24_000 * 2  # Mono, 16-bit, 24 kHz.
FINAL_TIMEOUT = 20


class TranscriptionError(Exception):
    """A message that is safe to show in the recorder."""


def realtime_url(endpoint: str) -> str:
    parsed = urlsplit(endpoint.strip())
    if parsed.scheme != "https" or not parsed.netloc:
        raise TranscriptionError("AZURE_OPENAI_ENDPOINT must be an HTTPS resource endpoint.")
    path = parsed.path.rstrip("/")
    if path not in ("", "/openai", "/openai/v1"):
        raise TranscriptionError("Use your Azure resource endpoint, not a deployment or project URL.")
    return urlunsplit(("wss", parsed.netloc, "/openai/v1/realtime", "intent=transcription", ""))


def session_update(deployment: str) -> dict:
    return {
        "type": "session.update",
        "session": {
            "type": "transcription",
            "audio": {"input": {
                "format": {"type": "audio/pcm", "rate": 24000},
                "transcription": {"model": deployment},
                "turn_detection": None,
            }},
        },
    }


def check_error(event: dict):
    if event.get("type") in ("error", "conversation.item.input_audio_transcription.failed"):
        # Do not forward arbitrary upstream details that could contain input or credentials.
        raise TranscriptionError(
            "Foundry rejected transcription. Check the deployment supports "
            "gpt-live-transcribe, your credentials, region, and quota."
        )


async def start_decoder(mime_type: str):
    container = mime_type.split(";")[0].strip()
    formats = {"audio/webm": "matroska", "audio/ogg": "ogg", "audio/mp4": "mov"}
    if container not in formats:
        raise TranscriptionError("Unsupported audio format. Try Chrome, Firefox, or Safari.")
    return await asyncio.create_subprocess_exec(
        imageio_ffmpeg.get_ffmpeg_exe(),
        "-hide_banner", "-loglevel", "error",
        "-probesize", "32", "-analyzeduration", "0",
        "-f", formats[container], "-i", "pipe:0",
        "-vn", "-ac", "1", "-ar", "24000", "-f", "s16le",
        "-flush_packets", "1", "pipe:1",
        stdin=asyncio.subprocess.PIPE,
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.DEVNULL,
    )


async def stream_transcription(browser: WebSocket, mime_type: str, config: dict) -> str:
    """Stream updates and return the full, ordered transcript after Stop."""
    if not all(config.values()):
        raise TranscriptionError(
            "Export AZURE_OPENAI_ENDPOINT, AZURE_OPENAI_API_KEY, and "
            "AZURE_OPENAI_TRANSCRIBE_DEPLOYMENT (or AZURE_OPENAI_DEPLOYMENT), then restart Python."
        )

    async with connect(
        realtime_url(config["endpoint"]),
        additional_headers={"api-key": config["key"]},
        open_timeout=10, close_timeout=2, max_size=4 * 1024 * 1024,
    ) as azure:
        await azure.send(json.dumps(session_update(config["deployment"])))
        async with asyncio.timeout(10):
            while True:
                event = json.loads(await azure.recv())
                check_error(event)
                if event.get("type") == "session.updated":
                    break

        decoder = await start_decoder(mime_type)
        finished = asyncio.Event()
        input_done = False
        commits_sent = 0
        finals_received = 0
        completed_segments = []
        tasks = []

        async def receive_audio():
            while True:
                event = await browser.receive()
                if event["type"] == "websocket.disconnect":
                    raise WebSocketDisconnect()
                audio = event.get("bytes")
                if audio is not None:
                    if len(audio) > 4 * 1024 * 1024:
                        raise TranscriptionError("Audio chunk is too large.")
                    decoder.stdin.write(audio)
                    await decoder.stdin.drain()
                else:
                    try:
                        message = json.loads(event.get("text") or "")
                    except json.JSONDecodeError:
                        raise TranscriptionError("Expected binary audio or a stop message.")
                    if not isinstance(message, dict) or message.get("type") != "stop":
                        raise TranscriptionError("Expected binary audio or a stop message.")
                    decoder.stdin.close()
                    return

        async def send_pcm():
            nonlocal input_done, commits_sent
            turn_bytes = 0
            total_bytes = 0
            # Read 100 ms at a time; readexactly keeps samples aligned.
            while True:
                try:
                    pcm = await decoder.stdout.readexactly(4800)
                    at_end = False
                except asyncio.IncompleteReadError as exc:
                    pcm = exc.partial
                    at_end = True
                if pcm:
                    total_bytes += len(pcm)
                    turn_bytes += len(pcm)
                    await azure.send(json.dumps({
                        "type": "input_audio_buffer.append",
                        "audio": base64.b64encode(pcm).decode("ascii"),
                    }))
                    # Bound each turn while retaining live deltas between commits.
                    if turn_bytes >= PCM_BYTES_PER_SECOND * 10:
                        commits_sent += 1
                        await azure.send(json.dumps({"type": "input_audio_buffer.commit"}))
                        turn_bytes = 0
                if at_end:
                    break
            if await decoder.wait() != 0 or not total_bytes:
                raise TranscriptionError("Could not decode the recorded audio. Please try again.")
            if turn_bytes:
                # Realtime commits need at least 100 ms; pad only a short final tail.
                if turn_bytes < 4800:
                    await azure.send(json.dumps({
                        "type": "input_audio_buffer.append",
                        "audio": base64.b64encode(bytes(4800 - turn_bytes)).decode("ascii"),
                    }))
                commits_sent += 1
                await azure.send(json.dumps({"type": "input_audio_buffer.commit"}))
            input_done = True
            if finals_received == commits_sent:
                finished.set()

        async def receive_transcripts():
            nonlocal finals_received
            order = deque()
            partials = {}
            finals = {}
            async for raw in azure:
                event = json.loads(raw)
                check_error(event)
                kind = event.get("type")
                item = event.get("item_id")
                if kind == "input_audio_buffer.committed":
                    order.append(item)
                elif kind == "conversation.item.input_audio_transcription.delta":
                    partials[item] = partials.get(item, "") + event.get("delta", "")
                elif kind == "conversation.item.input_audio_transcription.completed":
                    finals[item] = event.get("transcript", "")
                else:
                    continue

                # Completion events may arrive out of order: emit in commit order.
                while order and order[0] in finals:
                    current = order.popleft()
                    text = finals.pop(current)
                    completed_segments.append(text)
                    await browser.send_json({
                        "type": "transcript", "text": text, "isFinal": True,
                    })
                    partials.pop(current, None)
                    finals_received += 1
                await browser.send_json({
                    "type": "transcript", "text": " ".join(partials.values()), "isFinal": False,
                })
                if input_done and finals_received == commits_sent:
                    finished.set()
            raise TranscriptionError("Foundry closed the connection before transcription finished.")

        async def finish_recording():
            await input_task
            async with asyncio.timeout(FINAL_TIMEOUT):
                await pcm_task
                await finished.wait()

        try:
            input_task = asyncio.create_task(receive_audio())
            pcm_task = asyncio.create_task(send_pcm())
            receiver_task = asyncio.create_task(receive_transcripts())
            finish_task = asyncio.create_task(finish_recording())
            tasks = [input_task, pcm_task, receiver_task, finish_task]
            # Surface failures immediately even while the microphone is still active.
            pending = set(tasks)
            while pending:
                done, pending = await asyncio.wait(pending, return_when=asyncio.FIRST_COMPLETED)
                for task in done:
                    task.result()
                if finish_task in done:
                    return " ".join(text for text in completed_segments if text)
        finally:
            for task in tasks:
                task.cancel()
            await asyncio.gather(*tasks, return_exceptions=True)
            if decoder.returncode is None:
                with suppress(ProcessLookupError):
                    decoder.kill()
            await decoder.wait()
