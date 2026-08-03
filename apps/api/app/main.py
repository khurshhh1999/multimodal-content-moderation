from __future__ import annotations

from contextlib import asynccontextmanager

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from .config import get_settings
from .db import close_pool, init_pool
from .redis_client import close_redis, init_redis
from .routes import ingest, metrics, review


@asynccontextmanager
async def lifespan(_: FastAPI):
    await init_pool()
    await init_redis()
    yield
    await close_redis()
    await close_pool()


app = FastAPI(
    title="Multimodal Content Moderation API",
    version="0.1.0",
    description="Ingest, decisions, and human review for multimodal UGC",
    lifespan=lifespan,
)

settings = get_settings()
origins = [o.strip() for o in settings.cors_origins.split(",") if o.strip()]
app.add_middleware(
    CORSMiddleware,
    allow_origins=origins or ["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

app.include_router(ingest.router)
app.include_router(review.router)
app.include_router(metrics.router)


@app.get("/health")
async def health() -> dict[str, str]:
    return {"status": "ok", "service": "api"}
