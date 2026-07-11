"""벡터 검색 엔진 — sqlite-vec + sentence-transformers."""
from __future__ import annotations

import json
import sqlite3
import struct
from pathlib import Path

from ai_wiki.models import Article
from ai_wiki.storage import get_data_dir

# 모델은 최초 사용 시 lazy 로드
_model = None
_DIMS = 384  # paraphrase-multilingual-MiniLM-L12-v2 출력 차원
_MODEL_NAME = "paraphrase-multilingual-MiniLM-L12-v2"


def _get_model():
    global _model
    if _model is None:
        from sentence_transformers import SentenceTransformer
        _model = SentenceTransformer(_MODEL_NAME)
    return _model


def _serialize_f32(vector: list[float]) -> bytes:
    """float32 리스트를 sqlite-vec에 맞는 바이트로 직렬화."""
    return struct.pack(f"{len(vector)}f", *vector)


def _article_text(article: Article) -> str:
    """검색용 텍스트 생성 — 제목 + 태그 + content 텍스트."""
    parts = [article.title]
    parts.extend(article.tags)
    parts.append(article.content_as_text())
    return " ".join(parts)[:2000]  # 토큰 제한


class VectorIndex:
    def __init__(self, db_path: Path | None = None):
        if db_path is None:
            db_path = get_data_dir() / "vectors.db"
        self.db_path = db_path
        self.conn = sqlite3.connect(str(db_path), check_same_thread=False)
        self.conn.enable_load_extension(True)
        import sqlite_vec
        sqlite_vec.load(self.conn)
        self.conn.enable_load_extension(False)
        self._initialize()

    def _initialize(self):
        self.conn.executescript(f"""
            CREATE VIRTUAL TABLE IF NOT EXISTS article_vectors USING vec0(
                id TEXT PRIMARY KEY,
                embedding float[{_DIMS}]
            );
        """)
        # 메타 테이블: id → title 매핑 (vec0은 메타 저장 불가)
        self.conn.execute("""
            CREATE TABLE IF NOT EXISTS vector_meta (
                id TEXT PRIMARY KEY,
                title TEXT NOT NULL,
                category TEXT NOT NULL DEFAULT ''
            )
        """)
        self.conn.commit()

    def upsert(self, article: Article) -> None:
        """문서 임베딩 생성 후 저장."""
        model = _get_model()
        text = _article_text(article)
        embedding = model.encode(text).tolist()

        # vec0은 INSERT OR REPLACE 미지원 → DELETE 후 INSERT
        self.conn.execute("DELETE FROM article_vectors WHERE id = ?", (article.id,))
        self.conn.execute(
            "INSERT INTO article_vectors (id, embedding) VALUES (?, ?)",
            (article.id, _serialize_f32(embedding)),
        )
        self.conn.execute(
            "INSERT OR REPLACE INTO vector_meta (id, title, category) VALUES (?, ?, ?)",
            (article.id, article.title, article.category),
        )
        self.conn.commit()

    def remove(self, article_id: str) -> None:
        self.conn.execute("DELETE FROM article_vectors WHERE id = ?", (article_id,))
        self.conn.execute("DELETE FROM vector_meta WHERE id = ?", (article_id,))
        self.conn.commit()

    def search(self, query: str, limit: int = 10) -> list[dict]:
        """의미 기반 유사 검색."""
        model = _get_model()
        q_embedding = model.encode(query).tolist()

        rows = self.conn.execute(
            """
            SELECT v.id, v.distance
            FROM article_vectors v
            WHERE v.embedding MATCH ?
            ORDER BY v.distance
            LIMIT ?
            """,
            (_serialize_f32(q_embedding), limit),
        ).fetchall()

        results = []
        for row_id, distance in rows:
            meta = self.conn.execute(
                "SELECT title, category FROM vector_meta WHERE id = ?", (row_id,)
            ).fetchone()
            title, category = meta if meta else ("", "")
            # sqlite-vec distance: 낮을수록 유사. 0에 가까울수록 동일.
            similarity = round(max(0.0, 1.0 / (1.0 + abs(distance))), 4)
            results.append({
                "id": row_id,
                "title": title,
                "category": category,
                "distance": round(distance, 4),
                "similarity": similarity,
            })
        return results

    def rebuild(self, articles: list[Article]) -> int:
        """전체 재구축."""
        self.conn.execute("DELETE FROM article_vectors")
        self.conn.execute("DELETE FROM vector_meta")
        self.conn.commit()

        for article in articles:
            self.upsert(article)
        return len(articles)

    def count(self) -> int:
        row = self.conn.execute("SELECT COUNT(*) FROM vector_meta").fetchone()
        return row[0] if row else 0

    def close(self):
        self.conn.close()
