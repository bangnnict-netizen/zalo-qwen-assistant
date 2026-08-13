"""Unit tests for RAGService group-scoped FTS search."""

from __future__ import annotations

from pathlib import Path

from app.services.rag_service import RAGService


def _write_kb(kb_dir: Path) -> None:
    kb_dir.mkdir(parents=True, exist_ok=True)
    (kb_dir / "kb_internal.md").write_text(
        "# Nội bộ\n"
        "## Giờ làm việc\n"
        "Nhà máy làm việc từ 7h30 đến 16h30, thứ 2 đến thứ 7.\n"
        "## Quy định an toàn\n"
        "Mọi người vào khu vực chiếu xạ phải đeo dosimeter.\n",
        encoding="utf-8",
    )
    (kb_dir / "kb_customer.md").write_text(
        "# Khách hàng\n"
        "## Thời gian xử lý\n"
        "Thông thường 24 đến 48 giờ kể từ khi nhận hàng.\n"
        "## Dịch vụ chiếu xạ\n"
        "Công ty nhận chiếu xạ khử trùng cho thủy sản, rau củ.\n",
        encoding="utf-8",
    )


def test_search_returns_only_matching_group(tmp_path: Path) -> None:
    kb_dir = tmp_path / "knowledge_base"
    db_path = tmp_path / "rag.db"
    _write_kb(kb_dir)

    rag = RAGService(kb_dir=kb_dir, db_path=db_path)
    rag.ensure_index()

    internal_hits = rag.search("internal", "giờ làm việc nhà máy", top_k=3)
    customer_hits = rag.search("customer", "thời gian xử lý hàng", top_k=3)

    assert internal_hits
    assert all(hit["group_type"] == "internal" for hit in internal_hits)
    assert all(hit["source_file"] == "kb_internal.md" for hit in internal_hits)
    assert any("7h30" in hit["content"] or "16h30" in hit["content"] for hit in internal_hits)

    assert customer_hits
    assert all(hit["group_type"] == "customer" for hit in customer_hits)
    assert all(hit["source_file"] == "kb_customer.md" for hit in customer_hits)
    assert any("24" in hit["content"] or "48" in hit["content"] for hit in customer_hits)

    # Cross-group isolation: internal hours must not appear in customer search.
    assert not any("7h30" in hit["content"] for hit in customer_hits)
    assert not any("dosimeter" in hit["content"] for hit in customer_hits)

    rag.close()


def test_rebuilds_when_kb_file_changes(tmp_path: Path) -> None:
    kb_dir = tmp_path / "knowledge_base"
    db_path = tmp_path / "rag.db"
    _write_kb(kb_dir)

    rag = RAGService(kb_dir=kb_dir, db_path=db_path)
    first = rag.search("internal", "dosimeter", top_k=1)
    assert first

    (kb_dir / "kb_internal.md").write_text(
        "# Nội bộ\n"
        "## Ca đêm\n"
        "Ca đêm bắt đầu lúc 22h00 và kết thúc lúc 6h00.\n",
        encoding="utf-8",
    )
    rag.ensure_index()
    updated = rag.search("internal", "ca đêm 22h00", top_k=3)

    assert updated
    assert any("22h00" in hit["content"] for hit in updated)
    assert not any("dosimeter" in hit["content"] for hit in updated)

    rag.close()
