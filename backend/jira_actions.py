"""Jira actions and Cosmos DB retrieval helpers for Ticket2Go.

All functions are async and use the clients/configuration initialized by
backend.config.lifespan through FastAPI's app.state.

Important: keep create/update/transition functions behind the application's
approval-validated backend flow. Do not expose write functions directly to an
LLM without an independent approval check.
"""

from __future__ import annotations

from datetime import date, datetime, time, timezone
from typing import Any, Sequence
from urllib.parse import quote

import httpx
from starlette.datastructures import State

from backend.meeting_ingestion import EMBEDDING_DIMENSIONS, _embed_texts

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
    "duedate",
    "parent",
]

ALLOWED_COSMOS_SOURCES = {"meeting_history", "jira_issues"}
ALLOWED_COSMOS_CONTENT_TYPES = {
    "transcript_chunk",
    "meeting_summary",
    "jira_issue",
}


class JiraActionError(RuntimeError):
    """Jira request or response failed."""


# Retrieves a required configuration value from app.state and validates that it is a non-empty string.
def _required_state_value(state: State, name: str) -> str:
    value = getattr(state, name, None)
    if not isinstance(value, str) or not value.strip():
        raise ValueError(f"Missing required application configuration: {name}")
    return value.strip()


# Creates an authenticated asynchronous Jira client using the shared application configuration.
def _jira_client(state: State) -> httpx.AsyncClient:
    """Create a short-lived Jira client using the shared Key Vault config."""
    base_url = _required_state_value(state, "jira_base_url").rstrip("/")
    email = _required_state_value(state, "jira_email")
    token = _required_state_value(state, "jira_api_token")

    return httpx.AsyncClient(
        base_url=base_url,
        auth=(email, token),
        headers={"Accept": "application/json", "Content-Type": "application/json"},
        timeout=httpx.Timeout(30.0),
    )

