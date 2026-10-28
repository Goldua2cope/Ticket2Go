"""Receive browser audio and return live Foundry transcripts."""

import asyncio
import json
import logging
from contextlib import suppress
from datetime import datetime, timezone
from pathlib import Path
from urllib.parse import urlsplit, urlunsplit
from uuid import uuid4
import uvicorn
from fastapi.staticfiles import StaticFiles

from fastapi import FastAPI, WebSocket, WebSocketDisconnect, HTTPException, Request
from fastapi.responses import JSONResponse, StreamingResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field
from websockets.exceptions import InvalidStatus

from backend.live_transcription import TranscriptionError, stream_transcription
from backend.summarisation import summarize
from backend.postgres.meetings import (
    MeetingStorageError,
    create_meeting,
    update_meeting,
)
from backend.storage import (
    TranscriptStorageError,
    save_meeting_transcript,
)
from backend.config import (
    lifespan,
    check_cosmos,
    check_postgres,
    check_storage_container,
    run_health_check,
)
from backend.meeting_assistant import (
    prepare_conversation_messages,
    stream_meeting_assistant,
)
from backend.meeting_ingestion import ingest_meeting_transcript, ingest_meeting_summary
from backend.jira_sync import sync_jira_to_cosmos
from backend.ticket_suggestion.routes import router as suggestion_router
from backend.ticket_suggestion.review_routes import router as review_router
from backend.meeting_routes import router as meeting_router

logger = logging.getLogger("uvicorn.error")

# Initialize Application
app = FastAPI(lifespan=lifespan)
app.include_router(suggestion_router)
app.include_router(review_router)
app.include_router(meeting_router)


class MeetingAssistantMessage(BaseModel):
    """One message in the active assistant conversation."""

    role: str = Field(pattern=r"^(user|assistant)$")
    content: str = Field(min_length=1, max_length=24_000)


class MeetingAssistantRequest(BaseModel):
    """Conversation history submitted by the frontend."""

    messages: list[MeetingAssistantMessage] = Field(
        min_length=1,
        max_length=100,
    )

async def _run_jira_sync() -> None:
    """Run Jira synchronization without interrupting the meeting."""
    logger.info("Starting background Jira synchronization.")

    try:
        result = await sync_jira_to_cosmos(app.state)

        logger.info(
            "Jira sync completed: mode=%s, issues=%s, upserted=%s, "
            "embeddings_generated=%s, vectors_reused=%s",
            result.get("mode"),
            result.get("unique_issues_processed"),
            result.get("documents_upserted"),
            result.get("embeddings_generated"),
            result.get("vectors_reused"),
        )

    except asyncio.CancelledError:
        logger.warning("Background Jira synchronization was cancelled.")
        raise

    except Exception:
        logger.exception(
            "Background Jira synchronization failed. "
            "Meeting transcription will continue."
        )


def _schedule_jira_sync() -> None:
    """Start Jira sync unless a sync is already running."""
    current_task = getattr(app.state, "jira_sync_task", None)

    if current_task is not None and not current_task.done():
        logger.info(
            "Jira synchronization is already running; skipping duplicate trigger."
        )
        return

    app.state.jira_sync_task = asyncio.create_task(
        _run_jira_sync(),
        name="jira-sync",
    )

@app.get("/health")
async def health():
    """
    Checks weather Azure resources are reachable.
    """
    check_list = {
        "postgres": check_postgres,
        "cosmos": check_cosmos,
        "storage_container": check_storage_container,
    }

    results = dict(
        await asyncio.gather(
            *(run_health_check(app, name, check) for name, check in check_list.items())
        )
    )

    healthy = all(results.values())

    return JSONResponse(
        status_code=200 if healthy else 503,
        content={
            "status": "ready" if healthy else "not_ready",
            "checks": results,
        },
    )

