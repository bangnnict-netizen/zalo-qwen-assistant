"""FastAPI entrypoint for Zalo Qwen Assistant."""

from fastapi import FastAPI

from app.config import get_settings

settings = get_settings()

app = FastAPI(title="Zalo Qwen Assistant")


@app.get("/health")
async def health() -> dict[str, str]:
    """Liveness probe for local checks and deployments."""
    return {"status": "ok", "service": "zalo-qwen-assistant"}
