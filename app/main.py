"""FastAPI entrypoint for Zalo Qwen Assistant."""

from contextlib import asynccontextmanager
from typing import Literal

from fastapi import FastAPI
from pydantic import BaseModel, Field

from app.config import get_settings
from app.services.llm_service import LLMService
from app.services.rag_service import RAGService
from app.services.router_service import MessageRouter

settings = get_settings()
llm_service = LLMService(settings)
rag_service = RAGService()
router = MessageRouter(llm=llm_service, rag=rag_service)


@asynccontextmanager
async def lifespan(_app: FastAPI):
    rag_service.ensure_index()
    yield
    rag_service.close()


app = FastAPI(title="Zalo Qwen Assistant", lifespan=lifespan)


class AskRequest(BaseModel):
    question: str = Field(..., min_length=1)
    group_type: Literal["internal", "customer"] = "customer"


@app.get("/health")
async def health() -> dict[str, str]:
    """Liveness probe for local checks and deployments."""
    return {"status": "ok", "service": "zalo-qwen-assistant"}


@app.post("/ask")
async def ask(payload: AskRequest) -> dict[str, object]:
    """Route a question through persona + RAG + LLM."""
    return await router.route(payload.group_type, payload.question)
