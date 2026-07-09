"""FastAPI application entry point.

Run locally (single worker, loopback only — this is a private local tool and
must never be network-exposed):

    uvicorn app.main:app --host 127.0.0.1 --port 8000 --workers 1
"""
from __future__ import annotations

from contextlib import asynccontextmanager

from fastapi import FastAPI
from fastapi.staticfiles import StaticFiles

from . import config
from .db import init_db
from .routes import analysis, transcripts, uploads


@asynccontextmanager
async def lifespan(app: FastAPI):
    # Create data dirs + schema; reconcile any transcription stranded by a crash.
    init_db()
    yield


app = FastAPI(title="Audio Intelligence — Prototype v1", lifespan=lifespan)

app.mount("/static", StaticFiles(directory=str(config.STATIC_DIR)), name="static")

app.include_router(uploads.router)
app.include_router(transcripts.router)
app.include_router(analysis.router)
