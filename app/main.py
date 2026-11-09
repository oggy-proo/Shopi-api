"""
app/main.py - Enterprise FastAPI Application Factory with Lifespan & Middlewares
"""
import os
import uvicorn
from contextlib import asynccontextmanager
from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from app.core.config import settings
from app.core.proxy_manager import proxy_manager
from app.api.routes import router


@asynccontextmanager
async def lifespan(app: FastAPI):
    # Startup Initialization
    loaded_proxies = proxy_manager.load_from_file(settings.PROXY_FILE)
    print(f"[Shopify Engine v2.0] Started successfully. Proxies loaded: {loaded_proxies}")
    yield
    # Clean shutdown
    print("[Shopify Engine v2.0] Shutting down.")


def create_app() -> FastAPI:
    application = FastAPI(
        title=settings.PROJECT_NAME,
        version=settings.VERSION,
        lifespan=lifespan,
        docs_url="/docs",
        redoc_url="/redoc"
    )

    # Enable CORS for API clients
    application.add_middleware(
        CORSMiddleware,
        allow_origins=["*"],
        allow_credentials=True,
        allow_methods=["*"],
        allow_headers=["*"],
    )

    application.include_router(router, prefix=settings.API_PREFIX)
    return application


app = create_app()


if __name__ == "__main__":
    port = int(os.getenv("PORT", 8000))
    host = os.getenv("HOST", "0.0.0.0")
    workers = int(os.getenv("WORKERS", "4"))
    
    if os.name == "nt":
        # Windows development
        uvicorn.run("app.main:app", host=host, port=port, reload=False)
    else:
        # Linux/Production (Railway / Docker / VPS)
        uvicorn.run("app.main:app", host=host, port=port, workers=workers, loop="uvloop")
