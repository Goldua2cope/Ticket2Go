import os
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from azure.cosmos import CosmosClient
from dotenv import load_dotenv
from openai import OpenAI

load_dotenv(Path(__file__).resolve().with_name(".env"))

COSMOS_ENDPOINT = os.environ["AZURE_COSMOS_ENDPOINT"].strip()
COSMOS_KEY = os.environ["AZURE_COSMOS_KEY"].strip()
DATABASE_NAME = os.getenv("AZURE_COSMOS_DATABASE", "ticket2go-cosmos-db").strip()
CONTAINER_NAME = os.getenv("AZURE_COSMOS_CONTAINER", "knowledge_container").strip()

# Set embedding-specific values if this model is deployed in a different resource.
EMBEDDING_ENDPOINT = (
    os.getenv("AZURE_OPENAI_EMBEDDING_ENDPOINT")
    or os.getenv("AZURE_OPENAI_ENDPOINT", "")
).strip().rstrip("/")
EMBEDDING_API_KEY = (
    os.getenv("AZURE_OPENAI_EMBEDDING_API_KEY")
    or os.getenv("AZURE_OPENAI_API_KEY", "")
).strip()
EMBEDDING_DEPLOYMENT = os.environ["AZURE_OPENAI_EMBEDDING_DEPLOYMENT"].strip()
EMBEDDING_DIMENSIONS = int(os.getenv("AZURE_OPENAI_EMBEDDING_DIMENSIONS", "1536"))

CHUNK_MAX_CHARS = int(os.getenv("MEETING_CHUNK_MAX_CHARS", "2200"))
CHUNK_OVERLAP_CHARS = int(os.getenv("MEETING_CHUNK_OVERLAP_CHARS", "250"))
EMBEDDING_BATCH_SIZE = 32


def _make_embedding_client() -> OpenAI:
    if not EMBEDDING_ENDPOINT or not EMBEDDING_API_KEY:
        raise RuntimeError(
            "Configure the embedding endpoint and API key in .env. You can use "
            "AZURE_OPENAI_EMBEDDING_ENDPOINT/API_KEY or the shared "
            "AZURE_OPENAI_ENDPOINT/API_KEY variables."
        )
    base_url = EMBEDDING_ENDPOINT
    if not base_url.endswith("/openai/v1"):
        base_url += "/openai/v1"
    return OpenAI(base_url=base_url + "/", api_key=EMBEDDING_API_KEY)


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


def _embed_texts(texts: list[str]) -> list[list[float]]:
    """Call the Foundry embedding deployment in batches and validate dimensions."""
    if not texts:
        return []

    client = _make_embedding_client()
    all_vectors: list[list[float]] = []
    try:
        for offset in range(0, len(texts), EMBEDDING_BATCH_SIZE):
            batch = texts[offset : offset + EMBEDDING_BATCH_SIZE]
            response = client.embeddings.create(
                model=EMBEDDING_DEPLOYMENT,  # Use the deployment name here.
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
    finally:
        client.close()
    return all_vectors


def _cosmos_container():
    client = CosmosClient(COSMOS_ENDPOINT, credential=COSMOS_KEY)
    container = client.get_database_client(DATABASE_NAME).get_container_client(CONTAINER_NAME)
    return client, container


def _embed_and_upsert_documents(
    documents: list[dict[str, Any]],
    meeting_id: str,
    content_type: str,
) -> int:
    """Embed documents, upsert them, and remove stale documents of this type."""
    if not documents:
        return 0

    vectors = _embed_texts([doc["text"] for doc in documents])

    for doc, vector in zip(documents, vectors, strict=True):
        doc["embedding"] = vector

    client, container = _cosmos_container()

    try:
        # Stable IDs prevent duplicate documents on retries.
        for doc in documents:
            container.upsert_item(doc)

        current_ids = {doc["id"] for doc in documents}

        # this part only comes in play if the transcript is edited and uploaded again in the same session
        # Remove outdated chunks if the source text has changed.
        old_items = container.query_items(
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
            enable_cross_partition_query=True,
        )

        for item in old_items:
            if item["id"] not in current_ids:
                container.delete_item(
                    item=item["id"],
                    partition_key=item["id"],
                )

    finally:
        client.close()

    return len(documents)


def ingest_meeting_transcript(
    *,
    meeting_id: str,
    transcript: str,
    meeting_title: str | None = None,
    meeting_date: str | None = None,
    source_blob_uri: str | None = None,
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
            "embedding_model": EMBEDDING_DEPLOYMENT,
            "created_at": now,
        }
        for i, chunk in enumerate(chunks)
    ]

    count = _embed_and_upsert_documents(
        documents,
        meeting_id,
        "transcript_chunk",
    )

    return {
        "meeting_id": meeting_id,
        "transcript_chunks_indexed": count,
    }


def ingest_meeting_summary(
    *,
    meeting_id: str,
    summary: str,
    meeting_title: str | None = None,
    meeting_date: str | None = None,
    source_blob_uri: str | None = None,
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
        "embedding_model": EMBEDDING_DEPLOYMENT,
        "created_at": now,
    }

    count = _embed_and_upsert_documents(
        [document],
        meeting_id,
        "meeting_summary",
    )

    return {
        "meeting_id": meeting_id,
        "summary_indexed": count == 1,
    }


def main() -> None:

    meeting_id = "b3b0db5c-87ee-46cd-95b7-5517b66dd856"
    source_blob_uri = "b3b0db5c-87ee-46cd-95b7-5517b66dd856.txt"
    meeting_title = "Second meeting with Ville"
    meeting_date = datetime.now(timezone.utc).isoformat()

    with open("meeting2.txt", "r") as file:
        transcript = file.read()

    result = ingest_meeting_transcript(meeting_id=meeting_id, transcript=transcript,
                                        meeting_title=meeting_title, meeting_date=meeting_date, 
                                        source_blob_uri=source_blob_uri)

    print("Ingestion complete:")
    for key, value in result.items():
        print(f"  {key}: {value}")


if __name__ == "__main__":
    main()