@app.post("/api/meeting-assistant/chat")
async def meeting_assistant_chat(
    payload: MeetingAssistantRequest,
    request: Request,
):
    """
    Stream Meeting Assistant responses and retrieval progress to the frontend.
    """
    messages = [
        message.model_dump()
        for message in payload.messages
    ]

    # Validate before starting the HTTP stream so invalid requests can
    # receive a proper HTTP 422 response.
    try:
        prepared_messages = prepare_conversation_messages(messages)
    except ValueError as exc:
        raise HTTPException(
            status_code=422,
            detail=str(exc),
        ) from exc

    async def event_stream():
        try:
            async for event in stream_meeting_assistant(
                request.app.state,
                prepared_messages,
            ):
                yield (
                    f"data: {json.dumps(event, ensure_ascii=False)}\n\n"
                )

        except asyncio.CancelledError:
            # Allow cancellation to propagate if the client disconnects.
            raise

        except Exception:
            logger.exception(
                "Meeting Assistant streaming endpoint failed."
            )

            # The response has already started, so report unexpected
            # failures through the stream rather than raising HTTPException.
            yield (
                "data: "
                + json.dumps(
                    {
                        "type": "error",
                        "message": (
                            "The Meeting Assistant request failed. "
                            "Please try again."
                        ),
                    },
                    ensure_ascii=False,
                )
                + "\n\n"
            )

    return StreamingResponse(
        event_stream(),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache",
            "X-Accel-Buffering": "no",
        },
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
    meeting_id = None
    meeting_created = False
    meeting_completed = False
    transcript_blob_uri = None

    try:
        # Wait up to 15 seconds for the initial start message.
        event = await asyncio.wait_for(websocket.receive(), timeout=15)

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

        # Create a unique ID and start Jira sync in the background.
        meeting_id = str(uuid4())
        await create_meeting(meeting_id, datetime.now(timezone.utc), app.state)
        meeting_created = True
        logger.info("Meeting %s is starting.", meeting_id)

        _schedule_jira_sync()

        # Start audio transcription without waiting for Jira synchronization.
        logger.info("Connecting audio stream to Foundry transcription")
        transcript = await stream_transcription(websocket, mime_type, app.state)

        # Notify the client that the completed transcript is being saved.
        await websocket.send_json({"type": "saving", "meeting_id": meeting_id})
        await update_meeting(meeting_id, "processing", app.state)

        # Persist the transcript and retrieve its storage identifier.
        blob_name = await save_meeting_transcript(meeting_id, transcript, app.state)
        # Keep the blob location, without storing SAS tokens or URL fragments.
        blob_url = urlsplit(app.state.container.get_blob_client(blob_name).url)
        transcript_blob_uri = urlunsplit(blob_url._replace(query="", fragment=""))
        await update_meeting(meeting_id, "processing", app.state, transcript_blob_uri)

        # Index the completed transcript in Cosmos DB.
        try:
            ingestion_result = await ingest_meeting_transcript(
                meeting_id=meeting_id,
                transcription=transcript,
                client_config=app.state,
            )

            logger.info(
                "Meeting transcript indexed in Cosmos DB: meeting_id=%s, chunks=%s",
                meeting_id,
                ingestion_result["transcript_chunks_indexed"],
            )

        except Exception:
            # Keep the saved transcript and allow summarization to continue.
            logger.exception(
                "Transcript ingestion into Cosmos DB failed for meeting_id=%s",
                meeting_id,
            )

        # Generate from the complete saved transcript over the same connection.
        await websocket.send_json(
            {
                "type": "summarizing",
                "meeting_id": meeting_id,
                "blob_name": blob_name,
            }
        )
        try:
            async with asyncio.timeout(60):
                summary = await summarize(
                    app.state.ai_client, app.state.model_response, transcript
                )
            if not summary.strip():
                raise ValueError("Empty summary")
        except Exception as exc:
            logger.warning("Summary failed (%s)", type(exc).__name__)
            await websocket.send_json(
                {
                    "type": "error",
                    "message": "Your transcript was saved, but summary generation failed. Your transcript is still on this page.",
                }
            )
            return

        # Index the summary in Cosmos DB.
        try:
            summary_result = await ingest_meeting_summary(
                meeting_id=meeting_id,
                summary=summary,
                client_config=app.state,
            )

            logger.info(
                "Meeting summary ingestion completed: meeting_id=%s, indexed=%s",
                meeting_id,
                summary_result["summary_indexed"],
            )

        except Exception:
            logger.exception(
                "Summary ingestion into Cosmos DB failed for meeting_id=%s",
                meeting_id,
            )

        await update_meeting(meeting_id, "completed", app.state)
        meeting_completed = True

        # Completion now includes the generated summary.
        await websocket.send_json(
            {
                "type": "done",
                "meeting_id": meeting_id,
                "blob_name": blob_name,
                "summary": summary,
            }
        )
        logger.info("Meeting %s saved", meeting_id)

    # Stop quietly when the client disconnects.
    except WebSocketDisconnect:
        return

    except Exception as exc:
        # Return safe, user-friendly messages for expected errors.
        if isinstance(
            exc, (TranscriptionError, TranscriptStorageError, MeetingStorageError)
        ):
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
        # Retain the blob reference if a later step fails or the client disconnects.
        if meeting_created and not meeting_completed:
            try:
                await update_meeting(
                    meeting_id, "failed", app.state, transcript_blob_uri
                )
            except MeetingStorageError:
                logger.warning("Could not mark meeting %s as failed", meeting_id)

        # Close the WebSocket regardless of success or failure.
        with suppress(RuntimeError, WebSocketDisconnect, OSError):
            await websocket.close()


# Backend serves Frontend
frontend_dist = Path(__file__).resolve().parent.parent / "frontend" / "dist"
app.mount("/", StaticFiles(directory=frontend_dist, html=True))

if __name__ == "__main__":
    uvicorn.run("backend.main:app", host="127.0.0.1", port=8000)