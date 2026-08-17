"""Local RAG over knowledge_base/*.md using SQLite FTS5."""

from __future__ import annotations

import hashlib
import logging
import re
import sqlite3
from pathlib import Path

logger = logging.getLogger(__name__)

PROJECT_ROOT = Path(__file__).resolve().parents[2]
DEFAULT_KB_DIR = PROJECT_ROOT / "knowledge_base"
DEFAULT_DB_PATH = PROJECT_ROOT / "rag.db"

GROUP_FILES = {
    "internal": "kb_internal.md",
}
GROUP_DIRS = {
    "customer": "customer",
}

HEADING_SPLIT_RE = re.compile(r"(?=^##\s+)", re.MULTILINE)
FTS_SPECIAL_RE = re.compile(r"[^\w\s]", re.UNICODE)


class RAGService:
    """Index markdown knowledge base chunks and search by group_type."""

    def __init__(
        self,
        kb_dir: Path | None = None,
        db_path: Path | None = None,
    ) -> None:
        self.kb_dir = kb_dir or DEFAULT_KB_DIR
        self.db_path = db_path or DEFAULT_DB_PATH
        self._conn: sqlite3.Connection | None = None

    def ensure_index(self) -> None:
        """Build or rebuild the FTS index when KB files change."""
        conn = self._get_conn()
        self._ensure_schema(conn)
        fingerprint = self._kb_fingerprint()
        current = conn.execute(
            "SELECT value FROM meta WHERE key = 'fingerprint'"
        ).fetchone()
        if current and current[0] == fingerprint:
            return
        self._rebuild(conn, fingerprint)
        logger.info("RAG index rebuilt for %s", self.kb_dir)

    def search(
        self,
        group_type: str,
        query: str,
        top_k: int = 3,
    ) -> list[dict[str, str]]:
        """Return relevant chunks for the given group_type."""
        if group_type not in GROUP_FILES and group_type not in GROUP_DIRS:
            raise ValueError(f"Unsupported group_type: {group_type}")

        self.ensure_index()
        conn = self._get_conn()
        fts_query = _to_fts_query(query)
        if not fts_query:
            return []

        rows = conn.execute(
            """
            SELECT heading, content, source_file, group_type
            FROM chunks
            WHERE chunks MATCH ?
              AND group_type = ?
            ORDER BY rank
            LIMIT ?
            """,
            (fts_query, group_type, top_k),
        ).fetchall()

        return [
            {
                "heading": row[0],
                "content": row[1],
                "source_file": row[2],
                "group_type": row[3],
            }
            for row in rows
        ]

    def close(self) -> None:
        if self._conn is not None:
            self._conn.close()
            self._conn = None

    def _get_conn(self) -> sqlite3.Connection:
        if self._conn is None:
            self.db_path.parent.mkdir(parents=True, exist_ok=True)
            self._conn = sqlite3.connect(self.db_path, check_same_thread=False)
            self._conn.row_factory = sqlite3.Row
        return self._conn

    def _ensure_schema(self, conn: sqlite3.Connection) -> None:
        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS meta (
                key TEXT PRIMARY KEY,
                value TEXT NOT NULL
            )
            """
        )
        conn.execute(
            """
            CREATE VIRTUAL TABLE IF NOT EXISTS chunks USING fts5(
                group_type UNINDEXED,
                heading,
                content,
                source_file UNINDEXED,
                tokenize = 'unicode61'
            )
            """
        )
        conn.commit()

    def _iter_group_dir_files(self, group_type: str, dir_name: str) -> list[Path]:
        dir_path = self.kb_dir / dir_name
        if not dir_path.is_dir():
            logger.warning("KB directory missing: %s", dir_path)
            return []
        files = sorted(dir_path.glob("*.md"))
        if not files:
            logger.warning("KB directory empty: %s", dir_path)
        return files

    def _kb_fingerprint(self) -> str:
        hasher = hashlib.sha256()
        for group_type, filename in sorted(GROUP_FILES.items()):
            path = self.kb_dir / filename
            if not path.exists():
                hasher.update(f"{group_type}:missing\n".encode())
                continue
            stat = path.stat()
            hasher.update(
                f"{group_type}:{filename}:{stat.st_mtime_ns}:{stat.st_size}\n".encode()
            )
            hasher.update(path.read_bytes())

        for group_type, dir_name in sorted(GROUP_DIRS.items()):
            for path in self._iter_group_dir_files(group_type, dir_name):
                filename = path.name
                stat = path.stat()
                hasher.update(
                    f"{group_type}:{filename}:{stat.st_mtime_ns}:{stat.st_size}\n".encode()
                )
                hasher.update(path.read_bytes())

        return hasher.hexdigest()

    def _rebuild(self, conn: sqlite3.Connection, fingerprint: str) -> None:
        conn.execute("DELETE FROM chunks")
        for group_type, filename in GROUP_FILES.items():
            path = self.kb_dir / filename
            if not path.exists():
                logger.warning("KB file missing: %s", path)
                continue
            for chunk in _split_markdown_sections(path.read_text(encoding="utf-8")):
                conn.execute(
                    """
                    INSERT INTO chunks (group_type, heading, content, source_file)
                    VALUES (?, ?, ?, ?)
                    """,
                    (group_type, chunk["heading"], chunk["content"], filename),
                )

        for group_type, dir_name in GROUP_DIRS.items():
            for path in self._iter_group_dir_files(group_type, dir_name):
                filename = path.name
                for chunk in _split_markdown_sections(path.read_text(encoding="utf-8")):
                    conn.execute(
                        """
                        INSERT INTO chunks (group_type, heading, content, source_file)
                        VALUES (?, ?, ?, ?)
                        """,
                        (group_type, chunk["heading"], chunk["content"], filename),
                    )

        conn.execute(
            """
            INSERT INTO meta(key, value) VALUES('fingerprint', ?)
            ON CONFLICT(key) DO UPDATE SET value = excluded.value
            """,
            (fingerprint,),
        )
        conn.commit()


def _split_markdown_sections(text: str) -> list[dict[str, str]]:
    """Split markdown into chunks on ## headings."""
    parts = HEADING_SPLIT_RE.split(text.strip())
    chunks: list[dict[str, str]] = []
    for part in parts:
        part = part.strip()
        if not part or not part.startswith("##"):
            continue
        lines = part.splitlines()
        heading = lines[0].lstrip("#").strip()
        body = "\n".join(lines[1:]).strip()
        content = f"{heading}\n{body}".strip() if body else heading
        if content:
            chunks.append({"heading": heading, "content": content})
    return chunks


def _to_fts_query(query: str) -> str:
    """Convert free text into a safe FTS5 OR query of tokens."""
    tokens = [t for t in FTS_SPECIAL_RE.sub(" ", query).split() if t]
    if not tokens:
        return ""
    return " OR ".join(tokens)
