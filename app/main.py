from fastapi import FastAPI

from app.api.routes.tenants import router as tenant_router
from app.api.routes.documents import router as document_router
from app.core.config import get_settings

settings = get_settings()

app = FastAPI(
    title=settings.app_name,
    version=settings.app_version,
    description="Multi-tenant RAG backend API",
)

app.include_router(tenant_router)
app.include_router(document_router)


@app.get("/")
def root() -> dict[str, str]:
    return {"message": "Multi-Tenant RAG Backend API"}
