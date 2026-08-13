"""FastAPI entrypoint for Zalo Qwen Assistant."""

from __future__ import annotations

import asyncio
from contextlib import asynccontextmanager
from typing import Literal

from fastapi import FastAPI, Header, HTTPException, Query
from fastapi.responses import HTMLResponse
from pydantic import BaseModel, Field

from app.config import get_settings
from app.repositories.supabase_repo import SupabaseRepo
from app.services.llm_service import LLMService
from app.services.message_pipeline import MessagePipeline
from app.services.rag_service import RAGService
from app.services.router_service import MessageRouter
from app.services.zalo_bridge import MockZaloBridge
from app.services.zalo_bridge_real import RealZaloBridge

settings = get_settings()
llm_service = LLMService(settings)
rag_service = RAGService()
router = MessageRouter(llm=llm_service, rag=rag_service)
pipeline = MessagePipeline(router=router, settings=settings)
supabase_repo = SupabaseRepo(settings)

if settings.enable_zalo_real:
    zalo_bridge: MockZaloBridge | RealZaloBridge = RealZaloBridge(
        pipeline=pipeline,
        repo=supabase_repo,
        settings=settings,
    )
else:
    zalo_bridge = MockZaloBridge(settings=settings)


def _require_admin_token(token: str | None) -> None:
    if not settings.admin_token or token != settings.admin_token:
        raise HTTPException(status_code=401, detail="Unauthorized")


@asynccontextmanager
async def lifespan(_app: FastAPI):
    rag_service.ensure_index()
    loop = asyncio.get_running_loop()
    if isinstance(zalo_bridge, RealZaloBridge):
        zalo_bridge.set_event_loop(loop)
        zalo_bridge.start()
    yield
    rag_service.close()


app = FastAPI(title="Zalo Qwen Assistant", lifespan=lifespan)


class AskRequest(BaseModel):
    question: str = Field(..., min_length=1)
    group_type: Literal["internal", "customer"] = "customer"


class SimulateEvent(BaseModel):
    group_id: str
    sender_id: str = ""
    sender_name: str = ""
    sender_gender: Literal["male", "female", "unknown"] = "unknown"
    text: str


@app.get("/health")
async def health() -> dict[str, str]:
    """Liveness probe for local checks and deployments."""
    return {"status": "ok", "service": "zalo-qwen-assistant"}


@app.post("/ask")
async def ask(payload: AskRequest) -> dict[str, object]:
    """Route a question through persona + RAG + LLM."""
    return await router.route(payload.group_type, payload.question)


@app.post("/simulate")
async def simulate(payload: SimulateEvent) -> dict[str, object]:
    """Simulate an inbound Zalo message through the mock bridge pipeline."""
    event = payload.model_dump()
    result = await pipeline.handle(event)
    if result is None:
        return {"replied": False}

    await zalo_bridge.send(payload.group_id, str(result["answer"]))
    return {
        "replied": True,
        "answer": result["answer"],
        "honorific": result["honorific"],
        "model_used": result["model_used"],
    }


@app.get("/zalo/status")
async def zalo_status(x_admin_token: str | None = Header(default=None)) -> dict[str, str]:
    """Return current Zalo bridge connection status."""
    _require_admin_token(x_admin_token)
    if isinstance(zalo_bridge, RealZaloBridge):
        return zalo_bridge.get_status()
    return {"status": "mock"}


@app.post("/zalo/persist-session")
async def zalo_persist_session(
    x_admin_token: str | None = Header(default=None),
) -> dict[str, bool]:
    """Force-save the current Zalo session to Supabase."""
    _require_admin_token(x_admin_token)
    if not isinstance(zalo_bridge, RealZaloBridge):
        raise HTTPException(status_code=404, detail="Real Zalo bridge disabled")
    saved = zalo_bridge.persist_session_now()
    return {"saved": saved}


@app.post("/zalo/refresh-qr")
async def zalo_refresh_qr(
    x_admin_token: str | None = Header(default=None),
) -> dict[str, str]:
    """Generate a fresh Zalo QR code (e.g. after timeout or failed scan)."""
    _require_admin_token(x_admin_token)
    if not isinstance(zalo_bridge, RealZaloBridge):
        raise HTTPException(status_code=404, detail="Real Zalo bridge disabled")
    started = zalo_bridge.refresh_qr_login()
    return {"status": "awaiting_qr" if started else zalo_bridge.status}


@app.get("/zalo/qrpage", response_class=HTMLResponse)
async def zalo_qrpage(token: str = Query(default="")) -> HTMLResponse:
    """Simple QR login page with 5-second status polling."""
    _require_admin_token(token)
    if not isinstance(zalo_bridge, RealZaloBridge):
        raise HTTPException(status_code=404, detail="Real Zalo bridge disabled")
    html = zalo_bridge.render_qr_html("/zalo/status")
    return HTMLResponse(content=html)
