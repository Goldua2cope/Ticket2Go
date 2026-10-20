import asyncio
import base64
import json
import os
import subprocess
import unittest
from unittest.mock import patch

import imageio_ffmpeg
from fastapi.testclient import TestClient

from backend.main import app
from backend.live_transcription import realtime_url


class FakeFoundry:
    """Only Azure is mocked; audio goes through the real FFmpeg decoder."""

    def __init__(self):
        self.events = asyncio.Queue()
        self.messages = []
        self.partial_sent = False
        self.pcm = bytearray()
        self.closed = False
        self.commit_count = 0

    async def __aenter__(self):
        return self

    async def __aexit__(self, *args):
        self.closed = True

    async def send(self, raw):
        message = json.loads(raw)
        self.messages.append(message)
        kind = message["type"]
        if kind == "session.update":
            await self.events.put({"type": "session.updated"})
        elif kind == "input_audio_buffer.append":
            self.pcm.extend(base64.b64decode(message["audio"]))
            if not self.partial_sent:
                self.partial_sent = True
                await self.events.put(
                    {
                        "type": "conversation.item.input_audio_transcription.delta",
                        "item_id": "turn1",
                        "delta": "Hello",
                    }
                )
        elif kind == "input_audio_buffer.commit":
            self.commit_count += 1
            item = f"turn{self.commit_count}"
            await self.events.put(
                {"type": "input_audio_buffer.committed", "item_id": item}
            )
            await self.events.put(
                {
                    "type": "conversation.item.input_audio_transcription.completed",
                    "item_id": item,
                    "transcript": "Hello world.",
                }
            )

    async def recv(self):
        return json.dumps(await self.events.get())

    def __aiter__(self):
        return self

    async def __anext__(self):
        return await self.recv()


