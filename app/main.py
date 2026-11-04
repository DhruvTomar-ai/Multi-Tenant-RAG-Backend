from fastapi import FastAPI

from app.api.routes.tenants import router as tenants_router
from app.api.routes.users import router as users_router
from app.api.routes.auth import router as auth_router
from app.api.routes.documents import router as documents_router
from app.api.routes.embeddings import router as embeddings_router
from app.api.routes.jobs import router as jobs_router
from app.api.routes.search import router as search_router

app = FastAPI(title="Multi-Tenant RAG Backend")
app.include_router(tenants_router)
app.include_router(users_router)
app.include_router(auth_router)
app.include_router(documents_router)
app.include_router(jobs_router)
app.include_router(embeddings_router)
app.include_router(search_router)
