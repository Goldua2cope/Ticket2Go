from datetime import datetime, timezone
from typing import Any

from starlette.datastructures import State

EMBEDDING_DIMENSIONS = 1536

CHUNK_MAX_CHARS = 2200
CHUNK_OVERLAP_CHARS = 250
EMBEDDING_BATCH_SIZE = 32

def chunk_text(
    text: str,
    max_chars: int = CHUNK_MAX_CHARS,
    overlap_chars: int = CHUNK_OVERLAP_CHARS,
) -> list[str]:
    """Split transcript text into overlapping chunks, preferring sentence boundaries."""
    text = text.strip()
    if not text:
        return []
    if max_chars <= 0 or overlap_chars < 0 or overlap_chars >= max_chars:
        raise ValueError("Require max_chars > overlap_chars >= 0.")

    chunks: list[str] = []
    start = 0
    while start < len(text):
        end = min(start + max_chars, len(text))
        if end < len(text):
            search_start = start + max_chars // 2
            boundary = max(
                text.rfind("\n", search_start, end),
                text.rfind(". ", search_start, end),
                text.rfind("? ", search_start, end),
                text.rfind("! ", search_start, end),
            )
            if boundary > search_start:
                end = boundary + 1

        chunk = text[start:end].strip()
        if chunk:
            chunks.append(chunk)
        if end >= len(text):
            break
        start = max(start + 1, end - overlap_chars)
    return chunks


async def _embed_texts(texts: list[str], client_config: State) -> list[list[float]]:
    """Call the Foundry embedding deployment in batches and validate dimensions."""
    if not texts:
        return []

    client = client_config.ai_client

    all_vectors: list[list[float]] = []

    for offset in range(0, len(texts), EMBEDDING_BATCH_SIZE):
        batch = texts[offset : offset + EMBEDDING_BATCH_SIZE]
        response = await client.embeddings.create(
            model=client_config.model_embedding,  # Use the deployment name here.
            input=batch,
        )
        vectors: list[list[float] | None] = [None] * len(batch)
        for result in response.data:
            vectors[result.index] = result.embedding
        if any(vector is None for vector in vectors):
            raise RuntimeError("Embedding service returned an incomplete response.")
        for vector in vectors:
            assert vector is not None
            if len(vector) != EMBEDDING_DIMENSIONS:
                raise ValueError(
                    f"Embedding returned {len(vector)} dimensions; expected "
                    f"{EMBEDDING_DIMENSIONS}. Check the deployment and Cosmos policy."
                )
            all_vectors.append(vector)
            
    return all_vectors

async def _embed_and_upsert_documents(
    documents: list[dict[str, Any]],
    meeting_id: str,
    content_type: str,
    client_config: State
) -> int:
    """Embed documents, upsert them, and remove stale documents of this type."""
    if not documents:
        return 0

    vectors = await _embed_texts([doc["text"] for doc in documents], client_config)

    for doc, vector in zip(documents, vectors, strict=True):
        doc["embedding"] = vector

    client = client_config.cosmos_container
    # Stable IDs prevent duplicate documents on retries.

    for doc in documents:
        await client.upsert_item(doc)

    current_ids = {doc["id"] for doc in documents}

    # this part only comes in play if the transcript is edited and uploaded again in the same session
    # Remove outdated chunks if the source text has changed.
    old_items = client.query_items(
        query=(
            "SELECT c.id FROM c "
            "WHERE c.data_source = @source "
            "AND c.meeting_id = @meeting_id "
            "AND c.content_type = @content_type"
        ),
        parameters=[
            {"name": "@source", "value": "meeting_history"},
            {"name": "@meeting_id", "value": meeting_id},
            {"name": "@content_type", "value": content_type},
        ],
    )

    async for item in old_items:
        if item["id"] not in current_ids:
            await client.delete_item(
                item=item["id"],
                partition_key=item["id"],
            )

    return len(documents)


async def ingest_meeting_transcript(
    *,
    meeting_id: str,
    transcript: str,
    meeting_title: str | None = None,
    meeting_date: str | None = None,
    source_blob_uri: str | None = None,
    client_config: State
) -> dict[str, Any]:
    """Chunk, embed, and store a completed meeting transcript."""
    if not meeting_id.strip():
        raise ValueError("meeting_id must not be empty.")
    if not transcript.strip():
        raise ValueError("transcript must not be empty.")

    chunks = chunk_text(transcript)
    now = datetime.now(timezone.utc).isoformat()
    meeting_date = meeting_date or now

    documents = [
        {
            "id": f"meeting:{meeting_id}:chunk:{i:05d}",
            "data_source": "meeting_history",
            "content_type": "transcript_chunk",
            "meeting_id": meeting_id,
            "meeting_title": meeting_title,
            "meeting_date": meeting_date,
            "chunk_index": i,
            "text": chunk,
            "source_blob_uri": source_blob_uri,
            "embedding_model": client_config.model_embedding,
            "created_at": now,
        }
        for i, chunk in enumerate(chunks)
    ]

    count = await _embed_and_upsert_documents(
        documents,
        meeting_id,
        "transcript_chunk",
        client_config
    )

    return {
        "meeting_id": meeting_id,
        "transcript_chunks_indexed": count,
    }


async def ingest_meeting_summary(
    *,
    meeting_id: str,
    summary: str,
    meeting_title: str | None = None,
    meeting_date: str | None = None,
    source_blob_uri: str | None = None,
    client_config: State
) -> dict[str, Any]:
    """Embed and store a meeting summary as a separate searchable document."""
    if not meeting_id.strip():
        raise ValueError("meeting_id must not be empty.")
    if not summary.strip():
        raise ValueError("summary must not be empty.")

    now = datetime.now(timezone.utc).isoformat()
    
    document = {
        "id": f"meeting:{meeting_id}:summary",
        "data_source": "meeting_history",
        "content_type": "meeting_summary",
        "meeting_id": meeting_id,
        "meeting_title": meeting_title,
        "meeting_date": meeting_date or now,
        "chunk_index": -1,
        "text": summary.strip(),
        "source_blob_uri": source_blob_uri,
        "embedding_model": client_config.model_embedding,
        "created_at": now,
    }

    count = await _embed_and_upsert_documents(
        [document],
        meeting_id,
        "meeting_summary",
        client_config
    )

    return {
        "meeting_id": meeting_id,
        "summary_indexed": count == 1,
    }


#def main() -> None:

#    meeting_id = "b3b0db5c-87ee-46cd-95b7-5517b66dd856"
#    source_blob_uri = "b3b0db5c-87ee-46cd-95b7-5517b66dd856.txt"
#    meeting_title = "Second meeting with Ville"
#    meeting_date = datetime.now(timezone.utc).isoformat()

#    with open("meeting2.txt", "r") as file:
#        transcript = file.read()

#    result = ingest_meeting_transcript(meeting_id=meeting_id, transcript=transcript,
#                                        meeting_title=meeting_title, meeting_date=meeting_date, 
#                                        source_blob_uri=source_blob_uri)

#    print("Ingestion complete:")
#    for key, value in result.items():
#        print(f"  {key}: {value}")


#if __name__ == "__main__":
#    main()