# Sends authenticated requests to Jira and handles API errors and responses consistently.
async def _jira_request(
    state: State,
    method: str,
    path: str,
    *,
    json_body: dict[str, Any] | None = None,
    params: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Perform one Jira API request without retrying potentially non-idempotent writes."""
    async with _jira_client(state) as client:
        try:
            response = await client.request(
                method,
                f"/rest/api/3/{path.lstrip('/')}",
                json=json_body,
                params=params,
            )
        except httpx.TimeoutException as exc:
            raise JiraActionError(
                "The Jira request timed out. For write operations, check Jira before retrying "
                "because the operation may have succeeded despite the timeout."
            ) from exc
        except httpx.RequestError as exc:
            raise JiraActionError("Could not connect to Jira. Check the Jira URL and network.") from exc

    if not response.is_success:
        try:
            body = response.json()
            detail = body.get("errorMessages") or body.get("errors") or body
        except ValueError:
            detail = response.text[:1500]
        raise JiraActionError(f"Jira API returned HTTP {response.status_code}: {detail}")

    if response.status_code == 204 or not response.content:
        return {}

    try:
        result = response.json()
    except ValueError as exc:
        raise JiraActionError("Jira returned a successful response that was not valid JSON.") from exc

    if not isinstance(result, dict):
        raise JiraActionError("Jira returned an unexpected response format.")
    return result

# Converts Jira's structured Atlassian Document Format into readable plain text.
def _adf_to_text(node: Any) -> str:
    """Convert Jira's Atlassian Document Format description to readable text."""
    if node is None:
        return ""
    if isinstance(node, str):
        return node
    if isinstance(node, list):
        return "".join(_adf_to_text(part) for part in node)
    if not isinstance(node, dict):
        return ""

    node_type = node.get("type")
    if node_type == "text":
        return str(node.get("text", ""))
    if node_type == "hardBreak":
        return "\n"

    children = node.get("content") or []
    if node_type in {"bulletList", "orderedList"}:
        lines = []
        for index, child in enumerate(children, start=1):
            text = _adf_to_text(child).strip()
            if text:
                prefix = f"{index}. " if node_type == "orderedList" else "- "
                lines.append(prefix + text)
        return "\n".join(lines) + ("\n" if lines else "")
    if node_type == "listItem":
        return "\n".join(
            text for text in (_adf_to_text(child).strip() for child in children) if text
        )
    if node_type in {"paragraph", "heading", "blockquote", "codeBlock", "panel"}:
        return _adf_to_text(children).strip() + "\n"
    if node_type == "tableRow":
        return " | ".join(_adf_to_text(child).strip() for child in children) + "\n"
    if node_type in {"tableCell", "tableHeader"}:
        return _adf_to_text(children).strip()
    return _adf_to_text(children)


# Converts a Jira description to clean, readable text and normalizes whitespace.
def _description_text(value: Any) -> str:
    return "\n".join(line.strip() for line in _adf_to_text(value).splitlines()).strip()


# Converts a plain-text description into the ADF format required by Jira.
def _plain_text_to_adf(value: str) -> dict[str, Any]:
    """Convert plain text to ADF paragraphs for Jira REST API v3."""
    paragraphs = value.splitlines() or [""]
    return {
        "type": "doc",
        "version": 1,
        "content": [
            {
                "type": "paragraph",
                "content": ([{"type": "text", "text": line}] if line else []),
            }
            for line in paragraphs
        ],
    }

# Converts a Jira field name or ID into the object format expected by the API.
def _name_or_id(value: str, field_name: str) -> dict[str, str]:
    if not isinstance(value, str) or not value.strip():
        raise ValueError(f"{field_name} must be a non-empty name or ID.")
    value = value.strip()
    return {"id": value} if value.isdigit() else {"name": value}


# Transforms a raw Jira issue response into a normalized dictionary of useful ticket information.
def _normalize_issue(issue: dict[str, Any], base_url: str) -> dict[str, Any]:
    """Return useful issue fields without including the raw embedding or API payload."""
    fields = issue.get("fields") or {}
    issue_type = fields.get("issuetype") or {}
    project = fields.get("project") or {}
    status = fields.get("status") or {}
    priority = fields.get("priority") or {}
    assignee = fields.get("assignee") or {}
    parent = fields.get("parent") or {}
    parent_fields = parent.get("fields") or {}
    components = fields.get("components") or []

    key = issue.get("key")
    return {
        "id": str(issue.get("id")) if issue.get("id") is not None else None,
        "key": key,
        "url": f"{base_url.rstrip('/')}/browse/{key}" if key else issue.get("self"),
        "summary": fields.get("summary"),
        "description": _description_text(fields.get("description")),
        "issue_type": issue_type.get("name"),
        "issue_type_id": str(issue_type["id"]) if issue_type.get("id") is not None else None,
        "is_subtask": issue_type.get("subtask"),
        "project_key": project.get("key"),
        "project_name": project.get("name"),
        "status": status.get("name"),
        "priority": priority.get("name"),
        "assignee_display_name": assignee.get("displayName"),
        "assignee_account_id": assignee.get("accountId"),
        "labels": fields.get("labels") or [],
        "components": [item.get("name") for item in components if isinstance(item, dict)],
        "parent_issue_key": parent.get("key"),
        "parent_summary": parent_fields.get("summary"),
        "due_date": fields.get("duedate"),
        "created": fields.get("created"),
        "updated": fields.get("updated"),
    }


# Validates and cleans a list of non-empty strings for Jira fields such as labels and components.
def _string_list(values: Sequence[str], field_name: str) -> list[str]:
    if isinstance(values, (str, bytes)):
        raise ValueError(f"{field_name} must be a list of strings, not a single string.")
    result = []
    for value in values:
        if not isinstance(value, str) or not value.strip():
            raise ValueError(f"Every {field_name} value must be a non-empty string.")
        result.append(value.strip())
    return result


# Searches Jira issues using JQL and returns normalized results with pagination information.
async def search_jira_issues(
    state: State,
    jql: str,
    *,
    max_results: int = 10,
    next_page_token: str | None = None,
) -> dict[str, Any]:
    """Search live Jira issues using JQL; returns a page and its pagination token."""
    if not jql or not jql.strip():
        raise ValueError("jql must not be empty.")
    if not 1 <= max_results <= 100:
        raise ValueError("max_results must be between 1 and 100.")

    payload: dict[str, Any] = {
        "jql": jql.strip(),
        "fields": JIRA_FIELDS,
        "maxResults": max_results,
    }
    if next_page_token:
        payload["nextPageToken"] = next_page_token

    result = await _jira_request(state, "POST", "search/jql", json_body=payload)
    issues = result.get("issues") or []
    base_url = _required_state_value(state, "jira_base_url")
    return {
        "issues": [_normalize_issue(issue, base_url) for issue in issues],
        "next_page_token": result.get("nextPageToken"),
        "is_last": result.get("isLast", not bool(result.get("nextPageToken"))),
    }


# Retrieves the latest details of a specific Jira issue and returns them in a normalized format.
async def get_jira_issue(state: State, issue_key: str) -> dict[str, Any]:
    """Fetch the latest field values for one Jira issue by key or ID."""
    if not issue_key or not issue_key.strip():
        raise ValueError("issue_key must not be empty.")
    encoded_key = quote(issue_key.strip(), safe="")
    result = await _jira_request(
        state,
        "GET",
        f"issue/{encoded_key}",
        params={"fields": ",".join(JIRA_FIELDS)},
    )
    return _normalize_issue(result, _required_state_value(state, "jira_base_url"))


# Retrieves a Jira issue's field-change history, with optional date filtering.
async def get_jira_issue_changelog(
    state: State,
    issue_key: str,
    *,
    since: str | date | None = None,
    max_changes: int = 100,
) -> list[dict[str, Any]]:
    """Return recent field-level changes for a Jira issue.

    Args:
        state: Application state containing Jira configuration.
        issue_key: Jira issue key or ID.
        since: Optional date (YYYY-MM-DD) or ISO datetime. Only changes
            at or after this date/time are returned.
        max_changes: Maximum number of field changes to return (1–100).

    Returns:
        A list containing the issue key, timestamp, author, field,
        previous value, and new value for each change.
    """
    if not issue_key or not issue_key.strip():
        raise ValueError("issue_key must not be empty.")
    if not 1 <= max_changes <= 100:
        raise ValueError("max_changes must be between 1 and 100.")

    encoded_key = quote(issue_key.strip(), safe="")
    page_size = 100

    # Parse the optional cutoff as UTC.
    since_datetime: datetime | None = None

    if isinstance(since, datetime):
        since_datetime = since
    elif isinstance(since, date):
        since_datetime = datetime.combine(
            since, time.min, tzinfo=timezone.utc
        )
    elif isinstance(since, str):
        value = since.strip()
        if not value:
            raise ValueError("since must not be empty when provided.")

        try:
            if len(value) == 10:
                since_datetime = datetime.combine(
                    date.fromisoformat(value),
                    time.min,
                    tzinfo=timezone.utc,
                )
            else:
                since_datetime = datetime.fromisoformat(
                    value.replace("Z", "+00:00")
                )
        except ValueError as exc:
            raise ValueError(
                "since must be a YYYY-MM-DD date or an ISO datetime."
            ) from exc
    elif since is not None:
        raise ValueError("since must be a string, date, or None.")

    if since_datetime is not None:
        if since_datetime.tzinfo is None:
            since_datetime = since_datetime.replace(tzinfo=timezone.utc)
        else:
            since_datetime = since_datetime.astimezone(timezone.utc)

    # Parse Jira timestamps into comparable UTC datetimes.
    def parse_timestamp(value: Any) -> datetime | None:
        if not isinstance(value, str) or not value:
            return None
        try:
            parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
        except ValueError:
            return None

        if parsed.tzinfo is None:
            parsed = parsed.replace(tzinfo=timezone.utc)
        return parsed.astimezone(timezone.utc)

    # Convert each changelog entry into one record per changed field.
    def flatten_entries(entries: list[dict[str, Any]]) -> list[dict[str, Any]]:
        flattened: list[dict[str, Any]] = []

        for entry in entries:
            changed_at = entry.get("created")
            changed_datetime = parse_timestamp(changed_at)

            if since_datetime is not None:
                if changed_datetime is None or changed_datetime < since_datetime:
                    continue

            author = entry.get("author") or {}

            for item in entry.get("items") or []:
                flattened.append(
                    {
                        "issue_key": issue_key.strip(),
                        "changelog_id": entry.get("id"),
                        "changed_at": changed_at,
                        "author": author.get("displayName"),
                        "field": item.get("field") or item.get("fieldId"),
                        "from_value": (
                            item.get("fromString")
                            if item.get("fromString") is not None
                            else item.get("from")
                        ),
                        "to_value": (
                            item.get("toString")
                            if item.get("toString") is not None
                            else item.get("to")
                        ),
                    }
                )

        return flattened

    # Get the first page to discover how many changelog entries exist.
    first_page = await _jira_request(
        state,
        "GET",
        f"issue/{encoded_key}/changelog",
        params={"startAt": 0, "maxResults": page_size},
    )
    first_entries = first_page.get("values") or []
    total = first_page.get("total")

    changes: list[dict[str, Any]] = []

    if since_datetime is None and isinstance(total, int) and total > len(first_entries):
        # Without a cutoff date, start with the newest history instead of
        # downloading old changes that are unlikely to be relevant.
        end = total

        while end > 0 and len(changes) < max_changes:
            start = max(0, end - page_size)
            page = await _jira_request(
                state,
                "GET",
                f"issue/{encoded_key}/changelog",
                params={
                    "startAt": start,
                    "maxResults": end - start,
                },
            )
            entries = page.get("values") or []

            # Pages are retrieved newest-first; prepend to retain chronological order.
            changes = flatten_entries(entries) + changes
            end = start

            if not entries:
                break

            changes = changes[-max_changes:]
    else:
        # With a cutoff date, inspect all pages so older entries can be
        # filtered correctly and the most recent matching changes retained.
        page = first_page
        start = 0

        while True:
            entries = page.get("values") or []
            changes.extend(flatten_entries(entries))
            changes = changes[-max_changes:]

            if page.get("isLast") is True or not entries:
                break

            next_start = int(page.get("startAt", start)) + len(entries)
            page_total = page.get("total")

            if isinstance(page_total, int) and next_start >= page_total:
                break
            if len(entries) < page_size and not isinstance(page_total, int):
                break

            start = next_start
            page = await _jira_request(
                state,
                "GET",
                f"issue/{encoded_key}/changelog",
                params={"startAt": start, "maxResults": page_size},
            )

    return changes[-max_changes:]


# Retrieves the issue types available for creating tickets in the configured Jira project.
async def get_project_issue_types(state: State) -> list[dict[str, Any]]:
    """Get issue types available for creating issues in the configured Jira project."""
    project_key = _required_state_value(state, "jira_project_key")
    result = await _jira_request(
        state,
        "GET",
        f"issue/createmeta/{quote(project_key, safe='')}/issuetypes",
    )
    return result.get("issueTypes") or []


# Retrieves the available and required creation fields for a specific Jira issue type.
async def get_project_issue_type_fields(
    state: State,
    issue_type_id: str,
) -> list[dict[str, Any]]:
    """Get create-screen fields and required-field metadata for an issue type."""
    if not issue_type_id or not issue_type_id.strip():
        raise ValueError("issue_type_id must not be empty.")
    project_key = _required_state_value(state, "jira_project_key")
    result = await _jira_request(
        state,
        "GET",
        f"issue/createmeta/{quote(project_key, safe='')}/issuetypes/{quote(issue_type_id.strip(), safe='')}",
    )
    return result.get("fields") or []


# Retrieves the editable fields and allowed values for a specific Jira issue.
async def get_jira_issue_edit_metadata(
    state: State,
    issue_key: str,
) -> dict[str, Any]:
    """Get the fields and allowed values editable on one Jira issue."""
    if not issue_key or not issue_key.strip():
        raise ValueError("issue_key must not be empty.")
    encoded_key = quote(issue_key.strip(), safe="")
    result = await _jira_request(state, "GET", f"issue/{encoded_key}/editmeta")
    return result.get("fields") or {}


# Verifies that a Jira issue belongs to the configured project before allowing it to be modified.
async def _require_issue_in_configured_project(state: State, issue_key: str) -> dict[str, Any]:
    """Ensure writes only target an issue in the configured Jira project."""
    issue = await get_jira_issue(state, issue_key)
    configured_project = _required_state_value(state, "jira_project_key")
    if issue.get("project_key") != configured_project:
        raise ValueError(
            f"Issue {issue_key} belongs to project {issue.get('project_key')!r}, "
            f"not configured project {configured_project!r}."
        )
    return issue


# Creates a Jira issue with the supplied fields and returns its ID, key, and URL.
async def create_jira_issue(
    state: State,
    *,
    summary: str,
    issue_type: str,
    description: str | None = None,
    priority: str | None = None,
    assignee_account_id: str | None = None,
    labels: Sequence[str] | None = None,
    components: Sequence[str] | None = None,
    due_date: str | None = None,
    parent_issue_key: str | None = None,
) -> dict[str, Any]:
    """Create an issue in the configured Jira project and return its ID/key/URL.

    Call this only after the backend has validated that the user approved the
    exact create action. `issue_type` and `priority` may be a Jira name or ID.
    """
    if not isinstance(summary, str) or not summary.strip():
        raise ValueError("summary must not be empty.")

    project_key = _required_state_value(state, "jira_project_key")
    fields: dict[str, Any] = {
        "project": {"key": project_key},
        "issuetype": _name_or_id(issue_type, "issue_type"),
        "summary": summary.strip(),
    }
    if description is not None:
        fields["description"] = _plain_text_to_adf(description)
    if priority is not None:
        fields["priority"] = _name_or_id(priority, "priority")
    if assignee_account_id is not None:
        if not assignee_account_id.strip():
            raise ValueError("assignee_account_id must be a non-empty account ID or None.")
        fields["assignee"] = {"accountId": assignee_account_id.strip()}
    if labels is not None:
        fields["labels"] = _string_list(labels, "labels")
    if components is not None:
        fields["components"] = [
            {"name": name} for name in _string_list(components, "components")
        ]
    if due_date is not None:
        date.fromisoformat(due_date)
        fields["duedate"] = due_date
    if parent_issue_key is not None:
        if not parent_issue_key.strip():
            raise ValueError("parent_issue_key must be a non-empty issue key.")
        fields["parent"] = {"key": parent_issue_key.strip()}

    result = await _jira_request(state, "POST", "issue", json_body={"fields": fields})
    issue_id = result.get("id")
    issue_key = result.get("key")
    if not issue_id or not issue_key:
        raise JiraActionError("Jira reported success but did not return an issue ID and key.")
    base_url = _required_state_value(state, "jira_base_url")
    return {
        "id": str(issue_id),
        "key": issue_key,
        "url": f"{base_url.rstrip('/')}/browse/{issue_key}",
    }


# Updates approved fields on an existing Jira issue without changing unspecified fields.
async def update_jira_issue(
    state: State,
    issue_key: str,
    *,
    fields: dict[str, Any],
) -> dict[str, Any]:
    """Update explicitly supplied, supported fields on an existing issue.

    Supported input keys: summary, description, issue_type, priority,
    assignee_account_id, labels, components, due_date. Omitted fields are
    left unchanged. Use assignee_account_id=None to explicitly unassign.
    Call only after backend approval validation.
    """
    if not issue_key or not issue_key.strip():
        raise ValueError("issue_key must not be empty.")
    if not isinstance(fields, dict) or not fields:
        raise ValueError("fields must be a non-empty dictionary of approved changes.")

    field_mapping = {
        "summary": "summary",
        "description": "description",
        "issue_type": "issuetype",
        "priority": "priority",
        "assignee_account_id": "assignee",
        "labels": "labels",
        "components": "components",
        "due_date": "duedate",
    }
    unsupported = set(fields) - set(field_mapping)
    if unsupported:
        raise ValueError(f"Unsupported Jira update fields: {', '.join(sorted(unsupported))}")

    jira_fields: dict[str, Any] = {}
    for name, value in fields.items():
        if name == "summary":
            if not isinstance(value, str) or not value.strip():
                raise ValueError("summary must be a non-empty string.")
            jira_fields["summary"] = value.strip()
        elif name == "description":
            if value is None:
                jira_fields["description"] = None
            elif isinstance(value, str):
                jira_fields["description"] = _plain_text_to_adf(value)
            else:
                raise ValueError("description must be a string or None.")
        elif name == "issue_type":
            jira_fields["issuetype"] = _name_or_id(value, "issue_type")
        elif name == "priority":
            jira_fields["priority"] = _name_or_id(value, "priority")
        elif name == "assignee_account_id":
            if value is None:
                jira_fields["assignee"] = None
            elif isinstance(value, str) and value.strip():
                jira_fields["assignee"] = {"accountId": value.strip()}
            else:
                raise ValueError("assignee_account_id must be a non-empty string or None.")
        elif name == "labels":
            jira_fields["labels"] = _string_list(value, "labels")
        elif name == "components":
            jira_fields["components"] = [
                {"name": component} for component in _string_list(value, "components")
            ]
        elif name == "due_date":
            if value is None:
                jira_fields["duedate"] = None
            elif isinstance(value, str):
                date.fromisoformat(value)
                jira_fields["duedate"] = value
            else:
                raise ValueError("due_date must be YYYY-MM-DD or None.")

    # Fetch the live issue and restrict writes to the configured project.
    await _require_issue_in_configured_project(state, issue_key.strip())
    encoded_key = quote(issue_key.strip(), safe="")
    await _jira_request(
        state,
        "PUT",
        f"issue/{encoded_key}",
        json_body={"fields": jira_fields},
    )
    return {"key": issue_key.strip(), "updated_fields": sorted(fields)}


# Retrieves the workflow transitions currently available for a Jira issue.
async def get_jira_transitions(state: State, issue_key: str) -> list[dict[str, Any]]:
    """Return workflow transitions available for the current user on an issue."""
    if not issue_key or not issue_key.strip():
        raise ValueError("issue_key must not be empty.")
    encoded_key = quote(issue_key.strip(), safe="")
    result = await _jira_request(state, "GET", f"issue/{encoded_key}/transitions")
    return [
        {
            "id": item.get("id"),
            "name": item.get("name"),
            "to_status": (item.get("to") or {}).get("name"),
        }
        for item in result.get("transitions", [])
    ]


# Changes a Jira issue's workflow status after verifying that the requested transition is available.
async def transition_jira_issue(
    state: State,
    issue_key: str,
    transition_id: str,
) -> dict[str, Any]:
    """Perform an available Jira workflow transition by its transition ID."""
    if not issue_key or not issue_key.strip():
        raise ValueError("issue_key must not be empty.")
    if not isinstance(transition_id, str) or not transition_id.strip():
        raise ValueError("transition_id must be a non-empty string.")
    await _require_issue_in_configured_project(state, issue_key.strip())
    available = await get_jira_transitions(state, issue_key.strip())
    if not any(str(item.get("id")) == transition_id.strip() for item in available):
        raise ValueError(
            f"Transition {transition_id!r} is not currently available for issue {issue_key}."
        )
    encoded_key = quote(issue_key.strip(), safe="")
    await _jira_request(
        state,
        "POST",
        f"issue/{encoded_key}/transitions",
        json_body={"transition": {"id": transition_id.strip()}},
    )
    return {"key": issue_key.strip(), "transition_id": transition_id.strip(), "success": True}


# Executes a parameterized Cosmos DB query and returns a limited list of documents.okay lets move on to the next function
async def _query_cosmos(
    state: State,
    query: str,
    parameters: list[dict[str, Any]],
    *,
    max_items: int = 20,
) -> list[dict[str, Any]]:
    if not 1 <= max_items <= 100:
        raise ValueError("max_items must be between 1 and 100.")
    container = state.cosmos_container
    results = container.query_items(
        query=query,
        parameters=parameters,
    )
    items: list[dict[str, Any]] = []
    async for item in results:
        items.append(item)
        if len(items) >= max_items:
            break
    return items


# Retrieves an indexed Jira issue from Cosmos DB by its Jira issue key.
async def get_cosmos_jira_issue(state: State, issue_key: str) -> dict[str, Any] | None:
    """Retrieve one indexed Jira issue from Cosmos DB by its Jira key."""
    if not issue_key or not issue_key.strip():
        raise ValueError("issue_key must not be empty.")
    results = await _query_cosmos(
        state,
        """
        SELECT TOP 1 c.id, c.data_source, c.content_type, c.jira_id,
            c.issue_key, c.project_key, c.project_name, c.summary, c.description,
            c.issue_type, c.status, c.priority, c.assignee_display_name,
            c.labels, c.components, c.parent_issue_key, c.parent_summary,
            c.jira_created_at, c.jira_updated_at, c.jira_url, c.text
        FROM c
        WHERE c.data_source = @source AND c.content_type = @type
            AND c.issue_key = @issue_key
        """,
        [
            {"name": "@source", "value": "jira_issues"},
            {"name": "@type", "value": "jira_issue"},
            {"name": "@issue_key", "value": issue_key.strip()},
        ],
        max_items=1,
    )
    return results[0] if results else None


# Retrieves transcript chunks and summary documents for a specific meeting from Cosmos DB.
async def get_meeting_history(
    state: State,
    meeting_id: str,
    *,
    max_items: int = 100,
) -> list[dict[str, Any]]:
    """Retrieve transcript chunks and summary for a single meeting from Cosmos DB."""
    if not meeting_id or not meeting_id.strip():
        raise ValueError("meeting_id must not be empty.")
    if not 1 <= max_items <= 100:
        raise ValueError("max_items must be between 1 and 100.")
    return await _query_cosmos(
        state,
        f"""
        SELECT TOP {max_items} c.id, c.data_source, c.content_type, c.meeting_id,
            c.meeting_title, c.meeting_date, c.chunk_index, c.text,
            c.source_blob_uri, c.created_at
        FROM c
        WHERE c.data_source = @source AND c.meeting_id = @meeting_id
        ORDER BY c.chunk_index ASC
        """,
        [
            {"name": "@source", "value": "meeting_history"},
            {"name": "@meeting_id", "value": meeting_id.strip()},
        ],
        max_items=max_items,
    )

# Retrieves the most recent meeting summaries from Cosmos DB, ordered by meeting date.
async def get_recent_meeting_summaries(
    state: State,
    *,
    limit: int = 10,
) -> list[dict[str, Any]]:
    """Retrieve recent meeting summaries, newest first."""
    if not 1 <= limit <= 100:
        raise ValueError("limit must be between 1 and 100.")
    return await _query_cosmos(
        state,
        f"""
        SELECT TOP {limit} c.id, c.meeting_id, c.meeting_title, c.meeting_date,
            c.text, c.source_blob_uri, c.created_at
        FROM c
        WHERE c.data_source = @source AND c.content_type = @type
        ORDER BY c.meeting_date DESC
        """,
        [
            {"name": "@source", "value": "meeting_history"},
            {"name": "@type", "value": "meeting_summary"},
        ],
        max_items=limit,
    )

async def get_recent_meetings(
    state: State,
    *,
    limit: int = 10,
) -> list[dict[str, Any]]:
    """List distinct recent meetings found in summaries or transcript chunks."""
    if not 1 <= limit <= 10:
        raise ValueError("limit must be between 1 and 10.")

    # Retrieve both record types. Multiple transcript chunks and a summary
    # must not cause the same meeting to be counted more than once.
    rows = await _query_cosmos(
        state,
        """
        SELECT DISTINCT TOP 100
            c.meeting_id,
            c.meeting_title,
            c.meeting_date,
            c.content_type
        FROM c
        WHERE c.data_source = @source
            AND IS_DEFINED(c.meeting_id)
            AND IS_DEFINED(c.meeting_date)
            AND (
                c.content_type = @summary_type
                OR c.content_type = @transcript_type
            )
        ORDER BY c.meeting_date DESC
        """,
        [
            {"name": "@source", "value": "meeting_history"},
            {"name": "@summary_type", "value": "meeting_summary"},
            {"name": "@transcript_type", "value": "transcript_chunk"},
        ],
        max_items=100,
    )

    meetings: dict[str, dict[str, Any]] = {}

    for row in rows:
        meeting_id = row.get("meeting_id")
        if not meeting_id:
            continue

        meeting = meetings.setdefault(
            meeting_id,
            {
                "id": f"meeting-index:{meeting_id}",
                "meeting_id": meeting_id,
                "meeting_title": row.get("meeting_title"),
                "meeting_date": row.get("meeting_date"),
                "content_type": "meeting_index",
                "record_types": set(),
                "has_summary": False,
                "has_transcript": False,
            },
        )

        # Fill missing metadata if another record for this meeting has it.
        if not meeting.get("meeting_title") and row.get("meeting_title"):
            meeting["meeting_title"] = row["meeting_title"]

        row_date = row.get("meeting_date")
        current_date = meeting.get("meeting_date")
        if row_date and (not current_date or row_date > current_date):
            meeting["meeting_date"] = row_date

        content_type = row.get("content_type")
        if content_type in {"meeting_summary", "transcript_chunk"}:
            meeting["record_types"].add(content_type)

        if content_type == "meeting_summary":
            meeting["has_summary"] = True
        elif content_type == "transcript_chunk":
            meeting["has_transcript"] = True

    results = list(meetings.values())
    results.sort(
        key=lambda item: item.get("meeting_date") or "",
        reverse=True,
    )

    for meeting in results:
        meeting["record_types"] = sorted(meeting["record_types"])

    return results[:limit]

# Performs semantic search across meeting history and Jira documents stored in Cosmos DB.
async def search_cosmos_context(
    state: State,
    query: str,
    *,
    top_k: int = 5,
    data_source: str | None = None,
    content_type: str | None = None,
) -> list[dict[str, Any]]:
    """Semantic-search indexed meeting/Jira documents using Cosmos vector search."""
    if not isinstance(query, str) or not query.strip():
        raise ValueError("query must not be empty.")
    if not 1 <= top_k <= 20:
        raise ValueError("top_k must be between 1 and 20.")
    if data_source is not None and data_source not in ALLOWED_COSMOS_SOURCES:
        raise ValueError(f"Unsupported data_source: {data_source}")
    if content_type is not None and content_type not in ALLOWED_COSMOS_CONTENT_TYPES:
        raise ValueError(f"Unsupported content_type: {content_type}")

    vectors = await _embed_texts([query.strip()], state)
    query_vector = vectors[0]
    filters = ["IS_DEFINED(c.embedding)"]
    parameters: list[dict[str, Any]] = [
        {"name": "@embedding", "value": query_vector},
    ]
    if data_source is not None:
        filters.append("c.data_source = @data_source")
        parameters.append({"name": "@data_source", "value": data_source})
    if content_type is not None:
        filters.append("c.content_type = @content_type")
        parameters.append({"name": "@content_type", "value": content_type})

    # top_k is validated as an integer and is the only interpolated query value.
    query_sql = f"""
    SELECT TOP {top_k} c.id, c.data_source, c.content_type, c.meeting_id,
        c.meeting_title, c.meeting_date, c.chunk_index, c.issue_key,
        c.project_key, c.project_name, c.summary, c.description,
        c.issue_type, c.status, c.priority, c.assignee_display_name,
        c.labels, c.components, c.jira_url, c.text,
        VectorDistance(c.embedding, @embedding) AS vector_distance
    FROM c
    WHERE {' AND '.join(filters)}
    ORDER BY VectorDistance(c.embedding, @embedding)
    """
    # false, {{'distanceFunction': 'Cosine'}}
    # false, {{'distanceFunction': 'Cosine'}}
    # DESC
    return await _query_cosmos(
        state,
        query_sql,
        parameters,
        max_items=top_k,
    )

# Finds semantically relevant Jira issues from the copies indexed in Cosmos DB.
async def search_cosmos_jira_issues(
    state: State,
    query: str,
    *,
    top_k: int = 5,
) -> list[dict[str, Any]]:
    """Semantic-search Jira issue documents already indexed in Cosmos DB."""
    return await search_cosmos_context(
        state,
        query,
        top_k=top_k,
        data_source="jira_issues",
        content_type="jira_issue",
    )

# Performs semantic search over meeting transcripts and summaries, optionally returning summaries only.
async def search_meeting_history(
    state: State,
    query: str,
    *,
    top_k: int = 5,
    summaries_only: bool = False,
) -> list[dict[str, Any]]:
    """Semantic-search meeting history, optionally restricting results to summaries."""
    return await search_cosmos_context(
        state,
        query,
        top_k=top_k,
        data_source="meeting_history",
        content_type="meeting_summary" if summaries_only else None,
    )
