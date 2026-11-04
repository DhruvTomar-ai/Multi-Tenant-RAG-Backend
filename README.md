# Multi-Tenant RAG

A question-answering system over company documents, built so that each tenant, team and person sees only what they're allowed to.

Users upload documents into teams. The system extracts the text, splits it into chunks and embeds them. People can then search those documents, or ask questions in a chat and get answers with citations. Every search and answer is limited to documents the person can read at that moment.

## What it does

- **Tenants, teams and roles:** each tenant has teams. Each person has a tenant role and a team role: admin, manager, member or viewer.
- **Document access:** reading comes from the team role. A manager can also share one document with one person, which is called a grant.
- **Documents:** upload PDF, DOCX and TXT files with metadata such as department, tags and an access label. A background worker extracts the text, splits it into chunks and embeds them.
- **Search:** semantic search over the chunks you can read, with team, document and metadata filters.
- **Chat:** answers from Groq, grounded in retrieved passages, with citations the backend validates. The system says plainly when the documents don't contain the answer.
- **Isolation:** another tenant's or team's data always looks the same as data that doesn't exist. Access is rechecked on every request, so losing access to a document also hides old answers that cited it.

## How it fits together

```
Browser (React)  ──/api──▶  FastAPI  ──▶  PostgreSQL + pgvector (data, chunks, 384-d vectors, HNSW index)
                               │
                               ├──▶  Redis  ──▶  Celery worker (extract, chunk, embed with BAAI/bge-small-en-v1.5)
                               │
                               └──▶  Groq API (answer generation only)
```

| Part | Technology |
|---|---|
| API | Python 3.11+, FastAPI, SQLAlchemy 2, Alembic |
| Database | PostgreSQL with pgvector (HNSW index, cosine distance) |
| Background jobs | Celery with Redis |
| Embeddings | `BAAI/bge-small-en-v1.5`, run locally on CPU. No paid embedding API. |
| Answers | Groq, default model `openai/gpt-oss-120b` |
| Frontend | React, TypeScript, Vite, Tailwind |

## Repository layout

| Path | Contents |
|---|---|
| `app/api/routes/` | HTTP endpoints: auth, tenants, users, documents, jobs, embeddings, search, chat |
| `app/security/` | Tokens, roles and the single definition of who may read a document |
| `app/services/` | Business logic: documents, embeddings and search, RAG chat |
| `app/workers/` | Celery tasks for ingestion and embedding, and the backfill command |
| `alembic/` | Database migrations |
| `frontend/` | The web app, its end-to-end tests and manual testing guide |
| `tests/` | Unit and integration tests |
| `api_tests/` | API and isolation tests against a throwaway PostgreSQL |
| `benchmarks/` | Chunk size, search latency, HNSW and relevance threshold measurements, plus workflow reports |

## Running it locally

You need Python 3.11+, Node 20+ and Docker. The commands use Windows paths, so use `.venv/bin/` instead of `.venv\Scripts\` on macOS and Linux.

### 1. One-time setup

```bash
# Python environment and dependencies
python -m venv .venv
.venv\Scripts\activate
pip install -e ".[dev]"

# Settings: copy the example, then fill in DATABASE_URL, both JWT secrets and GROQ_API_KEY
copy .env.example .env
python -c "import secrets; print(secrets.token_urlsafe(48))"   # run twice, once per JWT secret

# Frontend dependencies
cd frontend
npm install
cd ..
```

The embedding model downloads from Hugging Face into `./models` the first time it's used, at a pinned revision. Later runs can work offline.

### 2. Start the services

`docker-compose.yml` starts Redis only. PostgreSQL with pgvector runs in its own container on port 5433. If you don't have it yet:

```bash
docker run -d --name multi-tenant-rag-db -e POSTGRES_USER=rag -e POSTGRES_PASSWORD=change-me -e POSTGRES_DB=rag -p 127.0.0.1:5433:5432 pgvector/pgvector:pg18
docker compose up -d
```

Match `DATABASE_URL` in `.env` to these values: `postgresql+psycopg://rag:change-me@localhost:5433/rag`.

### 3. Create or update the database tables

```bash
alembic upgrade head
```

Run this after every pull that adds a migration. It also enables the `vector` extension.

### 4. Run the system

Use three terminals, all from the repository root with the virtual environment active.

