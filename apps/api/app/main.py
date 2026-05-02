import logging
from contextlib import asynccontextmanager

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from app.core.config import settings
from app.core.database import Base, engine
from app.models import audit as _audit_model  # noqa: F401  (register model on Base)
from app.routers import audit, dashboard, report

logger = logging.getLogger(__name__)


@asynccontextmanager
async def lifespan(_app: FastAPI):
    try:
        async with engine.begin() as conn:
            await conn.run_sync(Base.metadata.create_all)
        logger.info("database: tables ensured")
    except Exception as exc:  # noqa: BLE001
        logger.warning("database: skipping create_all (%s)", exc)
    try:
        yield
    finally:
        try:
            await engine.dispose()
        except Exception:  # noqa: BLE001
            pass


app = FastAPI(title="VERITAS Oracle API", version="1.0.0", lifespan=lifespan)

app.add_middleware(
    CORSMiddleware,
    allow_origins=["http://localhost:5173", "https://veritasoracle.vercel.app"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

app.include_router(audit.router)
app.include_router(dashboard.router)
app.include_router(report.router)


@app.get("/")
async def root():
    return {"status": "operational", "version": "1.0.0"}


@app.get("/health")
async def health():
    return {
        "status": "operational",
        "version": "1.0.0",
        "services": {
            "database": bool(settings.DATABASE_URL),
            "nasa_firms": bool(settings.NASA_FIRMS_API_KEY),
            "sentinel_hub": bool(settings.SENTINEL_HUB_CLIENT_ID and settings.SENTINEL_HUB_CLIENT_SECRET),
            "noaa": bool(settings.NOAA_TOKEN),
            "mapbox": bool(settings.MAPBOX_TOKEN),
            "groq": bool(settings.GROQ_API_KEY),
            "anthropic": bool(settings.ANTHROPIC_API_KEY),
            "global_forest_watch": True,
        },
    }
