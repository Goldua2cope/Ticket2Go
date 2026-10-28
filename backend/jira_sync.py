"""Sync Jira issues into the project's Cosmos DB vector container.

First run:
    python jira_sync.py
    -> imports every issue visible in JIRA_PROJECT_KEY.

Later runs:
    python -m backend.jira_sync
    -> fetches issues updated since the last successful sync, with an overlap window.

Force a full project scan:
    python -m backend.jira_sync.py --full

This script writes to Cosmos DB (for vector search) and to the PostgreSQL
jira_issue_cache table (which ticket_proposals.jira_issue_key references).
It does not create or modify Jira issues.

If jira_issue_cache is empty, a full scan runs even when a checkpoint exists,
so the cache gets every issue and not only recently updated ones.

Requires meeting_ingestion.py in the same folder, because it reuses that module's
Cosmos connection and Azure AI Foundry embedding helpers. Keep .env beside the files.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError
from starlette.datastructures import State

#import requests
from dotenv import load_dotenv
#from requests.adapters import HTTPAdapter
#from requests.auth import HTTPBasicAuth
#from urllib3.util.retry import Retry
import asyncio
import httpx
from azure.cosmos.exceptions import CosmosResourceNotFoundError
from fastapi import FastAPI
from backend.config import lifespan
from backend.postgres.jira_issue_cache import cache_is_empty, upsert_issues

# Load this folder's .env before importing the shared helper module, which reads
# Cosmos and embedding settings when it is imported.
load_dotenv(Path(__file__).resolve().with_name(".env"))

from backend.meeting_ingestion import (  # noqa: E402
    EMBEDDING_DIMENSIONS,
    _embed_texts,
)

DEFAULT_MAX_EMBED_CHARS = 12000
COSMOS_VECTOR_FIELD = "embedding"
#EMBEDDING_DEPLOYMENT = "text-embedding-3-small"


JIRA_FIELDS = [
    "summary",
    "description",
    "issuetype",
    "project",
    "status",
    "priority",
    "assignee",
    "labels",
    "components",
    "created",
    "updated",
    "parent",
]

CHECKPOINT_DATA_SOURCE = "sync_state"
CHECKPOINT_CONTENT_TYPE = "sync_checkpoint"
DEFAULT_PAGE_SIZE = 100
DEFAULT_OVERLAP_MINUTES = 5
DEFAULT_TIMEOUT_SECONDS = 60
DEFAULT_MAX_EMBED_CHARS = 12000


def _required_state_value(state: State, attribute: str) -> str:
    """Read and validate a required value from FastAPI application state."""
    value = getattr(state, attribute, "")

    if not isinstance(value, str) or not value.strip():
        raise ValueError(
            f"Missing required application configuration: {attribute}"
        )

    return value.strip()


def _load_config(state: State) -> dict[str, Any]:
    """Read Jira connection settings from app.state and sync options from env."""

    base_url = _required_state_value(state, "jira_base_url").rstrip("/")
    email = _required_state_value(state, "jira_email")
    api_token = _required_state_value(state, "jira_api_token")
    project_key = _required_state_value(state, "jira_project_key")

    # Validate the Jira project key before using it in JQL.
    if not re.fullmatch(r"[A-Za-z][A-Za-z0-9_]*", project_key):
        raise ValueError(
            "JIRA_PROJECT_KEY should be a Jira project key containing letters, "
            "numbers, or underscores (for example SCRUM)."
        )

    try:
        overlap_minutes = int(
            os.getenv("JIRA_SYNC_OVERLAP_MINUTES", str(DEFAULT_OVERLAP_MINUTES))
        )
        page_size = int(os.getenv("JIRA_PAGE_SIZE", str(DEFAULT_PAGE_SIZE)))
        timeout_seconds = int(
            os.getenv("JIRA_REQUEST_TIMEOUT_SECONDS", str(DEFAULT_TIMEOUT_SECONDS))
        )
        max_embed_chars = int(
            os.getenv("JIRA_MAX_EMBED_CHARS", str(DEFAULT_MAX_EMBED_CHARS))
        )
    except ValueError as exc:
        raise ValueError(
            "JIRA_SYNC_OVERLAP_MINUTES, JIRA_PAGE_SIZE, "
            "JIRA_REQUEST_TIMEOUT_SECONDS, and JIRA_MAX_EMBED_CHARS "
            "must be integers."
        ) from exc

    if overlap_minutes < 0:
        raise ValueError("JIRA_SYNC_OVERLAP_MINUTES cannot be negative.")
    if not 1 <= page_size <= 100:
        raise ValueError("JIRA_PAGE_SIZE must be between 1 and 100.")
    if timeout_seconds <= 0:
        raise ValueError("JIRA_REQUEST_TIMEOUT_SECONDS must be greater than zero.")
    if max_embed_chars < 100:
        raise ValueError("JIRA_MAX_EMBED_CHARS must be at least 100 characters.")

    timezone_name = os.getenv("JIRA_TIMEZONE", "Europe/Stockholm").strip()

    try:
        jira_timezone = ZoneInfo(timezone_name)
    except ZoneInfoNotFoundError:
        local_timezone = datetime.now().astimezone().tzinfo or timezone.utc
        print(
            f"Warning: time zone '{timezone_name}' was not available. "
            f"Using the computer's local time zone ({local_timezone})."
        )
        jira_timezone = local_timezone

    return {
        "base_url": base_url,
        "email": email,
        "api_token": api_token,
        "project_key": project_key,
        "overlap_minutes": overlap_minutes,
        "page_size": page_size,
        "timeout_seconds": timeout_seconds,
        "max_embed_chars": max_embed_chars,
        "jira_timezone": jira_timezone,
        "timezone_name": timezone_name,
        "embedding_model": _required_state_value(state, "model_embedding"),
    }


def _make_jira_client(config: dict[str, Any]) -> httpx.AsyncClient:
    """Create an asynchronous, authenticated Jira HTTP client."""
    return httpx.AsyncClient(
        auth=(config["email"], config["api_token"]),
        headers={
            "Accept": "application/json",
            "Content-Type": "application/json",
        },
        timeout=httpx.Timeout(config["timeout_seconds"]),
    )


async def _post_with_retries(
    client: httpx.AsyncClient,
    url: str,
    payload: dict[str, Any],
    max_attempts: int = 5,
) -> httpx.Response:
    """Retry transient Jira API failures without blocking the event loop."""
    retryable_statuses = {429, 500, 502, 503, 504}

    for attempt in range(max_attempts):
        try:
            response = await client.post(url, json=payload)
        except httpx.RequestError:
            if attempt == max_attempts - 1:
                raise

            await asyncio.sleep(0.6 * (2 ** attempt))
            continue

        if (
            response.status_code not in retryable_statuses
            or attempt == max_attempts - 1
        ):
            return response

        retry_after = response.headers.get("Retry-After")

        try:
            delay = float(retry_after) if retry_after else 0.6 * (2 ** attempt)
        except ValueError:
            delay = 0.6 * (2 ** attempt)

        await asyncio.sleep(min(max(delay, 0), 30))

    raise RuntimeError("Jira request retries were exhausted.")


def _jql_timestamp(value: datetime, jira_timezone: Any) -> str:
    """Format a UTC checkpoint in the time zone Jira uses to interpret JQL dates."""
    local_value = value.astimezone(jira_timezone)
    # Jira JQL date/time literals have minute precision and must be quoted.
    return local_value.strftime("%Y-%m-%d %H:%M")


async def _fetch_all_issues(client: httpx.AsyncClient, config: dict[str, Any],
                            jql: str, *, fields: list[str] | None = None,) -> tuple[list[dict[str, Any]], int]:
    
    """Fetch all pages from Jira's enhanced JQL search endpoint."""
    url = f"{config['base_url']}/rest/api/3/search/jql"

    payload: dict[str, Any] = {
        "jql": jql,
        "fields": fields if fields is not None else JIRA_FIELDS,
        "maxResults": config["page_size"],
    }

    all_issues: list[dict[str, Any]] = []
    seen_tokens: set[str] = set()
    page_count = 0

    while True:
        response = await _post_with_retries(client, url, payload,)

        if not response.is_success:
            raise httpx.HTTPStatusError(
                f"Jira search failed with HTTP {response.status_code}: "
                f"{response.text[:1500]}",
                request=response.request,
                response=response,
            )

        try:
            body = response.json()
        except json.JSONDecodeError as exc:
            raise RuntimeError("Jira search returned a non-JSON response.") from exc

        issues = body.get("issues")

        if not isinstance(issues, list):
            raise RuntimeError("Jira response did not contain an 'issues' list.")

        page_count += 1
        all_issues.extend(issues)

        print(
            f"  Page {page_count}: received {len(issues)} issues"
        )

        if body.get("isLast") is True:
            break

        next_token = body.get("nextPageToken")

        if not next_token:
            if body.get("isLast") is False:
                raise RuntimeError(
                    "Jira reported more results but did not return "
                    "nextPageToken. Stopping pagination."
                )
            break

        if next_token in seen_tokens:
            raise RuntimeError(
                "Jira returned a repeated nextPageToken; "
                "stopping pagination."
            )

        seen_tokens.add(next_token)
        payload["nextPageToken"] = next_token

    return all_issues, page_count