class AudioTransportTests(unittest.TestCase):
    def setUp(self):
        self.environment = patch.dict(
            os.environ,
            {
                "AZURE_OPENAI_ENDPOINT": "https://example.openai.azure.com",
                "AZURE_OPENAI_API_KEY": "test-only",
                "AZURE_OPENAI_TRANSCRIBE_DEPLOYMENT": "gpt-live-transcribe",
            },
        )
        self.environment.start()
        self.addCleanup(self.environment.stop)

    def test_live_transcript_before_stop_and_final_before_done(self):
        audio = subprocess.check_output(
            [
                imageio_ffmpeg.get_ffmpeg_exe(),
                "-hide_banner",
                "-loglevel",
                "error",
                "-f",
                "lavfi",
                "-i",
                "sine=frequency=440:duration=2",
                "-c:a",
                "libopus",
                "-f",
                "webm",
                "-cluster_time_limit",
                "250",
                "pipe:1",
            ]
        )
        azure = FakeFoundry()
        with patch(
            "backend.live_transcription.connect", return_value=azure
        ), TestClient(app) as client:
            self.assertTrue(client.get("/health").json()["transcription_enabled"])
            with client.websocket_connect("/ws/transcribe") as socket:
                socket.send_json(
                    {"type": "start", "mimeType": "audio/webm;codecs=opus"}
                )
                for i in range(0, len(audio), 2000):
                    socket.send_bytes(audio[i : i + 2000])
                # Crucially, this arrives while the recording's input pipe is still open.
                self.assertEqual(
                    socket.receive_json(),
                    {
                        "type": "transcript",
                        "text": "Hello",
                        "isFinal": False,
                    },
                )
                socket.send_json({"type": "stop"})
                received = []
                while True:
                    event = socket.receive_json()
                    received.append(event)
                    if event["type"] in ("done", "error"):
                        break
                self.assertEqual(received[-1], {"type": "done"})
                self.assertIn(
                    {"type": "transcript", "text": "Hello world.", "isFinal": True},
                    received,
                )
        self.assertTrue(azure.closed)
        self.assertGreater(len(azure.pcm), 90_000)
        self.assertEqual(len(azure.pcm) % 2, 0)
        self.assertEqual(azure.commit_count, 1)
        self.assertIsNone(
            azure.messages[0]["session"]["audio"]["input"]["turn_detection"]
        )

    def test_audio_before_start_is_rejected(self):
        with TestClient(app) as client:
            with client.websocket_connect("/ws/transcribe") as socket:
                socket.send_bytes(b"unexpected audio")
                self.assertEqual(socket.receive_json()["type"], "error")

    def test_invalid_control_messages_are_rejected(self):
        for message in ("not json", "[]", '{"type":"stop"}'):
            with self.subTest(message=message), TestClient(app) as client:
                with client.websocket_connect("/ws/transcribe") as socket:
                    socket.send_text(message)
                    self.assertEqual(socket.receive_json()["type"], "error")

    def test_missing_config_is_reported(self):
        with patch.dict(os.environ, {"AZURE_OPENAI_API_KEY": ""}), TestClient(
            app
        ) as client:
            with client.websocket_connect("/ws/transcribe") as socket:
                socket.send_json({"type": "start", "mimeType": "audio/webm"})
                self.assertIn("Export", socket.receive_json()["message"])

    def test_url_accepts_resource_or_v1_endpoint(self):
        for endpoint in (
            "https://example.openai.azure.com",
            "https://example.openai.azure.com/openai/v1/",
        ):
            self.assertEqual(
                realtime_url(endpoint),
                "wss://example.openai.azure.com/openai/v1/realtime?intent=transcription",
            )

    def test_foundry_rejection_is_returned_to_frontend(self):
        class RejectingFoundry(FakeFoundry):
            async def send(self, raw):
                await self.events.put(
                    {"type": "error", "error": {"code": "invalid_value"}}
                )

        azure = RejectingFoundry()
        with patch(
            "backend.live_transcription.connect", return_value=azure
        ), TestClient(app) as client:
            with client.websocket_connect("/ws/transcribe") as socket:
                socket.send_json({"type": "start", "mimeType": "audio/webm"})
                self.assertIn("Foundry rejected", socket.receive_json()["message"])
        self.assertTrue(azure.closed)

    def test_final_transcripts_stay_in_order(self):
        class ReorderedFoundry(FakeFoundry):
            async def send(self, raw):
                message = json.loads(raw)
                if message["type"] != "input_audio_buffer.commit":
                    return await super().send(raw)
                self.commit_count += 1
                await self.events.put(
                    {
                        "type": "input_audio_buffer.committed",
                        "item_id": f"turn{self.commit_count}",
                    }
                )
                if self.commit_count == 2:
                    for item, text in [("turn2", "Second."), ("turn1", "First.")]:
                        await self.events.put(
                            {
                                "type": "conversation.item.input_audio_transcription.completed",
                                "item_id": item,
                                "transcript": text,
                            }
                        )

        audio = subprocess.check_output(
            [
                imageio_ffmpeg.get_ffmpeg_exe(),
                "-hide_banner",
                "-loglevel",
                "error",
                "-f",
                "lavfi",
                "-i",
                "sine=frequency=440:duration=11",
                "-c:a",
                "libopus",
                "-f",
                "webm",
                "pipe:1",
            ]
        )
        azure = ReorderedFoundry()
        with patch(
            "backend.live_transcription.connect", return_value=azure
        ), TestClient(app) as client:
            with client.websocket_connect("/ws/transcribe") as socket:
                socket.send_json({"type": "start", "mimeType": "audio/webm"})
                socket.send_bytes(audio)
                socket.send_json({"type": "stop"})
                finals = []
                while True:
                    event = socket.receive_json()
                    self.assertNotEqual(event["type"], "error", event)
                    if event["type"] == "done":
                        break
                    if event.get("isFinal"):
                        finals.append(event["text"])
                self.assertEqual(finals, ["First.", "Second."])


if __name__ == "__main__":
    unittest.main()
