"""FastAPI application entry point.

Run locally (single worker, loopback only — this is a private local tool and
must never be network-exposed):

    uvicorn app.main:app --host 127.0.0.1 --port 8000 --workers 1
"""
from __future__ import annotations

from contextlib import asynccontextmanager

from fastapi import FastAPI, Request
from fastapi.responses import RedirectResponse
from fastapi.staticfiles import StaticFiles

from . import config
from .db import init_db
from .routes import analysis, auth, transcripts, uploads, chat
from .web import UnauthenticatedException


@asynccontextmanager
async def lifespan(app: FastAPI):
    # Create data dirs + schema; reconcile any transcription stranded by a crash.
    init_db()
    yield


app = FastAPI(title="Audio Intelligence", lifespan=lifespan)


@app.exception_handler(UnauthenticatedException)
async def unauthenticated_exception_handler(request: Request, exc: UnauthenticatedException):
    """Globally catch unauthenticated user exceptions and redirect to login page."""
    return RedirectResponse(url="/login", status_code=303)


app.mount("/static", StaticFiles(directory=str(config.STATIC_DIR)), name="static")

app.include_router(auth.router)
app.include_router(uploads.router)
app.include_router(transcripts.router)
app.include_router(analysis.router)
app.include_router(chat.router)