def _extract_adf_text(node: Any) -> str:
    """Convert a Jira Atlassian Document Format description to readable text."""
    if node is None:
        return ""
    if isinstance(node, str):
        return node
    if isinstance(node, list):
        return "".join(_extract_adf_text(child) for child in node)
    if not isinstance(node, dict):
        return ""

    node_type = node.get("type")
    if node_type == "text":
        return str(node.get("text", ""))
    if node_type == "hardBreak":
        return "\n"

    content = node.get("content") or []

    if node_type == "bulletList":
        lines = []
        for child in content:
            value = _extract_adf_text(child).strip()
            if value:
                lines.append(f"- {value}")
        return "\n".join(lines) + ("\n" if lines else "")

    if node_type == "orderedList":
        lines = []
        for index, child in enumerate(content, start=1):
            value = _extract_adf_text(child).strip()
            if value:
                lines.append(f"{index}. {value}")
        return "\n".join(lines) + ("\n" if lines else "")

    if node_type == "listItem":
        return "\n".join(
            part.strip()
            for part in (_extract_adf_text(child) for child in content)
            if part.strip()
        )

    if node_type in {"tableRow"}:
        return " | ".join(
            _extract_adf_text(child).strip() for child in content
        ) + "\n"

    if node_type in {"tableCell", "tableHeader"}:
        return _extract_adf_text(content).strip()

    if node_type in {
        "paragraph",
        "heading",
        "blockquote",
        "codeBlock",
        "panel",
        "taskItem",
    }:
        return _extract_adf_text(content).strip() + "\n"

    # Generic fallback supports future/less common ADF nodes without failing.
    return _extract_adf_text(content)


