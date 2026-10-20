"""Save a completed meeting transcript to Azure Blob Storage."""

import asyncio
import os
from uuid import UUID

from azure.core.exceptions import AzureError, ResourceExistsError, ResourceNotFoundError
from azure.storage.blob import ContentSettings
from azure.storage.blob.aio import BlobServiceClient

DEFAULT_CONTAINER = "ticket2go-container"


class TranscriptStorageError(Exception):
    """Storage failure that can be displayed without exposing credentials."""


async def save_meeting_transcript(meeting_id: str, transcript: str) -> str:
    """Upload UTF-8 text as <meeting_id>.txt and return the blob name."""
    meeting_id = str(UUID(meeting_id))
    if not transcript.strip():
        raise TranscriptStorageError(
            "No transcript text was produced, so no file was saved."
        )

    connection_string = os.getenv("AZURE_STORAGE_CONNECTION_STRING", "").strip()
    container = os.getenv("AZURE_STORAGE_CONTAINER_NAME", DEFAULT_CONTAINER).strip()
    if not connection_string or not container:
        raise TranscriptStorageError(
            "Transcript not saved. Export AZURE_STORAGE_CONNECTION_STRING and "
            "AZURE_STORAGE_CONTAINER_NAME, then restart Python. Your text is still on this page."
        )

    blob_name = f"{meeting_id}.txt"
    try:
        async with asyncio.timeout(20):
            async with BlobServiceClient.from_connection_string(
                connection_string,
                retry_total=0,
                connection_timeout=10,
                read_timeout=10,
            ) as service:
                blob = service.get_blob_client(container=container, blob=blob_name)
                await blob.upload_blob(
                    transcript.encode("utf-8"),
                    overwrite=False,
                    content_settings=ContentSettings(
                        content_type="text/plain; charset=utf-8"
                    ),
                    metadata={"meeting_id": meeting_id},
                )
    except ResourceExistsError:
        raise TranscriptStorageError(
            f"Meeting {meeting_id} already exists in storage. It was not overwritten."
        ) from None
    except ResourceNotFoundError:
        raise TranscriptStorageError(
            "Transcript not saved. Check that AZURE_STORAGE_CONTAINER names an existing container."
        ) from None
    except (TimeoutError, AzureError, ValueError):
        raise TranscriptStorageError(
            f"Could not confirm that meeting {meeting_id} was saved. Check storage access "
            "and connectivity. Your transcript is still on this page."
        ) from None
    return blob_name
