"""Receive browser audio and return live Foundry transcripts."""

import asyncio
import json
import logging
import os
from contextlib import asynccontextmanager, suppress
from pathlib import Path
from uuid import uuid4

from dotenv import load_dotenv
from fastapi import FastAPI, WebSocket, WebSocketDisconnect
from websockets.exceptions import InvalidStatus

from backend.live_transcription import TranscriptionError, stream_transcription
from backend.storage import (
    DEFAULT_CONTAINER,
    TranscriptStorageError,
    save_meeting_transcript,
)

load_dotenv(Path(__file__).with_name(".env"))
logger = logging.getLogger("uvicorn.error")


@asynccontextmanager
async def lifespan(app: FastAPI):
    app.state.azure = {
        "endpoint": os.getenv("AZURE_OPENAI_ENDPOINT", "").strip(),
        "key": os.getenv("AZURE_OPENAI_API_KEY", "").strip(),
        "deployment": (
            os.getenv("AZURE_OPENAI_TRANSCRIBE_DEPLOYMENT")
            or os.getenv("AZURE_OPENAI_DEPLOYMENT", "")
        ).strip(),
    }
    yield


app = FastAPI(lifespan=lifespan)


@app.get("/health")
async def health():
    configured = all(app.state.azure.values())
    return {
        "status": "ok",
        "azure_configured": configured,
        "transcription_enabled": configured,
        "azure_connection_verified": False,
        "storage_configured": bool(
            os.getenv("AZURE_STORAGE_CONNECTION_STRING", "").strip()
            and os.getenv("AZURE_STORAGE_CONTAINER_NAME", DEFAULT_CONTAINER).strip()
        ),
    }


@app.websocket("/ws/transcribe")
async def transcribe(websocket: WebSocket):
    await websocket.accept()
    try:
        event = await asyncio.wait_for(websocket.receive(), timeout=15)
        if event["type"] == "websocket.disconnect":
            return
        try:
            message = json.loads(event.get("text") or "")
        except json.JSONDecodeError:
            raise TranscriptionError("Send a JSON start message before sending audio.")
        if not isinstance(message, dict) or message.get("type") != "start":
            raise TranscriptionError("Send a start message before sending audio.")
        mime_type = message.get("mimeType")
        if not isinstance(mime_type, str) or not mime_type.startswith("audio/"):
            raise TranscriptionError("Start must include an audio MIME type.")
        logger.info("Connecting audio stream to Foundry transcription")
        meeting_id = str(uuid4())
        transcript = await stream_transcription(websocket, mime_type, app.state.azure)
        # Saving is the final step: the model and decoder have finished first.
        await websocket.send_json({"type": "saving", "meeting_id": meeting_id})
        blob_name = await save_meeting_transcript(meeting_id, transcript)
        await websocket.send_json(
            {
                "type": "done",
                "meeting_id": meeting_id,
                "blob_name": blob_name,
            }
        )
        logger.info("Meeting %s saved", meeting_id)
    except WebSocketDisconnect:
        return
    except Exception as exc:
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
            message = "Could not stream transcription. Check the backend connection and audio format."
        # Log only the exception class, not request headers, keys, or audio.
        logger.warning("Transcription stopped (%s)", type(exc).__name__)
        with suppress(RuntimeError, WebSocketDisconnect, OSError):
            await websocket.send_json({"type": "error", "message": message})
    finally:
        with suppress(RuntimeError, WebSocketDisconnect, OSError):
            await websocket.close()