def _description_to_text(description: Any) -> str:
    """Handle null, plain-string, and ADF descriptions consistently."""
    raw_text = _extract_adf_text(description)
    lines = [re.sub(r"[ \t]+", " ", line).strip() for line in raw_text.splitlines()]
    # Remove redundant blank lines while preserving paragraph/list boundaries.
    cleaned: list[str] = []
    previous_blank = False
    for line in lines:
        if not line:
            if cleaned and not previous_blank:
                cleaned.append("")
            previous_blank = True
        else:
            cleaned.append(line)
            previous_blank = False
    return "\n".join(cleaned).strip()


def _field_name(value: Any) -> str | None:
    """Return the name of a Jira object such as status, priority, or issue type."""
    if not isinstance(value, dict):
        return None
    name = value.get("name")
    return str(name).strip() if name is not None and str(name).strip() else None


def _transform_issue(
    issue: dict[str, Any],
    base_url: str,
    fallback_project_key: str,
    max_embed_chars: int,
    embedding_model: str,
) -> dict[str, Any]:
    """Transform Jira's raw API issue into the app's Cosmos document shape."""
    jira_id = str(issue.get("id") or "").strip()
    issue_key = str(issue.get("key") or "").strip()
    fields = issue.get("fields") or {}

    if not jira_id or not issue_key:
        raise ValueError("A Jira issue did not contain both 'id' and 'key'.")

    summary = str(fields.get("summary") or "").strip()
    description = _description_to_text(fields.get("description"))
    issue_type_object = fields.get("issuetype") or {}
    issue_type = _field_name(issue_type_object) or "Unknown"
    project = fields.get("project") or {}
    status = _field_name(fields.get("status"))
    priority = _field_name(fields.get("priority"))
    assignee = fields.get("assignee") or {}
    assignee_display_name = assignee.get("displayName") if isinstance(assignee, dict) else None
    labels = fields.get("labels") or []
    components = fields.get("components") or []
    parent = fields.get("parent") or {}
    parent_fields = (parent.get("fields") or {}) if isinstance(parent, dict) else {}

    label_names = sorted({str(label).strip() for label in labels if str(label).strip()})
    component_names = sorted(
        {
            str(component.get("name")).strip()
            for component in components
            if isinstance(component, dict) and component.get("name")
        }
    )

    parent_key = parent.get("key") if isinstance(parent, dict) else None
    parent_id = parent.get("id") if isinstance(parent, dict) else None
    parent_summary = parent_fields.get("summary") if isinstance(parent_fields, dict) else None
    project_key = str(project.get("key") or fallback_project_key).strip()
    project_name = project.get("name")

    # The embedding text contains relatively stable semantic content. We keep
    # status/priority/assignee in the retrieved text and metadata, but exclude
    # them from the embedding hash so metadata-only changes don't cost a new
    # embedding request.
    embedding_sections = [
        f"Issue key: {issue_key}",
        f"Project: {project_key}",
        f"Summary: {summary or '(no summary)'}",
        f"Issue type: {issue_type}",
    ]
    if description:
        embedding_sections.append(f"Description:\n{description}")
    if label_names:
        embedding_sections.append(f"Labels: {', '.join(label_names)}")
    if component_names:
        embedding_sections.append(f"Components: {', '.join(component_names)}")
    if parent_key:
        parent_line = f"Parent issue: {parent_key}"
        if parent_summary:
            parent_line += f" — {parent_summary}"
        embedding_sections.append(parent_line)
    semantic_text = "\n".join(embedding_sections).strip()

    # Keep the embedding input below a configurable character limit. The full
    # readable semantic text is still retained in the Cosmos document.
    if len(semantic_text) > max_embed_chars:
        marker = "\n[Content truncated for embedding]"
        embedding_text = semantic_text[: max(0, max_embed_chars - len(marker))].rstrip() + marker
    else:
        embedding_text = semantic_text

    context_sections = [
        semantic_text,
        f"Status: {status or 'Unspecified'}",
        f"Priority: {priority or 'Unspecified'}",
        f"Assignee: {assignee_display_name or 'Unassigned'}",
    ]
    text_for_retrieval = "\n".join(section for section in context_sections if section).strip()
    content_hash = hashlib.sha256(embedding_text.encode("utf-8")).hexdigest()

    return {
        "id": f"jira:{jira_id}",
        "data_source": "jira_issues",
        "content_type": "jira_issue",
        "jira_id": jira_id,
        "issue_key": issue_key,
        "project_key": project_key,
        "project_name": project_name,
        "summary": summary,
        "description": description,
        "issue_type": issue_type,
        "issue_type_id": str(issue_type_object.get("id")) if issue_type_object.get("id") is not None else None,
        "is_subtask": issue_type_object.get("subtask"),
        "status": status,
        "priority": priority,
        "assignee_display_name": assignee_display_name,
        "labels": label_names,
        "components": component_names,
        "parent_issue_id": str(parent_id) if parent_id is not None else None,
        "parent_issue_key": parent_key,
        "parent_summary": parent_summary,
        "jira_created_at": fields.get("created"),
        "jira_updated_at": fields.get("updated"),
        "jira_url": f"{base_url}/browse/{issue_key}",
        "text": text_for_retrieval,
        "embedding_text": embedding_text,
        "content_hash": content_hash,
        "embedding_model": embedding_model,
        "embedding_dimensions": EMBEDDING_DIMENSIONS,
    }


