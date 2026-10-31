# Multi-Tenant RAG Frontend

This frontend connects to the existing FastAPI backend in this repository.

## Run locally

Open the frontend files with a static server, for example:

```bash
cd "C:/Users/Victus/OneDrive/Documents/RAG/Multi-Tenant-RAG-Backend/frontend"
python -m http.server 8080
```

Then open:

- http://localhost:8080

## API configuration

The frontend uses a reusable module at `frontend/api.js` and stores the base URL in `localStorage`.

Default:

- http://localhost:8000

You can update it from the sidebar.

## Supported flows

- Create tenants
- Upload tenant documents using multipart form-data
- List tenant documents
- Semantic search by tenant
- Create conversations and read tenant-aware history
- Send chat messages with retrieval context

## Notes

- The backend is expected to run on port 8000.
- The frontend does not invent new APIs; it calls the existing routes implemented in the FastAPI application.
- If the backend is not running, the UI will show the error returned by the API.
