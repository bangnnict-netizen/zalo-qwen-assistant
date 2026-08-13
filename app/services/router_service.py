"""Route questions by group_type through persona + RAG + LLM."""

from __future__ import annotations

from pathlib import Path

from app.services.llm_service import LLMService
from app.services.rag_service import RAGService

PROJECT_ROOT = Path(__file__).resolve().parents[2]
DEFAULT_PROMPTS_DIR = PROJECT_ROOT / "prompts"

PERSONA_FILES = {
    "internal": "persona_internal.md",
    "customer": "persona_customer.md",
}

UNCERTAIN_HINT = (
    "Nếu không chắc chắn, hãy nói em chưa rõ và hướng dẫn liên hệ người phụ trách."
)


class MessageRouter:
    """Compose persona + RAG context and call the LLM."""

    def __init__(
        self,
        llm: LLMService | None = None,
        rag: RAGService | None = None,
        prompts_dir: Path | None = None,
    ) -> None:
        self.llm = llm or LLMService()
        self.rag = rag or RAGService()
        self.prompts_dir = prompts_dir or DEFAULT_PROMPTS_DIR

    async def route(
        self,
        group_type: str,
        question: str,
        honorific: str | None = None,
    ) -> dict[str, object]:
        if group_type not in PERSONA_FILES:
            raise ValueError(f"Unsupported group_type: {group_type}")

        persona = self._load_persona(group_type)
        sources = self.rag.search(group_type, question, top_k=3)

        system_parts = [persona]
        if honorific:
            gender_hint = {"anh": "nam", "chị": "nữ"}.get(
                honorific, "chưa rõ giới tính"
            )
            system_parts.append(
                f"Người hỏi là {gender_hint}, hãy gọi là '{honorific}'."
            )
        if sources:
            docs = "\n\n".join(
                f"### {item['heading']}\n{item['content']}" for item in sources
            )
            system_parts.append(f"TÀI LIỆU THAM KHẢO:\n{docs}")
        else:
            system_parts.append(UNCERTAIN_HINT)

        system = "\n\n".join(system_parts)
        llm_result = await self.llm.chat(question, system=system)

        return {
            "answer": llm_result["answer"],
            "model_used": llm_result["model_used"],
            "sources": sources,
        }

    def _load_persona(self, group_type: str) -> str:
        path = self.prompts_dir / PERSONA_FILES[group_type]
        return path.read_text(encoding="utf-8").strip()
