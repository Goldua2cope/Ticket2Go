"""Save a completed meeting transcript to Azure Blob Storage."""

import asyncio
import json
from uuid import UUID
from starlette.datastructures import State

from azure.core.exceptions import AzureError, ResourceExistsError, ResourceNotFoundError
from azure.storage.blob import ContentSettings


class TranscriptStorageError(Exception):
    """Storage failure that can be displayed without exposing credentials."""


async def load_meeting_transcript(meeting_id: str, config: State) -> dict:
    """Read the JSON transcript saved by save_meeting_transcript."""
    blob_name = f"{UUID(meeting_id)}.txt"
    try:
        async with asyncio.timeout(20):
            blob = config.container.get_blob_client(blob_name)
            download = await blob.download_blob()
            transcript = json.loads(await download.readall())
    except (TimeoutError, AzureError, ValueError):
        raise TranscriptStorageError("Could not read the saved meeting transcript.") from None

    segments = transcript.get("segments") if isinstance(transcript, dict) else None
    if not isinstance(segments, list) or not any(
        isinstance(segment, dict)
        and isinstance(segment.get("text"), str)
        and segment["text"].strip()
        for segment in segments
    ):
        raise TranscriptStorageError("The saved transcript contains no usable text.")
    return transcript


async def save_meeting_transcript(meeting_id: str, transcript: dict, config: State) -> str:
    """Upload UTF-8 text as <meeting_id>.txt and return the blob name."""
    meeting_id = str(UUID(meeting_id))

    # Ensure the transcript contains at least one non-empty segment
    segments = transcript.get("segments", [])
    if not isinstance(segments, list) or not any(
        isinstance(segment, dict)
        and isinstance(segment.get("text"), str)
        and segment["text"].strip()
        for segment in segments
    ):
        raise TranscriptStorageError(
            "No transcript text was produced, so no file was saved."
        )

    # Serialize the transcript dictionary to UTF-8 JSON.
    try:
        json_content = json.dumps(
            transcript,
            ensure_ascii=False,
            indent=2,
        ).encode("utf-8")
    except (TypeError, ValueError):
        raise TranscriptStorageError(
            "The transcript could not be converted to JSON."
        ) from None

    blob_name = f"{meeting_id}.txt"

    try:
        async with asyncio.timeout(20):
            blob = config.container.get_blob_client(blob_name)

            await blob.upload_blob(
                json_content,
                overwrite=False,
                content_settings=ContentSettings(
                    content_type="application/json; charset=utf-8"
                ),
                metadata={"meeting_id": meeting_id},
            )
    except ResourceExistsError:
        raise TranscriptStorageError(
            f"Meeting {meeting_id} already exists in storage. It was not overwritten."
        ) from None
    except ResourceNotFoundError:
        raise TranscriptStorageError(
            "Transcript not saved. Check that AZURE_STORAGE_CONTAINER_NAME names an existing container."
        ) from None
    except (TimeoutError, AzureError, ValueError):
        raise TranscriptStorageError(
            f"Could not confirm that meeting {meeting_id} was saved. Check storage access "
            "and connectivity. Your transcript is still on this page."
        ) from None
    
    return blob_name