```bash
# Terminal 1: API on http://127.0.0.1:8000 (interactive docs at /docs)
uvicorn app.main:app --port 8000 --reload

# Terminal 2: background worker. Uploads stay at "Waiting" without it. --pool=solo is needed on Windows.
celery -A app.workers.celery_app:celery_app worker --pool=solo --loglevel=info

# Terminal 3: web app on http://localhost:5173
cd frontend
npm run dev
```

The web app forwards `/api` to the API, so no CORS setup is needed. Create an account at `/register`, then create a tenant. You become its admin.

### Useful commands

| Task | Command |
|---|---|
| Check the database is up to date | `alembic current` |
| Embed documents that were never embedded | `python -m app.workers.backfill_embeddings` |
| Also retry failed embeddings | `python -m app.workers.backfill_embeddings --include-failed` |
| Check the relevance threshold against your documents | `python benchmarks/relevance_threshold.py` |
| Production build of the web app | `cd frontend` then `npm run build` (output in `frontend/dist/`) |
| Stop Redis | `docker compose down` |

## Configuration

All settings live in `.env`, and `.env.example` lists every one with its default. The important ones:

| Setting | Purpose |
|---|---|
| `DATABASE_URL` | PostgreSQL connection |
| `JWT_SECRET_KEY`, `JWT_REFRESH_SECRET_KEY` | Token signing. Use long random values, never shared. |
| `CELERY_BROKER_URL` | Redis for the worker |
| `GROQ_API_KEY`, `GROQ_MODEL` | Answer generation. Without a key, search still works, and questions that need the AI get a clear 503. |
| `MAX_UPLOAD_SIZE_MB`, `ALLOWED_DOCUMENT_EXTENSIONS` | Upload limits |
| `RAG_MIN_SIMILARITY` | How related a passage must be before the AI sees it (default 0.50) |

Never commit `.env`. It's already in `.gitignore`.

## Tests

The test commands use throwaway PostgreSQL (port 5434) and Redis (port 6380) containers, never the development database. The test setup refuses to run against anything else, because it deletes everything in its database.

```bash
# Start the throwaway services
docker compose -p rag-api-test -f api_tests/docker-compose.test.yml up -d --wait

# Unit and integration tests (about 1 minute)
pytest tests

# API and isolation tests (about 25 minutes). Writes api_tests/reports/api_test_report.md.
set TEST_DATABASE_URL=postgresql+psycopg://rag_test:rag_test@127.0.0.1:5434/rag_api_test
set TEST_REDIS_URL=redis://127.0.0.1:6380/0
pytest api_tests -p no:cacheprovider -q

# End-to-end browser tests (about 4 minutes). They start the API, worker and a fake Groq themselves.
cd frontend
npx playwright install chromium
npm run test:e2e
cd ..

# Remove the throwaway services
docker compose -p rag-api-test -f api_tests/docker-compose.test.yml down -v
```

`set` is for Windows `cmd`. In PowerShell use `$env:TEST_DATABASE_URL="..."`, and in bash use `export`.

The end-to-end tests replace Groq with a local fake, so they cost no quota. Real Groq is used only by the optional live tests: `api_tests/test_n_groq_live.py` with `GROQ_LIVE_TEST_API_KEY` set, and the single browser test run with `E2E_LIVE_GROQ=1` and `GROQ_API_KEY`.

### Known failing tests

36 API tests fail. They are known backend bugs, kept failing on purpose so they stay visible, and none involve documents, search or chat. The most serious is that `GET /users` and `POST /users` need no login. The full list is in `benchmarks/reports/WORKFLOW5_REPORT.md`, section 7.

## Manual testing

[`frontend/TESTING_GUIDE.md`](frontend/TESTING_GUIDE.md) seeds two tenants with a user for every role, then walks through each role's scenarios, including the isolation checks.

## Known limitations

- **No logout endpoint:** signing out clears the browser, but a copied token keeps working until it expires.
- **No delete management:** documents can't be deleted in v1.
- **Missing endpoints:** there's no endpoint for a user's own role, and none for removing someone from a tenant.
- **Relevance threshold:** it was tuned on short passages, so recheck it with your own documents before relying on "insufficient evidence" answers.
- **Further details:** the web app's gaps are in [`frontend/README.md`](frontend/README.md). Each workflow's results are in `benchmarks/reports/`.
