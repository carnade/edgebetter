"""FastAPI application entrypoint."""

from __future__ import annotations

import logging
from contextlib import asynccontextmanager

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from app.config import get_settings
from app.db import SessionLocal
from app.routers import nfl, sports, system

log = logging.getLogger(__name__)


@asynccontextmanager
async def lifespan(app: FastAPI):
    settings = get_settings()
    logging.basicConfig(level=settings.log_level.upper())

    # Seeding the crosswalk on boot keeps a fresh database usable immediately, and
    # validates the YAML before any ingest can half-write rows against it.
    try:
        with SessionLocal() as session:
            from app.services.team_map import seed_teams

            seed_teams(session)
    except Exception as exc:  # noqa: BLE001 - never block startup on seeding
        log.warning("team seeding on startup failed: %s", exc)

    if not settings.odds_enabled:
        log.info("THE_ODDS_API_KEY not set: stats endpoints work, edges will be empty")
    yield


app = FastAPI(
    title="EdgeBetter",
    version="0.1.0",
    summary="Find edges between sportsbook prices and independent projections",
    lifespan=lifespan,
)

app.add_middleware(
    CORSMiddleware,
    allow_origins=["http://localhost:5173", "http://127.0.0.1:5173"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

app.include_router(system.router, prefix="/api")
# NFL is registered before `sports`, whose `/{sport}/...` routes would otherwise
# capture /api/nfl/* first. FastAPI matches in registration order.
app.include_router(nfl.router, prefix="/api")
app.include_router(sports.router, prefix="/api")


@app.get("/")
def root() -> dict[str, str]:
    return {"service": "edgebetter", "docs": "/docs"}
