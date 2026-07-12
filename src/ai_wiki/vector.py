"""Chunk-level semantic retrieval backed by sqlite-vec and sentence-transformers."""
from __future__ import annotations

import sqlite3
import struct
import math
import os
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Iterable

from ai_wiki.chunking import ArticleChunk, article_chunks
from ai_wiki.models import Article
from ai_wiki.storage import get_data_dir

_model = None
_MODEL_NAME = os.environ.get(
    "AI_WIKI_EMBEDDING_MODEL", "intfloat/multilingual-e5-small"
)
_KNOWN_DIMENSIONS = {
    "paraphrase-multilingual-MiniLM-L12-v2": 384,
    "intfloat/multilingual-e5-small": 384,
    "intfloat/multilingual-e5-base": 768,
    "intfloat/multilingual-e5-large": 1024,
    "BAAI/bge-m3": 1024,
}
_DIMS = int(os.environ.get(
    "AI_WIKI_EMBEDDING_DIMENSIONS", str(_KNOWN_DIMENSIONS.get(_MODEL_NAME, 384))
))
_EMBEDDING_VERSION = 2
_BATCH_SIZE = 64


class VectorSearchUnavailable(RuntimeError):
    """Raised when required semantic retrieval cannot run."""

    def __init__(self, code: str, message: str):
        super().__init__(message)
        self.code = code


def _get_model():
    global _model
    if _model is None:
        from sentence_transformers import SentenceTransformer
        _model = SentenceTransformer(_MODEL_NAME)
    return _model


def _serialize_f32(vector: list[float]) -> bytes:
    return struct.pack(f"{len(vector)}f", *vector)


def _encode_texts(texts: list[str], *, query: bool = False) -> list[list[float]]:
    if not texts:
        return []
    if "e5" in _MODEL_NAME.casefold():
        prefix = "query: " if query else "passage: "
        texts = [prefix + text for text in texts]
    encoded = _get_model().encode(
        texts,
        batch_size=_BATCH_SIZE,
        normalize_embeddings=True,
        show_progress_bar=False,
    )
    vectors = encoded.tolist() if hasattr(encoded, "tolist") else encoded
    if vectors and isinstance(vectors[0], (int, float)):
        vectors = [vectors]
    output = [[float(value) for value in vector] for vector in vectors]
    if any(len(vector) != _DIMS for vector in output):
        raise ValueError(f"embedding dimension must be {_DIMS}")
    return output


