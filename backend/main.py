"""Receive browser audio and return live Foundry transcripts."""

import asyncio
import json
import logging
from contextlib import suppress
from pathlib import Path
from uuid import uuid4
import uvicorn

from fastapi import FastAPI, WebSocket, WebSocketDisconnect
from fastapi.responses import JSONResponse
from fastapi.staticfiles import StaticFiles
from websockets.exceptions import InvalidStatus

from backend.live_transcription import TranscriptionError, stream_transcription
from backend.storage import (
    TranscriptStorageError,
    save_meeting_transcript,
)
from backend.config import lifespan, check_cosmos, check_postgres, check_storage_container, run_health_check
from backend.meeting_ingestion import ingest_meeting_transcript

logger = logging.getLogger("uvicorn.error")

# Initialize Application
app = FastAPI(lifespan=lifespan)

    
@app.get("/health")
async def health():
    """
    Checks weather Azure resources are reachable.
    """
    check_list = {
        "postgres": check_postgres,
        "cosmos": check_cosmos,
        "storage_container": check_storage_container
    }

    results = dict(await asyncio.gather(
        *(
            run_health_check(app, name, check)
            for name, check in check_list.items()
        )
    ))

    healthy = all(results.values())

    return JSONResponse(
        status_code=200 if healthy else 503,
        content={
            "status": "ready" if healthy else "not_ready",
            "checks": results,
        }
    )

@app.websocket("/ws/transcribe")
async def transcribe(websocket: WebSocket):
    """
    Handle a real-time audio transcription session over WebSocket.

    Validates the client's start message and audio format, streams audio
    to the transcription service, saves the completed transcript, and
    sends progress, completion, or error messages back to the client.
    Ensures the WebSocket is closed when the session ends.
    """
    await websocket.accept()

    try:
        # Wait up to 15 seconds for the initial start message.
        event = await asyncio.wait_for(
            websocket.receive(),
            timeout=15
        )

        # Exit if the client disconnects before starting.
        if event["type"] == "websocket.disconnect":
            return

        # Parse the initial message as JSON.
        try:
            message = json.loads(event.get("text") or "")
        except json.JSONDecodeError:
            raise TranscriptionError("Send a JSON start message before sending audio.")

        # Require a valid start command before receiving audio.
        if not isinstance(message, dict) or message.get("type") != "start":
            raise TranscriptionError("Send a start message before sending audio.")

        # Validate the audio MIME type supplied by the client.
        mime_type = message.get("mimeType")
        if not isinstance(mime_type, str) or not mime_type.startswith("audio/"):
            raise TranscriptionError("Start must include an audio MIME type.")

        # Create a unique ID and stream audio to the transcription service.
        logger.info("Connecting audio stream to Foundry transcription")
        meeting_id = str(uuid4())
        transcript = await stream_transcription(websocket, mime_type, app.state)

        # Notify the client that the completed transcript is being saved.
        await websocket.send_json({"type": "saving", "meeting_id": meeting_id})

        # Persist the transcript and retrieve its storage identifier.
        blob_name = await save_meeting_transcript(meeting_id, transcript, app.state)

        # Tell the client that transcription and storage have completed.
        await websocket.send_json({
            "type": "done",
            "meeting_id": meeting_id,
            "blob_name": blob_name,
        })
        logger.info("Meeting %s saved", meeting_id)

    # Stop quietly when the client disconnects.
    except WebSocketDisconnect:
        return

    except Exception as exc:
        # Return safe, user-friendly messages for expected errors.
        if isinstance(exc, (TranscriptionError, TranscriptStorageError)):
            message = str(exc)
        elif isinstance(exc, (TimeoutError, asyncio.TimeoutError)):
            message = "Timed out waiting for audio or Foundry. Please try again."
        elif isinstance(exc, InvalidStatus):
            message = (
                f"Foundry connection rejected (HTTP {exc.response.status_code}). "
                "Check the Azure endpoint, API key, deployment, and access permissions."
            )
        else:
            message = f"Could not stream transcription. Check the backend connection and audio format. : {exc}"

        # Log the exception type without exposing credentials or audio data.
        logger.warning("Transcription stopped (%s)", type(exc).__name__)

        # Send the error to the client if the connection is still available.
        with suppress(RuntimeError, WebSocketDisconnect, OSError):
            await websocket.send_json({"type": "error", "message": message})

    finally:
        # Close the WebSocket regardless of success or failure.
        with suppress(RuntimeError, WebSocketDisconnect, OSError):
            await websocket.close()

# Backend serves Frontend
frontend_dist = (Path(__file__).resolve().parent.parent / "frontend" / "dist")
app.mount("/", StaticFiles(directory=frontend_dist, html=True))

if __name__ == "__main__":
    uvicorn.run("backend.main:app", host="127.0.0.1", port=8000)

