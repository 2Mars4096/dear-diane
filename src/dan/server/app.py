"""Minimal FastAPI composition root for Work/Notes and Super DAN."""

from __future__ import annotations

from pathlib import Path

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from dan.server.chat_store import ChatStore
from dan.server.chat_v2_store import ChatV2Store
from dan.server.paths import resolve_graphs_dir
from dan.server.routers.chat_v2 import router as chat_v2_router
from dan.server.routers.misc import router as workspace_router
from dan.server.routers.sessions import router as sessions_router


def create_app() -> FastAPI:
    app = FastAPI(title="DAN Work and Notes", version="0.2.0")
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
    app.state.chat_v2_store.recover_interrupted_runs_after_restart()

    app.include_router(workspace_router)
    app.include_router(sessions_router)
    app.include_router(chat_v2_router)
    return app


app = create_app()