class VectorIndex:
    def __init__(self, db_path: Path | None = None):
        self.db_path = db_path or (get_data_dir() / "vectors.db")
        self.db_path.parent.mkdir(parents=True, exist_ok=True)
        self.conn = sqlite3.connect(str(self.db_path), check_same_thread=False)
        self.conn.row_factory = sqlite3.Row
        self.conn.execute("PRAGMA journal_mode = WAL")
        self.conn.enable_load_extension(True)
        import sqlite_vec
        sqlite_vec.load(self.conn)
        self.conn.enable_load_extension(False)
        self._initialize()

    def _initialize(self) -> None:
        self.compatible = True
        self.rebuild_reason: str | None = None
        self.conn.executescript(f"""
            CREATE VIRTUAL TABLE IF NOT EXISTS chunk_vectors USING vec0(
                chunk_id TEXT PRIMARY KEY,
                embedding float[{_DIMS}]
            );
            CREATE TABLE IF NOT EXISTS chunk_meta (
                chunk_id TEXT PRIMARY KEY,
                document_id TEXT NOT NULL,
                path TEXT NOT NULL,
                text TEXT NOT NULL,
                content_hash TEXT NOT NULL,
                ordinal INTEGER NOT NULL,
                part INTEGER NOT NULL DEFAULT 0,
                document_version INTEGER NOT NULL,
                embedding_model TEXT NOT NULL,
                embedding_version INTEGER NOT NULL
            );
            CREATE INDEX IF NOT EXISTS idx_chunk_meta_document
                ON chunk_meta(document_id, ordinal);
            CREATE TABLE IF NOT EXISTS document_meta (
                document_id TEXT PRIMARY KEY,
                title TEXT NOT NULL,
                category TEXT NOT NULL,
                tags TEXT NOT NULL,
                document_version INTEGER NOT NULL
            );
            CREATE TABLE IF NOT EXISTS vector_state (
                key TEXT PRIMARY KEY,
                value TEXT NOT NULL
            );
        """)
        stored_dimensions = self.conn.execute(
            "SELECT value FROM vector_state WHERE key = 'dimensions'"
        ).fetchone()
        stored_model = self.conn.execute(
            "SELECT value FROM vector_state WHERE key = 'embedding_model'"
        ).fetchone()
        dimensions_changed = bool(stored_dimensions and int(stored_dimensions[0]) != _DIMS)
        model_changed = bool(stored_model and stored_model[0] != _MODEL_NAME)
        if dimensions_changed or model_changed:
            self.compatible = False
            self.rebuild_reason = (
                "embedding_dimensions_changed" if dimensions_changed else "embedding_model_changed"
            )
            self.conn.commit()
            return
        self.conn.executemany(
            "INSERT OR REPLACE INTO vector_state(key, value) VALUES (?, ?)",
            [
                ("embedding_model", _MODEL_NAME),
                ("embedding_version", str(_EMBEDDING_VERSION)),
                ("dimensions", str(_DIMS)),
            ],
        )
        self.conn.execute(
            "INSERT OR IGNORE INTO vector_state(key, value) VALUES ('index_revision', '0')"
        )
        self.conn.commit()

    def _require_compatible(self) -> None:
        if not self.compatible:
            raise VectorSearchUnavailable(
                self.rebuild_reason or "vector_index_incompatible",
                "Vector index was built with a different embedding configuration; run vindex",
            )

    def _touch_revision(self) -> None:
        row = self.conn.execute(
            "SELECT value FROM vector_state WHERE key = 'index_revision'"
        ).fetchone()
        revision = int(row[0]) + 1 if row else 1
        self.conn.execute(
            "INSERT OR REPLACE INTO vector_state(key, value) VALUES ('index_revision', ?)",
            (str(revision),),
        )

    def _document_is_current(self, article: Article, chunks: list[ArticleChunk]) -> bool:
        rows = self.conn.execute(
            """SELECT chunk_id, content_hash, document_version, embedding_model,
                      embedding_version FROM chunk_meta WHERE document_id = ?""",
            (article.id,),
        ).fetchall()
        if len(rows) != len(chunks):
            return False
        expected = {chunk.chunk_id: chunk.content_hash for chunk in chunks}
        return all(
            row["chunk_id"] in expected
            and row["content_hash"] == expected[row["chunk_id"]]
            and row["document_version"] == article.version
            and row["embedding_model"] == _MODEL_NAME
            and row["embedding_version"] == _EMBEDDING_VERSION
            for row in rows
        )

    def _replace_document(self, article: Article, chunks: list[ArticleChunk],
                          vectors: list[list[float]]) -> None:
        try:
            self.conn.execute("BEGIN IMMEDIATE")
            self._delete_document_rows(article.id)
            self._insert_document_rows(article, chunks, vectors)
            self._touch_revision()
            self.conn.commit()
        except Exception:
            self.conn.rollback()
            raise

    def _delete_document_rows(self, article_id: str) -> None:
        chunk_ids = [
            row[0] for row in self.conn.execute(
                "SELECT chunk_id FROM chunk_meta WHERE document_id = ?", (article_id,)
            ).fetchall()
        ]
        for chunk_id in chunk_ids:
            self.conn.execute("DELETE FROM chunk_vectors WHERE chunk_id = ?", (chunk_id,))
        self.conn.execute("DELETE FROM chunk_meta WHERE document_id = ?", (article_id,))
        self.conn.execute("DELETE FROM document_meta WHERE document_id = ?", (article_id,))

    def _insert_document_rows(self, article: Article, chunks: list[ArticleChunk],
                              vectors: list[list[float]]) -> None:
        self.conn.execute(
            """INSERT INTO document_meta
               (document_id, title, category, tags, document_version)
               VALUES (?, ?, ?, ?, ?)""",
            (article.id, article.title, article.category, " ".join(article.tags), article.version),
        )
        for chunk, vector in zip(chunks, vectors):
            self.conn.execute(
                "INSERT INTO chunk_vectors(chunk_id, embedding) VALUES (?, ?)",
                (chunk.chunk_id, _serialize_f32(vector)),
            )
            self.conn.execute(
                """INSERT INTO chunk_meta
                   (chunk_id, document_id, path, text, content_hash, ordinal, part,
                    document_version, embedding_model, embedding_version)
                   VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                (
                    chunk.chunk_id, chunk.document_id, chunk.path, chunk.text,
                    chunk.content_hash, chunk.ordinal, chunk.part, article.version,
                    _MODEL_NAME, _EMBEDDING_VERSION,
                ),
            )

    def upsert(self, article: Article) -> dict:
        self._require_compatible()
        chunks = article_chunks(article)
        if self._document_is_current(article, chunks):
            return {"document_id": article.id, "chunks": len(chunks), "embedded": 0}
        vectors = _encode_texts([chunk.indexed_text for chunk in chunks])
        self._replace_document(article, chunks, vectors)
        return {"document_id": article.id, "chunks": len(chunks), "embedded": len(chunks)}

    def upsert_many(self, articles: Iterable[Article], *, rebuild: bool = False) -> dict:
        self._require_compatible()
        article_list = list(articles)
        prepared: list[tuple[Article, list[ArticleChunk]]] = []
        skipped = 0
        for article in article_list:
            chunks = article_chunks(article)
            if not rebuild and self._document_is_current(article, chunks):
                skipped += 1
                continue
            prepared.append((article, chunks))

        all_chunks = [chunk for _, chunks in prepared for chunk in chunks]
        vectors = _encode_texts([chunk.indexed_text for chunk in all_chunks])
        offset = 0
        if rebuild:
            try:
                self.conn.execute("BEGIN IMMEDIATE")
                self.conn.execute("DELETE FROM chunk_vectors")
                self.conn.execute("DELETE FROM chunk_meta")
                self.conn.execute("DELETE FROM document_meta")
                offset = 0
                for article, chunks in prepared:
                    next_offset = offset + len(chunks)
                    self._insert_document_rows(article, chunks, vectors[offset:next_offset])
                    offset = next_offset
                self._touch_revision()
                self.conn.commit()
            except Exception:
                self.conn.rollback()
                raise
        else:
            for article, chunks in prepared:
                next_offset = offset + len(chunks)
                self._replace_document(article, chunks, vectors[offset:next_offset])
                offset = next_offset
        return {
            "documents": len(article_list),
            "updated_documents": len(prepared),
            "skipped_documents": skipped,
            "chunks": self.chunk_count(),
            "embedded_chunks": len(all_chunks),
        }

    def remove(self, article_id: str) -> None:
        try:
            self.conn.execute("BEGIN IMMEDIATE")
            self._delete_document_rows(article_id)
            self._touch_revision()
            self.conn.commit()
        except Exception:
            self.conn.rollback()
            raise

    def search_chunks(self, query: str, limit: int = 50) -> list[dict]:
        self._require_compatible()
        if self.chunk_count() == 0:
            return []
        query_vector = _encode_texts([query], query=True)[0]
        rows = self.conn.execute(
            """SELECT v.chunk_id, v.distance
               FROM chunk_vectors v
               WHERE v.embedding MATCH ?
               ORDER BY v.distance
               LIMIT ?""",
            (_serialize_f32(query_vector), limit),
        ).fetchall()
        results: list[dict] = []
        for row in rows:
            meta = self.conn.execute(
                """SELECT document_id, path, text, ordinal, part
                   FROM chunk_meta WHERE chunk_id = ?""",
                (row["chunk_id"],),
            ).fetchone()
            if not meta:
                continue
            distance = float(row["distance"])
            cosine_similarity = max(-1.0, min(1.0, 1.0 - (distance * distance) / 2.0))
            results.append({
                "chunk_id": row["chunk_id"],
                "document_id": meta["document_id"],
                "path": meta["path"],
                "text": meta["text"],
                "ordinal": meta["ordinal"],
                "part": meta["part"],
                "distance": round(distance, 6),
                "vector_similarity": round(cosine_similarity, 6),
                "similarity": round((cosine_similarity + 1.0) / 2.0, 6),
                "retrieval_source": "vector",
            })
        return results

    def search(self, query: str, limit: int = 10, *, chunks_per_document: int = 3) -> list[dict]:
        chunk_hits = self.search_chunks(query, limit=max(50, limit * 8))
        grouped: dict[str, list[dict]] = {}
        order: list[str] = []
        for hit in chunk_hits:
            document_id = hit["document_id"]
            if document_id not in grouped:
                grouped[document_id] = []
                order.append(document_id)
            if len(grouped[document_id]) < max(8, chunks_per_document * 2):
                grouped[document_id].append(hit)
        output: list[dict] = []
        for document_id in order[:limit]:
            raw_hits = grouped[document_id]
            hits = [raw_hits[0]]
            content_count = int(raw_hits[0]["path"].startswith("/content/data"))
            for hit in raw_hits[1:]:
                is_content = hit["path"].startswith("/content/data")
                if not is_content:
                    continue
                if hit["chunk_id"] != hits[0]["chunk_id"]:
                    hits.append(hit)
                    content_count += 1
                if content_count >= chunks_per_document:
                    break
            meta = self.conn.execute(
                "SELECT title, category FROM document_meta WHERE document_id = ?", (document_id,)
            ).fetchone()
            output.append({
                "id": document_id,
                "title": meta["title"] if meta else "",
                "category": meta["category"] if meta else "",
                "distance": hits[0]["distance"],
                "vector_similarity": hits[0]["vector_similarity"],
                "similarity": hits[0]["similarity"],
                "acceptance_score": self._acceptance_score(hits[0]["vector_similarity"]),
                "calibration_status": "calibrated" if self._calibration() else "uncalibrated",
                "matched_chunks": hits,
            })
        return output

    def _calibration(self) -> tuple[float, float] | None:
        rows = dict(self.conn.execute(
            """SELECT key, value FROM vector_state
               WHERE key IN ('calibration_a', 'calibration_b', 'calibration_revision', 'index_revision')"""
        ).fetchall())
        if "calibration_a" not in rows or "calibration_b" not in rows:
            return None
        if rows.get("calibration_revision") != rows.get("index_revision"):
            return None
        return float(rows["calibration_a"]), float(rows["calibration_b"])

    def _acceptance_score(self, similarity: float) -> float | None:
        calibration = self._calibration()
        if calibration is None:
            return None
        a, b = calibration
        value = max(-60.0, min(60.0, a * similarity + b))
        return round(1.0 / (1.0 + math.exp(-value)), 6)

    def calibrate(self, samples: list[tuple[float, int]], *, iterations: int = 2000,
                  learning_rate: float = 0.05) -> dict:
        """Fit Platt-style logistic calibration from labeled corpus retrieval scores."""
        if len(samples) < 20 or {label for _, label in samples} != {0, 1}:
            raise ValueError("calibration requires at least 20 positive and negative labeled samples")
        positives = sum(label for _, label in samples)
        negatives = len(samples) - positives
        positive_weight = len(samples) / (2.0 * positives)
        negative_weight = len(samples) / (2.0 * negatives)
        a = 1.0
        b = 0.0
        for _ in range(iterations):
            grad_a = 0.0
            grad_b = 0.0
            for score, label in samples:
                prediction = 1.0 / (1.0 + math.exp(-max(-60.0, min(60.0, a * score + b))))
                weight = positive_weight if label else negative_weight
                error = (prediction - label) * weight
                grad_a += error * score
                grad_b += error
            a -= learning_rate * grad_a / len(samples)
            b -= learning_rate * grad_b / len(samples)
        now = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
        revision_row = self.conn.execute(
            "SELECT value FROM vector_state WHERE key = 'index_revision'"
        ).fetchone()
        revision = revision_row[0] if revision_row else "0"
        self.conn.executemany(
            "INSERT OR REPLACE INTO vector_state(key, value) VALUES (?, ?)",
            [
                ("calibration_a", str(a)), ("calibration_b", str(b)),
                ("calibration_samples", str(len(samples))), ("calibrated_at", now),
                ("calibration_revision", revision),
            ],
        )
        self.conn.commit()
        return {
            "status": "calibrated", "a": a, "b": b,
            "samples": len(samples), "positives": positives, "negatives": negatives,
            "calibrated_at": now,
        }

    def rebuild(self, articles: list[Article]) -> int:
        self.upsert_many(articles, rebuild=True)
        return len(articles)

    @classmethod
    def rebuild_atomic(cls, articles: Iterable[Article], db_path: Path | None = None) -> dict:
        """Build a complete sibling DB and replace the old derived index only on success."""
        target = db_path or (get_data_dir() / "vectors.db")
        target.parent.mkdir(parents=True, exist_ok=True)
        temporary = target.with_name(f".{target.name}.{uuid.uuid4().hex}.rebuild")
        staged = None
        try:
            staged = cls(db_path=temporary)
            details = staged.upsert_many(list(articles), rebuild=True)
            staged.conn.execute("PRAGMA wal_checkpoint(TRUNCATE)").fetchone()
            staged.conn.execute("PRAGMA journal_mode = DELETE").fetchone()
            staged.close()
            staged = None
            for suffix in ("-wal", "-shm"):
                Path(str(target) + suffix).unlink(missing_ok=True)
            os.replace(temporary, target)
            return details
        finally:
            if staged is not None:
                staged.close()
            temporary.unlink(missing_ok=True)
            Path(str(temporary) + "-wal").unlink(missing_ok=True)
            Path(str(temporary) + "-shm").unlink(missing_ok=True)

    def count(self) -> int:
        row = self.conn.execute("SELECT COUNT(DISTINCT document_id) FROM chunk_meta").fetchone()
        return int(row[0]) if row else 0

    def chunk_count(self) -> int:
        row = self.conn.execute("SELECT COUNT(*) FROM chunk_meta").fetchone()
        return int(row[0]) if row else 0

    def state(self) -> dict:
        state = {
            "embedding_model": _MODEL_NAME,
            "embedding_version": _EMBEDDING_VERSION,
            "dimensions": _DIMS,
            "document_count": self.count(),
            "chunk_count": self.chunk_count(),
            "rebuild_required": not self.compatible,
            "rebuild_reason": self.rebuild_reason,
        }
        calibration = dict(self.conn.execute(
            """SELECT key, value FROM vector_state
               WHERE key IN ('calibration_samples', 'calibrated_at')"""
        ).fetchall())
        state["calibration_status"] = (
            "uncalibrated" if not self.compatible
            else ("calibrated" if self._calibration() else "uncalibrated")
        )
        if calibration:
            state["calibration_samples"] = int(calibration.get("calibration_samples", 0))
            state["calibrated_at"] = calibration.get("calibrated_at")
        return state

    def close(self) -> None:
        self.conn.close()
