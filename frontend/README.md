# Multi-Tenant RAG frontend

React, TypeScript and Vite, with Tailwind, React Router, TanStack Query and Radix Dialog.

## Run it with the backend

You need Node 20 or newer, Python with the backend's `.venv`, and Docker.

1. Start the backend's services from the repository root:

   ```
   docker compose up -d
   alembic upgrade head
   uvicorn app.main:app --port 8000
   celery -A app.workers.celery_app:celery_app worker --pool=solo --loglevel=info
   ```

   Run `uvicorn` and `celery` in separate terminals. The worker processes uploads, so documents stay at "Waiting" without it. `--pool=solo` is for Windows.

2. Start the frontend:

   ```
   cd frontend
   npm install
   npm run dev
   ```

   Open http://localhost:5173.

### Environment variables

Copy `.env.example` to `.env` only if you need to change these. All are optional.

| Variable | Default | Purpose |
|---|---|---|
| `API_PROXY_TARGET` | `http://127.0.0.1:8000` | Where the dev and preview servers forward `/api`. Never sent to the browser. |
| `VITE_MAX_UPLOAD_MB` | `25` | Limit shown and checked before upload. Keep it matched to the backend's `MAX_UPLOAD_SIZE_MB`. |
| `VITE_ALLOWED_EXTENSIONS` | `.pdf,.docx,.txt` | Types shown and checked before upload. Keep them matched to `ALLOWED_DOCUMENT_EXTENSIONS`. |

The Groq key stays in the backend's `.env`. Don't put secrets in a `VITE_` variable, because those are built into the browser code.

### CORS

The backend has no CORS settings, and it doesn't need any here. The browser only talks to the frontend's own address, and the Vite server forwards `/api` to the backend.

In production, serve `dist/` and the API from the same address, with a reverse proxy sending `/api` to the backend. If they must be on different addresses, the backend would need `CORSMiddleware`, limited to that one frontend address.

## How sign-in is stored

- **Access token:** in memory only, so it's gone on reload.
- **Refresh token:** in `sessionStorage`, so a reload keeps you signed in and closing the tab ends it.
- **Why not `httpOnly` cookies:** they're safer against script injection, but the backend returns tokens only as JSON. Using them would mean changing the backend.
- **Reducing the script-injection risk:** document text and model answers are always rendered as plain text, and production builds set a Content Security Policy that blocks inline scripts.
- **Sign out:** this clears the browser only. The backend has no logout endpoint, so a copied token keeps working until it expires: 30 minutes for access and 7 days for refresh, by default.

## End-to-end tests

The tests use the real API, Celery worker, PostgreSQL, Redis and embedding model. Only Groq is replaced, by `e2e/stack/fake_groq.py`, which the backend reaches through the SDK's `GROQ_BASE_URL` setting.

They use the throwaway PostgreSQL on port 5434 and Redis on port 6380, never your development database. The seed script refuses to run on anything else, because it deletes everything in its database.

```
# From the repository root
docker compose -p rag-api-test -f api_tests/docker-compose.test.yml up -d --wait

cd frontend
npx playwright install chromium
npm run test:e2e

# When finished, from the repository root
docker compose -p rag-api-test -f api_tests/docker-compose.test.yml down -v
```

To also check real Groq, set `E2E_LIVE_GROQ=1` and `GROQ_API_KEY`, then run `npx playwright test` after `npm run build`. That runs only one test, with one question, to save quota. A normal run never calls real Groq.

## Trying it by hand

`frontend/TESTING_GUIDE.md` has the seeded accounts and every scenario to click through, by role.

## API coverage

Left out on purpose:

| Endpoint | Why |
|---|---|
| `GET /users`, `POST /users` | They need no login, which is a known backend bug. People are managed through the tenant and team member endpoints instead. |
| `POST /auth/tenant/select` | It stores nothing and issues no token. The tenant comes from the URL, and the backend checks it on each request. |
| `GET /jobs`, `GET /jobs/{id}`, `GET .../documents/{id}/jobs` | Job IDs are internal. The document status endpoint gives users what they need. |
| `GET .../documents/{id}/embeddings` | Technical details with nothing for a user to act on. |
| `GET .../documents/{id}/metadata` | The document details endpoint already returns the same fields. |
| `GET .../documents/{id}/text` | The chunks endpoint shows the same text, and is what citations point to. |
| `GET /tenants/{id}/embeddings/model`, `GET /tenants/{id}/embeddings/index` | Operational details for developers. |
| `GET /tenants/{id}` | `GET /tenants` already returns the same details for the user's tenants. |
| `PATCH /tenants/{id}` with `is_active` | Deactivating a tenant locks its admin out, and a known backend bug stops them reactivating it. The rest of this endpoint is used, to edit the name, web name and description. |
