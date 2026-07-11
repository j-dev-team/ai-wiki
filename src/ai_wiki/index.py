from __future__ import annotations

import json
import sqlite3
from datetime import datetime, timedelta, timezone
from pathlib import Path

from ai_wiki.models import Article
from ai_wiki.storage import get_data_dir


class WikiIndex:
    def __init__(self, db_path: Path | None = None):
        if db_path is None:
            db_path = get_data_dir() / "wiki.db"
        self.db_path = db_path
        self.conn = sqlite3.connect(str(db_path), check_same_thread=False)
        self.conn.row_factory = sqlite3.Row
        self.conn.execute("PRAGMA foreign_keys = ON")
        self.conn.execute("PRAGMA journal_mode = WAL")
        self.initialize()

    def initialize(self) -> None:
        cur = self.conn.cursor()
        cur.executescript("""
            CREATE TABLE IF NOT EXISTS articles_meta (
                id TEXT PRIMARY KEY,
                title TEXT NOT NULL,
                category TEXT NOT NULL,
                tags TEXT,
                confidence REAL DEFAULT 0.8,
                version INTEGER DEFAULT 1,
                created_at TEXT NOT NULL,
                last_modified TEXT NOT NULL,
                last_verified TEXT NOT NULL,
                author TEXT DEFAULT 'unknown',
                file_path TEXT NOT NULL,
                content_type TEXT DEFAULT ''
            );

            CREATE VIRTUAL TABLE IF NOT EXISTS articles_fts USING fts5(
                id UNINDEXED,
                title,
                category,
                tags,
                content_text,
                tokenize='unicode61 remove_diacritics 2'
            );

            CREATE TABLE IF NOT EXISTS article_relations (
                from_id TEXT NOT NULL,
                to_id TEXT NOT NULL,
                PRIMARY KEY (from_id, to_id)
            );

            CREATE TABLE IF NOT EXISTS article_sources (
                article_id TEXT NOT NULL,
                url TEXT NOT NULL,
                PRIMARY KEY (article_id, url)
            );

            CREATE TABLE IF NOT EXISTS access_log (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                event_type TEXT NOT NULL,
                article_id TEXT,
                query TEXT,
                result_count INTEGER DEFAULT 0,
                timestamp TEXT NOT NULL
            );
        """)
        # 기존 DB 마이그레이션: 새 컬럼 추가
        for col, definition in [
            ("maturity", "TEXT DEFAULT 'stub'"),
            ("quality_score", "REAL DEFAULT 0.0"),
            ("completeness", "REAL DEFAULT 0.0"),
            ("human_verified", "INTEGER DEFAULT 0"),
            ("human_verified_by", "TEXT DEFAULT NULL"),
            ("human_verified_at", "TEXT DEFAULT NULL"),
        ]:
            try:
                cur.execute(f"ALTER TABLE articles_meta ADD COLUMN {col} {definition}")
            except sqlite3.OperationalError:
                pass
        self.conn.commit()

    def upsert(self, article: Article, file_path: str = "") -> None:
        cur = self.conn.cursor()

        # surrogate 문자(lone surrogate, \udcXX 등)를 SQLite에 넣기 전에 제거한다.
        # Windows 환경에서 cp949<->utf-8 인코딩 불일치로 발생하는 UnicodeEncodeError 방지.
        def _clean(s: str) -> str:
            return s.encode("utf-8", errors="replace").decode("utf-8", errors="replace")

        tags_json = json.dumps(
            [_clean(t) for t in article.tags], ensure_ascii=False
        )
        fmt = Article._fmt
        content_type = article.content.get("type", "") if isinstance(article.content, dict) else ""
        content_text = article.content_as_text()  # 이미 models.py에서 1차 정제됨

        # 품질/성숙도 계산
        from ai_wiki.quality import validate as _validate
        _report = _validate(article)

        cur.execute(
            """INSERT OR REPLACE INTO articles_meta
               (id, title, category, tags, confidence, version,
                created_at, last_modified, last_verified, author, file_path, content_type,
                maturity, quality_score, completeness)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
            (
                article.id, _clean(article.title), _clean(article.category), tags_json,
                article.confidence, article.version,
                fmt(article.created_at), fmt(article.last_modified),
                fmt(article.last_verified), _clean(article.author), file_path,
                _clean(content_type),
                _report.maturity, _report.quality_score,
                _report.quality_score,  # completeness ≈ quality_score for now
            ),
        )

        cur.execute("DELETE FROM articles_fts WHERE id = ?", (article.id,))
        cur.execute(
            "INSERT INTO articles_fts (id, title, category, tags, content_text) VALUES (?, ?, ?, ?, ?)",
            (article.id, _clean(article.title), _clean(article.category),
             _clean(" ".join(article.tags)), _clean(content_text)),
        )

        # #14: 관계 테이블 동기화
        cur.execute("DELETE FROM article_relations WHERE from_id = ?", (article.id,))
        for rel_id in article.related:
            cur.execute(
                "INSERT OR IGNORE INTO article_relations (from_id, to_id) VALUES (?, ?)",
                (article.id, rel_id),
            )

        cur.execute("DELETE FROM article_sources WHERE article_id = ?", (article.id,))
        for url in article.sources:
            cur.execute(
                "INSERT OR IGNORE INTO article_sources (article_id, url) VALUES (?, ?)",
                (article.id, url),
            )

        self.conn.commit()

    def remove(self, article_id: str) -> None:
        cur = self.conn.cursor()
        cur.execute("DELETE FROM articles_meta WHERE id = ?", (article_id,))
        cur.execute("DELETE FROM articles_fts WHERE id = ?", (article_id,))
        cur.execute("DELETE FROM article_relations WHERE from_id = ? OR to_id = ?",
                     (article_id, article_id))
        cur.execute("DELETE FROM article_sources WHERE article_id = ?", (article_id,))
        self.conn.commit()

    def search(
        self,
        query: str,
        category: str | None = None,
        tags: list[str] | None = None,
        limit: int = 20,
        min_confidence: float = 0.0,
    ) -> list[dict]:
        """하이브리드 검색: FTS5 + 벡터 의미검색을 RRF로 결합하여 반환.

        벡터 DB(vectors.db)가 없거나 비어있으면 FTS5 전용으로 폴백.
        RRF 공식: score = Σ 1/(k + rank_i),  k=60,  rank_i 는 1-based 순위.
        """
        # 1. FTS5 검색
        fts_results = self._search_fts(
            query, category=category, tags=tags,
            limit=limit, min_confidence=min_confidence,
        )

        # 2. 벡터 검색 (벡터 DB 없으면 폴백)
        vec_results = self._search_vector(query, limit=limit)

        # 벡터 결과가 없으면 FTS 결과만 반환 (폴백)
        if not vec_results:
            return fts_results

        # 3. 하이브리드 병합 — RRF(Reciprocal Rank Fusion), k=60
        # score = Σ 1/(k + rank_i),  rank_i 는 각 결과 목록에서의 1-based 순위
        RRF_K = 60
        merged: dict[str, dict] = {}

        # FTS 결과: _search_fts 가 ORDER BY rank 로 반환 → 1-based 순위 부여
        for fts_rank_i, r in enumerate(fts_results, start=1):
            rrf_fts = 1.0 / (RRF_K + fts_rank_i)
            merged[r["id"]] = {
                **r,
                "_vec_only": False,
                "_rrf_score": rrf_fts,
            }

        # 벡터 결과: similarity 내림차순 정렬 가정 → 1-based 순위 부여
        for vec_rank_i, v in enumerate(vec_results, start=1):
            vid = v["id"]
            rrf_vec = 1.0 / (RRF_K + vec_rank_i)
            if vid in merged:
                # 양쪽에 존재 → RRF 점수 합산
                merged[vid]["_rrf_score"] += rrf_vec
            else:
                # 벡터에만 존재 → 카테고리 필터 적용
                if category:
                    v_cat = v.get("category", "")
                    if v_cat != category and not v_cat.startswith(category + "/"):
                        continue
                merged[vid] = {
                    "id": vid,
                    "title": v.get("title", ""),
                    "category": v.get("category", ""),
                    "tags": [],
                    "confidence": 0.0,
                    "last_modified": "",
                    "version": 1,
                    "author": "",
                    "type": "",
                    "snippet": "",
                    "rank": 0.0,
                    "_vec_only": True,
                    "_rrf_score": rrf_vec,
                }

        # 4. 벡터 전용 결과의 메타 보완
        vec_only_ids = [
            aid for aid, d in merged.items() if d["_vec_only"]
        ]
        if vec_only_ids:
            cur = self.conn.cursor()
            placeholders = ",".join("?" for _ in vec_only_ids)
            cur.execute(
                f"""SELECT id, title, category, tags, confidence,
                           last_modified, version, author, content_type
                    FROM articles_meta WHERE id IN ({placeholders})""",
                vec_only_ids,
            )
            to_remove: list[str] = []
            for row in cur.fetchall():
                rd = dict(row)
                aid = rd["id"]
                if aid not in merged:
                    continue
                row_tags = json.loads(rd.get("tags") or "[]")
                if tags and not set(tags).intersection(set(row_tags)):
                    to_remove.append(aid)
                    continue
                if min_confidence > 0 and rd["confidence"] < min_confidence:
                    to_remove.append(aid)
                    continue
                merged[aid].update({
                    "title": rd["title"],
                    "category": rd["category"],
                    "tags": row_tags,
                    "confidence": rd["confidence"],
                    "last_modified": rd["last_modified"],
                    "version": rd["version"],
                    "author": rd["author"],
                    "type": rd["content_type"],
                })
            for aid in to_remove:
                del merged[aid]

        # 5. 최종 정렬 및 반환 (RRF 점수 내림차순)
        sorted_results = sorted(
            merged.values(),
            key=lambda x: x["_rrf_score"],
            reverse=True,
        )

        output = []
        for item in sorted_results[:limit]:
            output.append({
                "id": item["id"],
                "title": item["title"],
                "category": item["category"],
                "tags": item["tags"],
                "confidence": item["confidence"],
                "last_modified": item["last_modified"],
                "version": item["version"],
                "author": item["author"],
                "type": item["type"],
                "snippet": item["snippet"],
                "rank": item["rank"],
                "hybrid_score": round(item["_rrf_score"], 4),
            })
        return output

    def _search_fts(
        self,
        query: str,
        category: str | None = None,
        tags: list[str] | None = None,
        limit: int = 20,
        min_confidence: float = 0.0,
    ) -> list[dict]:
        """FTS5 전용 검색 (내부 메서드)."""
        cur = self.conn.cursor()

        # #3: 공백을 OR로 분할하여 다중 키워드 검색 지원
        fts_query = " OR ".join(query.split()) if " " in query else query

        sql = """
            SELECT m.id, m.title, m.category, m.tags, m.confidence,
                   m.last_modified, m.version, m.author, m.content_type,
                   snippet(articles_fts, 4, '>>>', '<<<', '...', 40) as snippet,
                   rank
            FROM articles_fts f
            JOIN articles_meta m ON f.id = m.id
            WHERE articles_fts MATCH ?
        """
        params: list = [fts_query]

        if category:
            sql += " AND (m.category = ? OR m.category LIKE ? || '/%')"
            params.extend([category, category])

        if min_confidence > 0:
            sql += " AND m.confidence >= ?"
            params.append(min_confidence)

        sql += " ORDER BY rank LIMIT ?"
        params.append(limit)

        try:
            cur.execute(sql, params)
        except sqlite3.OperationalError:
            return []

        results = []
        for row in cur.fetchall():
            row_dict = dict(row)
            row_tags = json.loads(row_dict.get("tags", "[]"))

            if tags and not set(tags).intersection(set(row_tags)):
                continue

            results.append({
                "id": row_dict["id"],
                "title": row_dict["title"],
                "category": row_dict["category"],
                "tags": row_tags,
                "confidence": row_dict["confidence"],
                "last_modified": row_dict["last_modified"],
                "version": row_dict["version"],
                "author": row_dict["author"],
                "type": row_dict["content_type"],
                "snippet": row_dict["snippet"],
                "rank": row_dict["rank"],
            })

        return results

    def _search_vector(self, query: str, limit: int = 20) -> list[dict]:
        """벡터 검색 (내부 메서드). 벡터 DB 없거나 비어있으면 빈 리스트 반환."""
        vec_db_path = get_data_dir() / "vectors.db"
        if not vec_db_path.exists():
            return []
        try:
            from ai_wiki.vector import VectorIndex
            vidx = VectorIndex(db_path=vec_db_path)
            if vidx.count() == 0:
                vidx.close()
                return []
            results = vidx.search(query, limit=limit)
            vidx.close()
            return results
        except Exception:
            return []

    def find_similar_titles(self, title: str, threshold: float = 0.6) -> list[dict]:
        cur = self.conn.cursor()
        cur.execute("SELECT id, title, confidence FROM articles_meta")

        title_tokens = set(title.lower().split())
        if not title_tokens:
            return []

        similar = []
        for row in cur.fetchall():
            row_tokens = set(dict(row)["title"].lower().split())
            if not row_tokens:
                continue
            overlap = len(title_tokens & row_tokens) / max(len(title_tokens), len(row_tokens))
            if overlap >= threshold:
                similar.append({
                    "id": dict(row)["id"],
                    "title": dict(row)["title"],
                    "confidence": dict(row)["confidence"],
                    "similarity": round(overlap, 2),
                })

        return sorted(similar, key=lambda x: x["similarity"], reverse=True)

    def get_stale(self, days: int = 90, category: str | None = None) -> list[dict]:
        cutoff = datetime.now(timezone.utc) - timedelta(days=days)
        cutoff_str = cutoff.strftime("%Y-%m-%dT%H:%M:%SZ")

        cur = self.conn.cursor()
        sql = """
            SELECT id, title, category, confidence, last_verified
            FROM articles_meta
            WHERE last_verified < ?
        """
        params: list = [cutoff_str]

        if category:
            sql += " AND category = ?"
            params.append(category)

        sql += " ORDER BY last_verified ASC"
        cur.execute(sql, params)

        now = datetime.now(timezone.utc)
        results = []
        for row in cur.fetchall():
            row_dict = dict(row)
            verified = Article._parse_dt(row_dict["last_verified"])
            days_since = (now - verified).days
            results.append({
                "id": row_dict["id"],
                "title": row_dict["title"],
                "category": row_dict["category"],
                "confidence": row_dict["confidence"],
                "last_verified": row_dict["last_verified"],
                "days_since_verified": days_since,
            })

        return results

    # ── #14: SQL 기반 lint 쿼리 ────────────────────

    def get_orphans(self) -> list[dict]:
        """관계가 전혀 없는 문서."""
        cur = self.conn.cursor()
        cur.execute("""
            SELECT m.id, m.title FROM articles_meta m
            LEFT JOIN article_relations r ON m.id = r.from_id OR m.id = r.to_id
            WHERE r.from_id IS NULL
        """)
        return [dict(row) for row in cur.fetchall()]

    def get_broken_refs(self) -> list[dict]:
        """존재하지 않는 문서를 참조하는 관계."""
        cur = self.conn.cursor()
        cur.execute("""
            SELECT r.from_id, r.to_id FROM article_relations r
            LEFT JOIN articles_meta m ON r.to_id = m.id
            WHERE m.id IS NULL
        """)
        return [{"from": row["from_id"], "to": row["to_id"]} for row in cur.fetchall()]

    def get_one_way_links(self) -> list[dict]:
        """단방향 링크 (A->B 있지만 B->A 없음)."""
        cur = self.conn.cursor()
        cur.execute("""
            SELECT r1.from_id, r1.to_id FROM article_relations r1
            LEFT JOIN article_relations r2
                ON r1.to_id = r2.from_id AND r1.from_id = r2.to_id
            WHERE r2.from_id IS NULL
        """)
        return [{"from": row["from_id"], "to": row["to_id"]} for row in cur.fetchall()]

    def get_no_sources(self) -> list[dict]:
        """출처가 없는 문서."""
        cur = self.conn.cursor()
        cur.execute("""
            SELECT m.id, m.title FROM articles_meta m
            LEFT JOIN article_sources s ON m.id = s.article_id
            WHERE s.article_id IS NULL
        """)
        return [dict(row) for row in cur.fetchall()]

    def get_low_confidence(self, threshold: float = 0.5) -> list[dict]:
        """낮은 신뢰도 문서."""
        cur = self.conn.cursor()
        cur.execute(
            "SELECT id, title, confidence FROM articles_meta WHERE confidence < ?",
            (threshold,),
        )
        return [dict(row) for row in cur.fetchall()]

    # ── #8: 자동 교차 참조 후보 탐색 ───────────────

    def find_related_candidates(self, article: Article, limit: int = 10) -> list[dict]:
        """카테고리, 태그, FTS 기반으로 관련 문서 후보 탐색."""
        seen: dict[str, dict] = {}
        cur = self.conn.cursor()

        # 1. 같은 카테고리
        cur.execute(
            "SELECT id, title FROM articles_meta WHERE category = ? AND id != ?",
            (article.category, article.id),
        )
        for row in cur.fetchall():
            seen[row["id"]] = {"id": row["id"], "title": row["title"],
                               "reason": "same_category", "score": 0.5}

        # 2. 태그 오버랩
        if article.tags:
            cur.execute("SELECT id, title, tags FROM articles_meta WHERE id != ?",
                        (article.id,))
            for row in cur.fetchall():
                row_tags = json.loads(row["tags"] or "[]")
                overlap = set(article.tags) & set(row_tags)
                if overlap:
                    score = len(overlap) / max(len(article.tags), len(row_tags))
                    if row["id"] not in seen or score > seen[row["id"]]["score"]:
                        seen[row["id"]] = {
                            "id": row["id"], "title": row["title"],
                            "reason": f"tag_overlap({','.join(overlap)})", "score": score,
                        }

        # 3. FTS 제목 유사 (_search_fts 내부 메서드 사용)
        try:
            fts_results = self._search_fts(article.title, limit=5)
            for r in fts_results:
                if r["id"] != article.id and r["id"] not in seen:
                    seen[r["id"]] = {
                        "id": r["id"], "title": r["title"],
                        "reason": "title_fts", "score": 0.4,
                    }
        except Exception:
            pass

        results = sorted(seen.values(), key=lambda x: x["score"], reverse=True)
        return results[:limit]

    # ── #10: 모순 탐지 보조 ──────────────────────

    def find_potential_conflicts(self) -> list[dict]:
        """동일 태그 공유 문서들의 content 키 충돌 후보 탐색."""
        cur = self.conn.cursor()
        cur.execute("SELECT id, tags FROM articles_meta")
        articles_tags = [(row["id"], json.loads(row["tags"] or "[]"))
                         for row in cur.fetchall()]

        # 태그 -> 문서 ID 매핑
        tag_to_ids: dict[str, list[str]] = {}
        for aid, tags in articles_tags:
            for tag in tags:
                tag_to_ids.setdefault(tag, []).append(aid)

        # 2개 이상 문서가 공유하는 태그 그룹
        groups: dict[str, set[str]] = {}
        for tag, ids in tag_to_ids.items():
            if len(ids) >= 2:
                key = frozenset(ids)
                groups.setdefault(str(sorted(key)), set()).add(tag)

        import ast
        return [{"shared_tags": list(tags), "article_ids": ast.literal_eval(group_key)}
                for group_key, tags in groups.items()]


    def get_backlinks(self, article_id: str) -> list[dict]:
        """이 문서를 참조하는 모든 문서 목록 반환."""
        cur = self.conn.cursor()
        cur.execute("""
            SELECT r.from_id, m.title, m.category
            FROM article_relations r
            JOIN articles_meta m ON r.from_id = m.id
            WHERE r.to_id = ?
            ORDER BY m.last_modified DESC
        """, (article_id,))
        return [{"id": row["from_id"], "title": row["title"], "category": row["category"]}
                for row in cur.fetchall()]

    def sync_all_backlinks(self) -> dict:
        """전체 문서의 양방향 백링크를 일괄 동기화. DB relations 기반으로 역방향 추가."""
        from ai_wiki.storage import load_article_with_path, atomic_update
        from datetime import datetime, timezone

        cur = self.conn.cursor()
        cur.execute("""
            SELECT r1.from_id, r1.to_id FROM article_relations r1
            LEFT JOIN article_relations r2
                ON r1.to_id = r2.from_id AND r1.from_id = r2.to_id
            WHERE r2.from_id IS NULL
        """)
        one_way = cur.fetchall()

        added = []
        failed = []
        for row in one_way:
            from_id = row["from_id"]
            to_id = row["to_id"]
            target, target_path = load_article_with_path(to_id)
            if target and target_path and from_id not in target.related:
                target.related.append(from_id)
                target.last_modified = datetime.now(timezone.utc)
                try:
                    atomic_update(target, target_path, self)
                    added.append({"from": to_id, "to": from_id})
                except Exception as e:
                    failed.append({"from": to_id, "to": from_id, "error": str(e)})
            self.conn.execute(
                "INSERT OR IGNORE INTO article_relations (from_id, to_id) VALUES (?, ?)",
                (to_id, from_id)
            )
        self.conn.commit()
        return {"added": added, "failed": failed, "total": len(added)}

    def rebuild(self, articles: list[tuple[Article, str]]) -> None:
        cur = self.conn.cursor()
        cur.execute("DELETE FROM articles_meta")
        cur.execute("DELETE FROM articles_fts")
        cur.execute("DELETE FROM article_relations")
        cur.execute("DELETE FROM article_sources")
        self.conn.commit()

        for article, file_path in articles:
            self.upsert(article, file_path)

    # ── 작업C: 일괄 제목 조회 ─────────────────────────

    def get_titles_by_ids(self, ids: list[str]) -> dict[str, str]:
        """여러 문서 ID의 제목을 한 번의 쿼리로 조회."""
        if not ids:
            return {}
        cur = self.conn.cursor()
        placeholders = ",".join("?" for _ in ids)
        cur.execute(
            f"SELECT id, title FROM articles_meta WHERE id IN ({placeholders})",
            ids,
        )
        return {row["id"]: row["title"] for row in cur.fetchall()}

    # ── 작업H: 홈/목록 DB 전환 ─────────────────────────

    def get_category_stats(self) -> list[dict]:
        """카테고리별 문서 수 집계 (최상위 카테고리 기준)."""
        cur = self.conn.cursor()
        cur.execute("""
            SELECT
                CASE WHEN INSTR(category, '/') > 0
                     THEN SUBSTR(category, 1, INSTR(category, '/') - 1)
                     ELSE category
                END AS top_category,
                COUNT(*) AS count
            FROM articles_meta
            GROUP BY top_category
            ORDER BY count DESC
        """)
        return [{"category": row["top_category"], "count": row["count"]}
                for row in cur.fetchall()]

    def get_recent_articles(self, limit: int = 10) -> list[dict]:
        """최근 수정된 문서 목록."""
        cur = self.conn.cursor()
        cur.execute(
            "SELECT id, title, category, last_modified FROM articles_meta "
            "ORDER BY last_modified DESC LIMIT ?",
            (limit,),
        )
        return [dict(row) for row in cur.fetchall()]

    def get_all_articles_meta(
        self, sort: str = "modified", category: str | None = None,
        limit: int | None = None, offset: int = 0,
    ) -> list[dict]:
        """모든 문서 메타데이터를 DB에서 조회 (파일 로드 없음).

        Args:
            sort: 정렬 기준 (title/category/confidence/modified)
            category: 카테고리 필터 (e.g. 'technology/python')
            limit: 반환할 최대 문서 수 (None = 전체)
            offset: 페이지네이션 오프셋
        """
        cur = self.conn.cursor()
        sql = """
            SELECT id, title, category, tags, confidence, version,
                   last_modified, author, content_type,
                   maturity, quality_score
            FROM articles_meta
        """
        params: list = []
        if category:
            sql += " WHERE (category = ? OR category LIKE ? || '/%')"
            params.extend([category, category])

        order_map = {
            "title": "title COLLATE NOCASE ASC",
            "category": "category ASC",
            "confidence": "confidence DESC",
        }
        sql += f" ORDER BY {order_map.get(sort, 'last_modified DESC')}"

        if limit is not None:
            sql += " LIMIT ?"
            params.append(limit)
            if offset:
                sql += " OFFSET ?"
                params.append(offset)
        elif offset:
            # OFFSET without LIMIT requires a large LIMIT in SQLite
            sql += " LIMIT -1 OFFSET ?"
            params.append(offset)

        cur.execute(sql, params)

        results = []
        for row in cur.fetchall():
            d = dict(row)
            d["tags"] = json.loads(d.get("tags") or "[]")
            results.append(d)
        return results

    def count(self) -> int:
        cur = self.conn.cursor()
        cur.execute("SELECT COUNT(*) FROM articles_meta")
        return cur.fetchone()[0]

    def get_all_tags(self) -> list[dict]:
        """전체 태그 목록과 각 태그별 문서 수 반환."""
        cur = self.conn.cursor()
        cur.execute("SELECT tags FROM articles_meta")
        tag_counts: dict[str, int] = {}
        for row in cur.fetchall():
            import json as _json
            tags = _json.loads(row["tags"] or "[]")
            for tag in tags:
                tag_counts[tag] = tag_counts.get(tag, 0) + 1
        result = [{"tag": tag, "count": count}
                  for tag, count in sorted(tag_counts.items(), key=lambda x: (-x[1], x[0]))]
        return result

    def get_articles_by_tag(self, tag: str) -> list[dict]:
        """특정 태그를 가진 문서 목록 반환."""
        cur = self.conn.cursor()
        cur.execute("""
            SELECT id, title, category, tags, confidence, last_modified
            FROM articles_meta
            ORDER BY last_modified DESC
        """)
        results = []
        import json as _json
        for row in cur.fetchall():
            tags = _json.loads(row["tags"] or "[]")
            if tag in tags:
                results.append({
                    "id": row["id"],
                    "title": row["title"],
                    "category": row["category"],
                    "tags": tags,
                    "confidence": row["confidence"],
                    "last_modified": row["last_modified"],
                })
        return results

    def log_access(self, event_type: str, article_id: str = "", query: str = "", result_count: int = 0) -> None:
        """접근 로그 기록."""
        from datetime import datetime, timezone
        timestamp = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
        try:
            self.conn.execute(
                """INSERT INTO access_log (event_type, article_id, query, result_count, timestamp)
                   VALUES (?, ?, ?, ?, ?)""",
                (event_type, article_id or "", query or "", result_count, timestamp)
            )
            self.conn.commit()
        except Exception:
            pass

    def get_access_stats(self, limit: int = 10) -> dict:
        """접근 통계 반환 - Top 조회 문서, Top 검색어."""
        cur = self.conn.cursor()

        # Top 조회 문서
        cur.execute("""
            SELECT al.article_id, m.title, COUNT(*) as view_count
            FROM access_log al
            LEFT JOIN articles_meta m ON al.article_id = m.id
            WHERE al.event_type = 'get' AND al.article_id != ''
            GROUP BY al.article_id
            ORDER BY view_count DESC
            LIMIT ?
        """, (limit,))
        top_articles = [{"id": row["article_id"], "title": row["title"] or row["article_id"],
                         "view_count": row["view_count"]} for row in cur.fetchall()]

        # Top 검색어
        cur.execute("""
            SELECT query, COUNT(*) as search_count, AVG(result_count) as avg_results
            FROM access_log
            WHERE event_type = 'search' AND query != ''
            GROUP BY query
            ORDER BY search_count DESC
            LIMIT ?
        """, (limit,))
        top_queries = [{"query": row["query"], "search_count": row["search_count"],
                        "avg_results": round(row["avg_results"] or 0, 1)} for row in cur.fetchall()]

        # 전체 이벤트 수
        cur.execute("SELECT COUNT(*) as total FROM access_log")
        total_events = cur.fetchone()["total"]

        # 이벤트 타입별 집계
        cur.execute("""
            SELECT event_type, COUNT(*) as count
            FROM access_log
            GROUP BY event_type
            ORDER BY count DESC
        """)
        event_summary = {row["event_type"]: row["count"] for row in cur.fetchall()}

        return {
            "total_events": total_events,
            "event_summary": event_summary,
            "top_articles": top_articles,
            "top_queries": top_queries,
        }


    def set_human_verified(self, article_id: str, verified_by: str, verified_at: str) -> None:
        """문서를 인간 검증 완료로 표시합니다."""
        with self.conn:
            self.conn.execute(
                """UPDATE articles_meta
                   SET human_verified = 1, human_verified_by = ?, human_verified_at = ?
                   WHERE id = ?""",
                (verified_by, verified_at, article_id),
            )

    def get_human_verified_status(self, article_id: str) -> dict:
        """문서의 인간 검증 상태를 반환합니다."""
        cur = self.conn.cursor()
        cur.execute(
            """SELECT human_verified, human_verified_by, human_verified_at
               FROM articles_meta WHERE id = ?""",
            (article_id,),
        )
        row = cur.fetchone()
        if row is None:
            return {"human_verified": False, "verified_by": None, "verified_at": None}
        return {
            "human_verified": bool(row["human_verified"]),
            "verified_by": row["human_verified_by"],
            "verified_at": row["human_verified_at"],
        }

    def close(self) -> None:
        self.conn.close()
