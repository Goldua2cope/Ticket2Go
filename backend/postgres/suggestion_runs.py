"""Make sure only one ticket suggestion run per meeting happens at a time."""

import hashlib
import logging
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from uuid import UUID

from starlette.datastructures import State

logger = logging.getLogger(__name__)


def _lock_key(meeting_id: UUID) -> int:
    """A signed 64-bit advisory-lock key, namespaced to this feature."""
    digest = hashlib.sha256(f"ticket_suggestion:{meeting_id}".encode()).digest()
    return int.from_bytes(digest[:8], "big", signed=True)


@asynccontextmanager
async def suggestion_run_lock(meeting_id: UUID, config: State) -> AsyncIterator[bool]:
    """Try to take this meeting's run lock. Yields True if acquired, False if
    another run already holds it.

    Uses a session-level PostgreSQL advisory lock on one pooled connection,
    held for the whole run and always released on exit. If the process dies,
    PostgreSQL releases the lock when the connection closes.
    """
    key = _lock_key(meeting_id)
    async with config.postgres.acquire() as connection:
        acquired = bool(await connection.fetchval("SELECT pg_try_advisory_lock($1)", key))
        try:
            yield acquired
        finally:
            if acquired:
                await connection.fetchval("SELECT pg_advisory_unlock($1)", key)
