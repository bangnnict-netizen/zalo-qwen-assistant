"""FastAPI entrypoint for Zalo Qwen Assistant."""

from contextlib import asynccontextmanager
from typing import Literal

from fastapi import FastAPI
from pydantic import BaseModel, Field

from app.config import get_settings
from app.services.llm_service import LLMService
from app.services.message_pipeline import MessagePipeline
from app.services.rag_service import RAGService
from app.services.router_service import MessageRouter
from app.services.zalo_bridge import MockZaloBridge

settings = get_settings()
llm_service = LLMService(settings)
rag_service = RAGService()
router = MessageRouter(llm=llm_service, rag=rag_service)
pipeline = MessagePipeline(router=router, settings=settings)
zalo_bridge = MockZaloBridge(settings=settings)


@asynccontextmanager
async def lifespan(_app: FastAPI):
    rag_service.ensure_index()
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
