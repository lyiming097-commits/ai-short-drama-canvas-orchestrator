from __future__ import annotations

from contextlib import asynccontextmanager
from typing import Any
from pathlib import Path

from fastapi import FastAPI
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles

from .api import configure_api, router
from .config import get_settings
from .gateway_admin import ModelGatewayAdmin
from .providers import InMemoryAssetStore, MockModelGateway, S3AssetStore
from .repositories import InMemoryRepository
from .routed_gateway import RoutedModelGateway
from .security import CredentialVault
from .services import StudioService

settings = get_settings()
repository = InMemoryRepository()
asset_store = S3AssetStore(settings.s3_bucket, settings.s3_region) if settings.environment == "production" else InMemoryAssetStore()
vault = CredentialVault(settings.model_config_encryption_key, development=settings.environment != "production")
gateway_admin = ModelGatewayAdmin(repository, vault)
gateway = RoutedModelGateway(gateway_admin)
studio = StudioService(repository, gateway, asset_store, settings, gateway_admin=gateway_admin)
configure_api(studio)


@asynccontextmanager
async def lifespan(_app: FastAPI):
    yield


app = FastAPI(title=settings.app_name, version="0.1.0", lifespan=lifespan)
app.include_router(router)
static_dir = Path(__file__).parent / "static"
app.mount("/static", StaticFiles(directory=static_dir), name="static")


@app.get("/", include_in_schema=False)
def demo_page() -> FileResponse:
    return FileResponse(static_dir / "index.html")


@app.get("/health")
def health() -> dict[str, Any]:
    return {"status": "ok", "environment": settings.environment, "graph": "langgraph" if studio.graph.graph else "fallback"}

