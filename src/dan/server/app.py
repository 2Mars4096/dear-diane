"""Minimal FastAPI composition root for Work/Notes and Super DAN."""

from __future__ import annotations

from pathlib import Path
import asyncio
from contextlib import asynccontextmanager, suppress

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from dan.server.chat_store import ChatStore
from dan.server.chat_v2_store import ChatV2Store
from dan.server.paths import resolve_graphs_dir
from dan.server.routers.chat_v2 import router as chat_v2_router, resume_recovered_queues
from dan.server.routers.misc import router as workspace_router
from dan.server.routers.sessions import router as sessions_router
from dan.server.routers.native_workers import router as native_workers_router
from dan.server.routers.reader import router as reader_router


def create_app() -> FastAPI:
    @asynccontextmanager
    async def lifespan(app):
        store = app.state.chat_v2_store
        store.recover_interrupted_runs_after_restart()
        recovery = asyncio.create_task(resume_recovered_queues(store))
        try:
            yield
        finally:
            recovery.cancel()
            with suppress(asyncio.CancelledError):
                await recovery

    app = FastAPI(title="DAN Work and Notes", version="0.2.0", lifespan=lifespan)
    app.add_middleware(
        CORSMiddleware,
        allow_origins=["*"],
        allow_credentials=True,
        allow_methods=["*"],
        allow_headers=["*"],
    )

    graphs_dir = Path(resolve_graphs_dir()).expanduser()
    graphs_dir.mkdir(parents=True, exist_ok=True)
    app.state.chat_store = ChatStore(base_dir=str(graphs_dir))
    app.state.chat_v2_store = ChatV2Store(base_dir=str(graphs_dir / "chat_v2"))

    app.include_router(workspace_router)
    app.include_router(sessions_router)
    app.include_router(chat_v2_router)
    app.include_router(native_workers_router)
    app.include_router(reader_router)
    return app


app = create_app()
