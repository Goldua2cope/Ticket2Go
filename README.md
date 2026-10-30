# Ticket2Go

Ticket2Go turns meetings into Jira tickets. It records a meeting, transcribes it live, summarises it, and suggests Jira issues based on what was said. Before suggesting anything it checks earlier meetings and existing Jira tickets. A person reviews every suggestion before anything reaches Jira.

## How it works

1. **Record.** The browser streams microphone audio over a WebSocket. Azure AI Foundry transcribes it live, and the meeting is registered in PostgreSQL.
2. **When the recording ends**, three things happen to the transcript:
   - **Store:** the full transcript is saved as a JSON file in Azure Blob Storage.
   - **Summarise:** an LLM writes a short summary, which is shown in the app.
   - **Index:** transcript chunks, the summary and the Jira issues are embedded and stored in Cosmos DB for vector search.
3. **Find the work.** The saved transcript is read back from Blob Storage. The LLM extracts actions, decisions, questions and problems (`meeting_items`).
4. **Check the history.** For each item, a hybrid search runs over past meetings and Jira tickets. Problems are assessed for recurrence: new, recurring, or regressed after being resolved.
5. **Draft a suggestion.** The LLM decides one of four things for each item:
   - **create** a new issue;
   - **update** an existing issue;
   - **revise** a pending suggestion from an earlier meeting;
   - **none**.

   Suggestions are saved in `ticket_proposals` with status `pending`.
6. **Person reviews.** In the app, the user edits, approves or rejects each suggestion. When editing, they can choose:
   - the work type;
   - the parent, for a subtask;
   - the destination: backlog or a sprint.
7. **Send to Jira.** Only approved suggestions are created or updated in Jira. The result is synced back into the local Jira cache.

Nothing reaches Jira without a person's approval.

## Tech stack

| Part | Technology |
| --- | --- |
| Backend | Python 3.12, FastAPI, asyncpg, httpx |
| Frontend | React 19, TypeScript, Vite, Tailwind CSS |
| AI | Azure AI Foundry, via the OpenAI SDK: `gpt-live-transcribe`, `gpt-5.4`, `text-embedding-3-small` |
| Relational data | Azure Database for PostgreSQL |
| Search index | Azure Cosmos DB (vector search) |
| Transcript files | Azure Blob Storage |
| Secrets | Azure Key Vault, accessed through `DefaultAzureCredential` |
| Ticketing | Jira Cloud REST API v3 and the Agile API |

## Prerequisites

- Python 3.12
- Node.js 20.19 or later (needed by Vite 8)
- Azure CLI, signed in with `az login` to an account that can read secrets from the Key Vault
- Access to the Azure resources named in [backend/config.py](backend/config.py)

The backend reads these secrets from Key Vault at startup:

| Secret | Used for |
| --- | --- |
| `azure-openai-api-key` | Azure AI Foundry |
| `azure-storage-connection-string` | Blob Storage |
| `cosmos-connection-string` | Cosmos DB |
| `psql-host`, `psql-admin`, `psql-password` | PostgreSQL |
| `jira-api-token` | Jira Cloud |

The Jira site, project key (`SCRUM`), board id and account email are set in `config.py`.

## Setup

**1. Install the backend dependencies.** Run from the repository root:

```bash
python -m venv .venv
# Windows: .venv\Scripts\activate    macOS/Linux: source .venv/bin/activate
pip install -r requirements.txt
```

**2. Build the frontend.** The backend serves `frontend/dist`, and it won't start if that folder is missing.

```bash
cd frontend
npm install
npm run build
```

**3. Create the database tables.** Run [backend/SQL-queries/table_creation.sql](backend/SQL-queries/table_creation.sql) against the PostgreSQL database. Run the part up to `COMMIT;`. The lines after it are ad-hoc queries.

**4. Import Jira.** Do this once, so suggestions can be compared with existing tickets:

```bash
python -m backend.jira_sync --full
```

After this first import, the sync runs incrementally. It runs in the background when a recording starts, after an approval, and when the user presses refresh in the app.

## Running

**Single server.** The backend serves the built frontend:

```bash
python -m backend.main
```

Open http://127.0.0.1:8000. Run `npm run build` again after any frontend changes.

**Frontend development, with hot reload.** Start the backend as above, then in a second terminal:

```bash
cd frontend
npm run dev
```

Open the address Vite prints, usually http://localhost:5173. Vite forwards the API and WebSocket calls to port 8000.

**Demo login:** username `demo`, password `ticket2go`. The login exists only on the frontend; there are no real user accounts.

## API overview

| Method | Path | Purpose |
| --- | --- | --- |
| `GET` | `/health` | Checks that PostgreSQL, Cosmos DB and Blob Storage are reachable |
| `WS` | `/ws/transcribe` | Records and transcribes a meeting, then stores, indexes and summarises it |
| `POST` | `/api/meeting-assistant/chat` | Meeting assistant chat (read-only, Server-Sent Events) |
| `GET` | `/meetings` | Meeting history with its suggestions |
| `GET` | `/meetings/{id}/summary` | Saved summary of a meeting |
| `POST` | `/meetings/refresh` | Syncs Jira now |
| `POST` | `/meetings/{id}/suggestions` | Generates suggestions for a completed meeting |
| `GET` | `/meetings/{id}/suggestions` | Lists a meeting's suggestions, including earlier ones it revised |
| `GET` | `/proposals/{id}` | One suggestion |
| `PATCH` | `/proposals/{id}` | Edits a suggestion: text, priority, work type, parent, sprint |
| `POST` | `/proposals/{id}/approve` | Approves a suggestion and creates or updates the Jira issue |
| `POST` | `/proposals/{id}/reject` | Rejects a suggestion |
| `GET` | `/proposals/{id}/parent-options` | Possible parents for a subtask |
| `GET` | `/proposals/sprint-options` | Open sprints on the board |
| `GET` | `/proposals/parent-sprint?issue_key=` | The sprint a parent issue is in |

With the backend running, interactive API docs are at http://127.0.0.1:8000/docs.

## Review rules

- **Decisions are final.** Once a suggestion is approved or rejected, any further changes are made in Jira.
- **Approval is claimed before Jira is called**, so a double click or a retry can't create the same issue twice.
- **Work types** are Epic, Story, Task, Bug and Subtask:
  - The AI usually suggests a Task. It suggests a Story only rarely, and it never suggests an Epic.
  - A Subtask needs a parent Story, Task or Bug. The parent can be an existing Jira issue or another suggestion.
  - If the parent is still a suggestion, it must be approved first. The subtask is then linked to the new Jira issue automatically.
  - The app shows a warning when the parent issue is already done.
- **Destination:** new Stories, Tasks and Bugs go to the backlog unless a sprint is chosen while editing. A Subtask follows its parent's sprint.
- **Updates overwrite** the summary, description and priority of the existing Jira issue.
- **Revisions:** if a later meeting changes a pending suggestion (for example, a new deadline), the change is added to its description. The suggestion is then listed under both meetings.

## Optional settings

`jira_sync.py` reads these environment variables, or a `backend/.env` file:

| Variable | Meaning | Default |
| --- | --- | --- |
| `JIRA_SYNC_OVERLAP_MINUTES` | Overlap window for incremental syncs | `5` |
| `JIRA_PAGE_SIZE` | Issues fetched per request | `100` |
| `JIRA_REQUEST_TIMEOUT_SECONDS` | Timeout for Jira requests | `60` |
| `JIRA_MAX_EMBED_CHARS` | Longest issue text that gets embedded | `12000` |
| `JIRA_TIMEZONE` | Time zone used for Jira's date filters | `Europe/Stockholm` |


