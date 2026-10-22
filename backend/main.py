"""Receive browser audio and return live Foundry transcripts."""

import asyncio
import json
import logging
import os
from contextlib import asynccontextmanager, suppress
from pathlib import Path
from uuid import uuid4

from azure.identity import DefaultAzureCredential
from azure.keyvault.secrets import SecretClient
from fastapi import FastAPI, WebSocket, WebSocketDisconnect
from fastapi.responses import JSONResponse
from fastapi.staticfiles import StaticFiles
from websockets.exceptions import InvalidStatus

from backend.live_transcription import TranscriptionError, stream_transcription
from backend.storage import (
    TranscriptStorageError,
    save_meeting_transcript,
)
from backend.config import lifespan, check_cosmos, check_key_vault, check_postgres, check_storage_container


logger = logging.getLogger("uvicorn.error")

AZURE_OPENAI_TRANSCRIBE_DEPLOYMENT = "gpt-live-transcribe"

# Initialize Application
app = FastAPI(lifespan=lifespan)
# Backend serves Frontend
frontend_dist = (Path(__file__).resolve().parent.parent / "frontend" / "dist")
app.mount("/", StaticFiles(directory=frontend_dist, html=True))

@app.get("/health")
async def health():
    """
    Checks weather Azure resources are reachable.
    """
    checks = {
        "postgres": check_postgres,
        "cosmos": check_cosmos,
        "key_vault": check_key_vault,
        "storage_container": check_storage_container
    }

    results = dict(zip(
        checks,
        await asyncio.gather(
            *(
                check(app) for check in checks.values()
            ))
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
