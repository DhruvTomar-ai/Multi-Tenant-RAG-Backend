# Multi-Tenant RAG Backend

A FastAPI-based multi-tenant retrieval-augmented generation (RAG) application for tenant-isolated document ingestion, semantic search, and conversational AI using retrieved document chunks.

## Features

- Multi-tenant isolation for documents, search, and conversations
- Document upload and parsing into text chunks
- Local embedding generation with `BAAI/bge-small-en-v1.5`
- PostgreSQL + pgvector storage
- HNSW vector indexing for fast semantic retrieval
- Tenant-scoped search and chat APIs
- Groq-based answer generation using retrieved context
- Simple frontend dashboard for testing the API

## Tech Stack

- Python 3.11+
- FastAPI
- SQLAlchemy
- PostgreSQL with pgvector
- Alembic
- SentenceTransformers
- Groq API
- HTML/CSS/JavaScript frontend

## Project Structure

```text
Multi-Tenant-RAG-Backend/
├── app/
│   ├── api/
│   │   └── routes/
│   ├── core/
│   ├── models/
│   ├── schemas/
│   ├── services/
│   └── main.py
├── alembic/
├── frontend/
├── tests/
├── .env
├── .env.example
├── .gitignore
├── alembic.ini
├── requirements.txt
├── pyproject.toml
├── README.md
└── validate_workflow4.py
```

## Prerequisites

- PostgreSQL installed and running
- pgvector extension enabled
- Python virtual environment
- Groq API key for LLM response generation

### PostgreSQL Setup

```sql
CREATE DATABASE multi_tenant_rag;
\c multi_tenant_rag;
CREATE EXTENSION IF NOT EXISTS vector;
```

## Environment Configuration

Create a `.env` file in the project root:

```env
DB_HOST=127.0.0.1
DB_PORT=5432
DB_NAME=multi_tenant_rag
DB_USER=postgres
DB_PASSWORD=your_password
GROQ_API_KEY=your_groq_key
GROQ_MODEL=llama-3.1-8b-instant
```

The app also supports defaults for embedding settings in `app/core/config.py`:
- `EMBEDDING_MODEL_NAME=BAAI/bge-small-en-v1.5`
- `EMBEDDING_DIMENSION=384`
- `DEFAULT_RETRIEVAL_K=5`

## Installation

From the project root:

```bash
python -m venv .venv
```

Windows PowerShell:

```powershell
.\.venv\Scripts\Activate.ps1
pip install -r requirements.txt
```

macOS/Linux:

```bash
source .venv/bin/activate
pip install -r requirements.txt
```

## Database Migration

Run the database migrations:

```bash
alembic upgrade head
```

## Start the Backend

From the project root:

```bash
uvicorn app.main:app --host 0.0.0.0 --port 8000 --reload
```

API docs:
- Swagger: `http://localhost:8000/docs`
- ReDoc: `http://localhost:8000/redoc`

## Start the Frontend

Open a second terminal:

```bash
cd frontend
python -m http.server 8080
```

Then open:
- `http://localhost:8080`

The frontend is configured to call the backend at:
- `http://localhost:8000`

## Main API Routes

### Tenants
- `POST /api/v1/tenants` — create a tenant
- `GET /api/v1/tenants` — list tenants

### Documents
- `POST /api/v1/tenants/{tenant_id}/documents` — upload a document
- `GET /api/v1/tenants/{tenant_id}/documents` — list documents

### Search
- `POST /api/v1/tenants/{tenant_id}/search` — semantic search within a tenant

### Conversations
- `POST /api/v1/tenants/{tenant_id}/conversations` — create a conversation
- `GET /api/v1/tenants/{tenant_id}/conversations` — list conversations
- `POST /api/v1/tenants/{tenant_id}/conversations/{conversation_id}/messages` — send a chat message
- `GET /api/v1/tenants/{tenant_id}/conversations/{conversation_id}/messages` — fetch messages

## Example: Create a Tenant

```bash
curl -X POST "http://localhost:8000/api/v1/tenants" \
  -H "Content-Type: application/json" \
  -d '{
    "slug": "tenant-alpha",
    "name": "Tenant Alpha",
    "owner_email": "owner@example.com",
    "owner_full_name": "Owner Name",
    "owner_password": "StrongPass123"
  }'
```

## Example: Upload a Document

```bash
curl -X POST "http://localhost:8000/api/v1/tenants/1/documents" \
  -F "file=@sample.txt" \
  -F 'metadata={"source":"manual-upload"}'
```

## Example: Semantic Search

```bash
curl -X POST "http://localhost:8000/api/v1/tenants/1/search" \
  -H "Content-Type: application/json" \
  -d '{
    "query": "password rotation policy",
    "k": 5,
    "score_threshold": 0.0
  }'
```

## Testing

```bash
pytest -q
```

## Notes

- Tenant data is query-scoped and filtered by tenant ID.
- Uploaded documents are chunked and embedded before search.
- Retrieved chunks are passed into the LLM prompt as context.
- If `GROQ_API_KEY` is missing, the app may not be able to generate LLM answers.

## Troubleshooting

### Backend not starting
- Check PostgreSQL is running
- Validate `.env` values
- Confirm pgvector extension is enabled

### Frontend shows API errors
- Make sure the backend is running on port `8000`
- Check the API base URL in the frontend UI
- Ensure the FastAPI app has CORS enabled for the frontend origin

### Large model download issues
- Keep `.cache/` ignored in `.gitignore`
- Do not commit downloaded embedding model files to GitHub

## License

This project is intended for local development and demonstration purposes unless specified otherwise.
