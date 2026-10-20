import os
import unittest
from unittest.mock import AsyncMock, MagicMock, patch

from azure.core.exceptions import ResourceExistsError, ResourceNotFoundError

from backend.storage import TranscriptStorageError, save_meeting_transcript

MEETING_ID = "fb74dfd8-5f3a-4a82-9f5b-bb0c1c710817"


class StorageTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        environment = patch.dict(os.environ, {
            "AZURE_STORAGE_CONNECTION_STRING": "test-connection-string",
            "AZURE_STORAGE_CONTAINER_NAME": "ticket2go-container",
        })
        environment.start()
        self.addCleanup(environment.stop)
        self.blob = MagicMock()
        self.blob.upload_blob = AsyncMock()
        self.service = MagicMock()
        self.service.__aenter__ = AsyncMock(return_value=self.service)
        self.service.__aexit__ = AsyncMock(return_value=False)
        self.service.get_blob_client.return_value = self.blob
        client = patch(
            "backend.storage.BlobServiceClient.from_connection_string",
            return_value=self.service,
        )
        self.client = client.start()
        self.addCleanup(client.stop)

    async def test_saves_unicode_text_and_meeting_id_without_overwriting(self):
        text = "Möte om nästa release. Tack!"
        name = await save_meeting_transcript(MEETING_ID, text)
        self.assertEqual(name, f"{MEETING_ID}.txt")
        self.service.get_blob_client.assert_called_once_with(
            container="ticket2go-container", blob=name,
        )
        args = self.blob.upload_blob.await_args
        self.assertEqual(args.args[0].decode("utf-8"), text)
        self.assertEqual(args.kwargs["metadata"], {"meeting_id": MEETING_ID})
        self.assertFalse(args.kwargs["overwrite"])
        self.assertEqual(
            args.kwargs["content_settings"].content_type, "text/plain; charset=utf-8",
        )
        self.service.__aexit__.assert_awaited_once()

    async def test_empty_transcript_is_not_uploaded(self):
        with self.assertRaises(TranscriptStorageError):
            await save_meeting_transcript(MEETING_ID, "  ")
        self.client.assert_not_called()

    async def test_missing_credentials_is_not_success(self):
        with patch.dict(os.environ, {"AZURE_STORAGE_CONNECTION_STRING": ""}):
            with self.assertRaisesRegex(TranscriptStorageError, "Export"):
                await save_meeting_transcript(MEETING_ID, "Meeting text")
        self.client.assert_not_called()

    async def test_existing_blob_is_not_overwritten(self):
        self.blob.upload_blob.side_effect = ResourceExistsError("Already exists")
        with self.assertRaisesRegex(TranscriptStorageError, "not overwritten"):
            await save_meeting_transcript(MEETING_ID, "Meeting text")

    async def test_missing_container_is_reported(self):
        self.blob.upload_blob.side_effect = ResourceNotFoundError("Not found")
        with self.assertRaisesRegex(TranscriptStorageError, "existing container"):
            await save_meeting_transcript(MEETING_ID, "Meeting text")

    async def test_timeout_does_not_claim_save_succeeded(self):
        self.blob.upload_blob.side_effect = TimeoutError()
        with self.assertRaisesRegex(TranscriptStorageError, "Could not confirm"):
            await save_meeting_transcript(MEETING_ID, "Meeting text")