async def _read_checkpoint(container: Any, project_key: str) -> dict[str, Any] | None:
    """Read the incremental-sync checkpoint stored in the same Cosmos container."""
    checkpoint_id = f"sync:jira:{project_key}"
    try:
        return await container.read_item(item=checkpoint_id, partition_key=checkpoint_id)
    except CosmosResourceNotFoundError:
        return None


async def _read_existing_issue(container: Any, document_id: str) -> dict[str, Any] | None:
    try:
        return await container.read_item(item=document_id, partition_key=document_id)
    except CosmosResourceNotFoundError:
        return None


def _deduplicate_issues(issues: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Avoid duplicate work if a search page boundary repeats an issue."""
    unique: dict[str, dict[str, Any]] = {}
    for issue in issues:
        issue_id = str(issue.get("id") or "").strip()
        if issue_id:
            unique[issue_id] = issue
    return list(unique.values())

async def _reconcile_jira_issue_documents(
    client: httpx.AsyncClient,
    config: dict[str, Any],
    container: Any,
    project_key: str,
) -> dict[str, Any]:
    """Remove Cosmos Jira documents absent from the current project in Jira.

    Fetches the complete current issue list from Jira, compares its issue IDs
    against Cosmos documents belonging to the configured project, and deletes
    only stale Jira issue documents.

    If Jira returns an empty project result, deletion is skipped as a safety
    measure. A failed or incomplete Jira scan raises an exception, preventing
    the sync checkpoint from advancing.
    """
    jql = f'project = "{project_key}" ORDER BY key ASC'

    live_issues, pages = await _fetch_all_issues(
        client,
        config,
        jql,
        fields=["summary"],
    )

    # A successful but unexpectedly empty response must not delete every
    # stored issue. The sync account might have lost project visibility.
    if not live_issues:
        print(
            "WARNING: Jira returned zero issues during deletion reconciliation. "
            "Skipping deletion to protect existing Cosmos documents."
        )
        return {
            "reconciliation_pages": pages,
            "live_issues_found": 0,
            "stale_documents_removed": 0,
            "reconciliation_performed": False,
        }

    # Jira IDs are stable even if an issue key changes.
    live_document_ids: set[str] = set()

    for issue in live_issues:
        jira_id = str(issue.get("id") or "").strip()
        if not jira_id:
            raise RuntimeError(
                "Jira returned an issue without an ID. "
                "Deletion reconciliation was aborted."
            )
        live_document_ids.add(f"jira:{jira_id}")

    # Query only this project's Jira issue documents. Meeting history,
    # summaries, transcript chunks and sync checkpoints are excluded.
    query = """
        SELECT c.id
        FROM c
        WHERE c.data_source = @source
          AND c.content_type = @content_type
          AND c.project_key = @project_key
    """

    parameters = [
        {"name": "@source", "value": "jira_issues"},
        {"name": "@content_type", "value": "jira_issue"},
        {"name": "@project_key", "value": project_key},
    ]

    results = container.query_items(
        query=query,
        parameters=parameters,
    )

    existing_document_ids: set[str] = set()

    async for item in results:
        document_id = item.get("id")
        if isinstance(document_id, str) and document_id:
            existing_document_ids.add(document_id)

    stale_document_ids = sorted(existing_document_ids - live_document_ids)

    removed = 0

    for document_id in stale_document_ids:
        try:
            # The container partition key is /id.
            await container.delete_item(
                item=document_id,
                partition_key=document_id,
            )
            removed += 1
            print(f"Removed stale Jira document: {document_id}")

        except CosmosResourceNotFoundError:
            # Another process may already have removed this document.
            continue

    print(
        "Jira deletion reconciliation completed: "
        f"{len(live_document_ids)} current Jira issues, "
        f"{len(existing_document_ids)} indexed Jira documents, "
        f"{removed} stale documents removed."
    )

    return {
        "reconciliation_pages": pages,
        "live_issues_found": len(live_document_ids),
        "stale_documents_removed": removed,
        "reconciliation_performed": True,
    }


async def sync_jira_to_cosmos(state: State, *, force_full: bool = False) -> dict[str, Any]:
    """Run one Jira sync.

    On first run, fetch the whole configured project. On later runs, fetch issues
    updated since the last successful watermark minus an overlap window. The
    checkpoint advances only after every issue has been transformed, embedded if
    needed, and upserted successfully.
    """
    config = _load_config(state)
    project_key = config["project_key"]
    sync_started = datetime.now(timezone.utc)
    checkpoint_id = f"sync:jira:{project_key}"
    #client = _make_jira_client(config)
    #cosmos_client = None
    client: httpx.AsyncClient | None = None

    try:
        client = _make_jira_client(config)
        #cosmos_client, container = _cosmos_container()

        # Reuse the Cosmos container initialized by config.py.
        container = state.cosmos_container
        vector_field = COSMOS_VECTOR_FIELD
        checkpoint = await _read_checkpoint(container, project_key)

        full_sync = force_full or checkpoint is None or await cache_is_empty(state)
        if full_sync:
            jql = f'project = "{project_key}" ORDER BY updated ASC, key ASC'
            mode = "full" if force_full or checkpoint is not None else "initial_full"
            print(f"Starting {mode} Jira sync for project {project_key}.")
        else:
            watermark_raw = checkpoint.get("sync_watermark_utc")
            if not watermark_raw:
                raise RuntimeError(
                    f"Checkpoint {checkpoint_id!r} has no sync_watermark_utc. "
                    "Inspect or repair the checkpoint before running an incremental sync."
                )
            try:
                watermark = datetime.fromisoformat(str(watermark_raw).replace("Z", "+00:00"))
            except ValueError as exc:
                raise RuntimeError(
                    f"Checkpoint contains an invalid sync_watermark_utc: {watermark_raw!r}"
                ) from exc
            if watermark.tzinfo is None:
                watermark = watermark.replace(tzinfo=timezone.utc)
            watermark = watermark.astimezone(timezone.utc)
            since = watermark - timedelta(minutes=config["overlap_minutes"])
            jql_time = _jql_timestamp(since, config["jira_timezone"])
            jql = (
                f'project = "{project_key}" AND updated >= "{jql_time}" '
                "ORDER BY updated ASC, key ASC"
            )
            mode = "incremental"
            print(
                f"Starting incremental Jira sync for {project_key}; "
                f"checking updates since {jql_time} ({config['timezone_name']}, "
                f"including a {config['overlap_minutes']}-minute overlap)."
            )

        raw_issues, page_count = await _fetch_all_issues(client, config, jql)
        issues = _deduplicate_issues(raw_issues)
        print(f"Fetched {len(raw_issues)} issue records; {len(issues)} unique issues to process.")

        transformed: list[dict[str, Any]] = []
        new_documents = 0
        changed_content = 0
        vectors_reused = 0
        embedding_queue: list[dict[str, Any]] = []

        for issue in issues:
            document = _transform_issue(
                issue, config["base_url"], project_key, config["max_embed_chars"], config["embedding_model"]
            )
            existing = await _read_existing_issue(container, document["id"])

            existing_vector = existing.get(vector_field) if existing else None
            can_reuse_vector = (
                existing is not None
                and existing.get("content_hash") == document["content_hash"]
                and existing.get("embedding_model") == config["embedding_model"]
                and isinstance(existing_vector, list)
                and len(existing_vector) == EMBEDDING_DIMENSIONS
            )

            if can_reuse_vector:
                document[vector_field] = existing_vector
                vectors_reused += 1
            else:
                if existing is None:
                    new_documents += 1
                else:
                    changed_content += 1
                embedding_queue.append(document)
            transformed.append(document)

        if embedding_queue:
            print(
                f"Generating {len(embedding_queue)} embeddings with deployment "
                f"'{config["embedding_model"]}'..."
            )
            vectors = await _embed_texts([document["embedding_text"] for document in embedding_queue], state)
            if len(vectors) != len(embedding_queue):
                raise RuntimeError(
                    f"Embedding helper returned {len(vectors)} vectors for "
                    f"{len(embedding_queue)} Jira documents."
                )
            for document, vector in zip(embedding_queue, vectors, strict=True):
                if len(vector) != EMBEDDING_DIMENSIONS:
                    raise ValueError(
                        f"Embedding for {document['issue_key']} has {len(vector)} dimensions; "
                        f"expected {EMBEDDING_DIMENSIONS}."
                    )
                document[vector_field] = vector

        # Stable IDs mean retries update the same documents rather than duplicate them.
        # If a later write fails, the checkpoint is not advanced; retrying is safe.
        for document in transformed:
            document["synced_at_utc"] = sync_started.isoformat()
            await container.upsert_item(document)

        # Reconcile deletions on every run, including incremental syncs.
        # This is necessary because deleted Jira issues cannot be found by the
        # "updated since checkpoint" JQL query.
        reconciliation_result = await _reconcile_jira_issue_documents(
            client,
            config,
            container,
            project_key,
        )
        # Done before the checkpoint, so if this fails the next run retries these issues.
        cache_rows = await upsert_issues(transformed, sync_started, state)

        # Use the run's start time as the watermark. Any issues changed during the
        # current scan will be included on the next run (also protected by overlap).
        sync_completed = datetime.now(timezone.utc)
        new_checkpoint = {
            "id": checkpoint_id,
            "data_source": CHECKPOINT_DATA_SOURCE,
            "content_type": CHECKPOINT_CONTENT_TYPE,
            "project_key": project_key,
            "sync_mode": mode,
            "sync_watermark_utc": sync_started.isoformat(),
            "last_successful_run_completed_at_utc": sync_completed.isoformat(),
            "last_run_issue_count": len(issues),
            "last_run_pages": page_count,
            "updated_at_utc": sync_completed.isoformat(),
        }
        await container.upsert_item(new_checkpoint)

        result = {
            "project_key": project_key,
            "mode": mode,
            "pages_fetched": page_count,
            "issues_fetched": len(raw_issues),
            "unique_issues_processed": len(issues),
            "new_documents": new_documents,
            "documents_with_changed_semantic_content": changed_content,
            "vectors_reused": vectors_reused,
            "embeddings_generated": len(embedding_queue),
            "documents_upserted": len(transformed),
            "cache_rows_upserted": cache_rows,
            "vector_field_used": f"/{vector_field}",
            "embedding_model": config["embedding_model"],
            "embedding_dimensions": EMBEDDING_DIMENSIONS,
            "checkpoint_id": checkpoint_id,
            "sync_watermark_utc": sync_started.isoformat(),
            "reconciliation_pages": reconciliation_result["reconciliation_pages"],
            "live_issues_found": reconciliation_result["live_issues_found"],
            "stale_documents_removed": reconciliation_result["stale_documents_removed"],
            "reconciliation_performed": reconciliation_result["reconciliation_performed"],
        }

        print("\nJira sync completed successfully:")
        for name, value in result.items():
            print(f"  {name}: {value}")
        return result

    finally:
        if client is not None:
            await client.aclose()


async def _run_cli(force_full: bool) -> None:
    """Initialize shared application resources and run Jira synchronization."""
    app = FastAPI()

    async with lifespan(app):
        await sync_jira_to_cosmos(
            app.state,
            force_full=force_full,
        )


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Import or incrementally sync Jira issues to Cosmos DB."
    )
    parser.add_argument(
        "--full",
        action="store_true",
        help="Fetch the entire configured Jira project instead of using the checkpoint.",
    )
    args = parser.parse_args()

    asyncio.run(_run_cli(force_full=args.full))


if __name__ == "__main__":
    main()


'''
def main() -> None:
    parser = argparse.ArgumentParser(
        description="Import or incrementally sync Jira issues to Cosmos DB."
    )
    parser.add_argument(
        "--full",
        action="store_true",
        help="Fetch the entire configured Jira project instead of using the checkpoint.",
    )
    args = parser.parse_args()
    sync_jira_to_cosmos(force_full=args.full)


if __name__ == "__main__":
    main()
'''