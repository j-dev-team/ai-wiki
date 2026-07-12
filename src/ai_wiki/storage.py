from __future__ import annotations

import logging
import hashlib
import json
import os
import shutil
import sqlite3
import subprocess
import tempfile
from pathlib import Path
from typing import Callable

import yaml

from ai_wiki.models import Article
from ai_wiki.yaml_loader import load_yaml_file as strict_load_yaml_file

logger = logging.getLogger(__name__)


def get_wiki_root() -> Path:
    """위키 루트 디렉토리 반환. AI_WIKI_ROOT 환경변수 우선."""
    env = os.environ.get("AI_WIKI_ROOT")
    if env:
        return Path(env)
    p = Path(__file__).resolve()
    for parent in p.parents:
        if (parent / "pyproject.toml").exists():
            return parent
    return Path.cwd()


def get_articles_dir() -> Path:
    return get_wiki_root() / "articles"


def get_data_dir() -> Path:
    d = get_wiki_root() / "data"
    d.mkdir(parents=True, exist_ok=True)
    return d


# ── #4: O(1) SQLite 경로 조회 ─────────────────────

def _lookup_file_path(article_id: str) -> Path | None:
    """SQLite 인덱스에서 file_path를 O(1) 조회."""
    db_path = get_data_dir() / "wiki.db"
    if not db_path.exists():
        return None
    try:
        conn = sqlite3.connect(str(db_path))
        cur = conn.cursor()
        cur.execute("SELECT file_path FROM articles_meta WHERE id = ?", (article_id,))
        row = cur.fetchone()
        conn.close()
        if row and row[0]:
            return get_wiki_root() / row[0]
    except sqlite3.Error as e:
        logger.warning("SQLite 조회 오류 (article_id=%s): %s", article_id, e)
    return None


def _load_yaml_file(yaml_file: Path) -> dict | None:
    """YAML 파일 로드. #3: 예외를 구분하여 로깅."""
    try:
        data = strict_load_yaml_file(yaml_file)
        if data is not None and not isinstance(data, dict):
            raise ValueError("document root must be a mapping")
        return data
    except (yaml.YAMLError, ValueError) as e:
        logger.warning("YAML 파싱 오류: %s: %s", yaml_file, e)
    except (OSError, UnicodeDecodeError) as e:
        logger.error("파일 읽기 오류: %s: %s", yaml_file, e)
    except Exception as e:
        logger.error("예상치 못한 오류: %s: %s", yaml_file, e)
    return None


# ── CRUD ──────────────────────────────────────────

def save_article(article: Article) -> Path:
    """Validate and atomically save an Article as canonical schema v2 YAML."""
    # category path traversal 방지
    if ".." in article.category:
        raise ValueError(f"category에 '..'을 포함할 수 없습니다: {article.category}")
    if Path(article.category).is_absolute():
        raise ValueError(f"category에 절대 경로를 사용할 수 없습니다: {article.category}")
    articles_dir = get_articles_dir()
    resolved = (articles_dir / article.category).resolve()
    if not str(resolved).startswith(str(articles_dir.resolve())):
        raise ValueError(f"category가 articles 디렉토리 바깥을 참조합니다: {article.category}")

    slug_part = article.id.split("-", 1)[1] if "-" in article.id else article.id
    category_dir = articles_dir / article.category
    category_dir.mkdir(parents=True, exist_ok=True)

    file_path = category_dir / f"{slug_part}.yaml"

    data = article.to_yaml_dict()
    serialized = yaml.safe_dump(
        data, allow_unicode=True, default_flow_style=False, sort_keys=False,
    )
    _atomic_write_bytes(file_path, serialized.encode("utf-8"))

    return file_path


def _atomic_write_bytes(path: Path, data: bytes) -> None:
    """Durably replace one file using a temporary file in the same directory."""
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, temp_name = tempfile.mkstemp(prefix=f".{path.name}.", suffix=".tmp", dir=path.parent)
    temp_path = Path(temp_name)
    try:
        with os.fdopen(fd, "wb") as handle:
            handle.write(data)
            handle.flush()
            os.fsync(handle.fileno())
        # Verify the exact bytes are readable before replacing the destination.
        if temp_path.read_bytes() != data:
            raise OSError(f"temporary file verification failed: {temp_path}")
        os.replace(temp_path, path)
        if os.name != "nt":
            directory_fd = os.open(path.parent, os.O_RDONLY)
            try:
                os.fsync(directory_fd)
            finally:
                os.close(directory_fd)
    finally:
        if temp_path.exists():
            temp_path.unlink()


def load_article(article_id: str) -> Article | None:
    """ID로 문서 로드. #4: SQLite O(1) 우선, 폴백으로 전체 순회."""
    # 1차: SQLite에서 O(1) 경로 조회
    cached_path = _lookup_file_path(article_id)
    if cached_path and cached_path.exists():
        data = _load_yaml_file(cached_path)
        if data and data.get("id") == article_id:
            return Article.from_yaml(data)

    # 2차: 폴백 전체 순회
    articles_dir = get_articles_dir()
    if not articles_dir.exists():
        return None
    for yaml_file in articles_dir.rglob("*.yaml"):
        data = _load_yaml_file(yaml_file)
        if data and data.get("id") == article_id:
            return Article.from_yaml(data)
    return None


def load_article_with_path(article_id: str) -> tuple[Article | None, Path | None]:
    """ID로 문서 + 파일경로 반환. #4: SQLite O(1) 우선."""
    cached_path = _lookup_file_path(article_id)
    if cached_path and cached_path.exists():
        data = _load_yaml_file(cached_path)
        if data and data.get("id") == article_id:
            return Article.from_yaml(data), cached_path

    articles_dir = get_articles_dir()
    if not articles_dir.exists():
        return None, None
    for yaml_file in articles_dir.rglob("*.yaml"):
        data = _load_yaml_file(yaml_file)
        if data and data.get("id") == article_id:
            return Article.from_yaml(data), yaml_file
    return None, None


def delete_article_file(article_id: str) -> bool:
    """파일 삭제. 성공 여부 반환."""
    _, path = load_article_with_path(article_id)
    if path and path.exists():
        path.unlink()
        parent = path.parent
        if parent != get_articles_dir() and not any(parent.iterdir()):
            parent.rmdir()
        return True
    return False


def list_all_articles() -> list[Article]:
    """모든 문서를 로드. DB 인덱스의 file_path로 직접 접근 (rglob 회피)."""
    wiki_root = get_wiki_root()
    db_path = get_data_dir() / "wiki.db"

    # DB에서 file_path 목록을 가져와 직접 로드 (O(n) 파일 I/O, rglob 순회 없음)
    if db_path.exists():
        try:
            conn = sqlite3.connect(str(db_path))
            cur = conn.cursor()
            cur.execute("SELECT file_path FROM articles_meta")
            rows = cur.fetchall()
            conn.close()

            result = []
            for (rel_path,) in rows:
                full_path = wiki_root / rel_path
                if full_path.exists():
                    data = _load_yaml_file(full_path)
                    if data and "id" in data:
                        result.append(Article.from_yaml(data))
            return result
        except sqlite3.Error as e:
            logger.warning("SQLite 전체 조회 오류, 폴백으로 전환: %s", e)

    # 폴백: DB 없으면 기존 방식
    articles_dir = get_articles_dir()
    if not articles_dir.exists():
        return []
    result = []
    for yaml_file in articles_dir.rglob("*.yaml"):
        data = _load_yaml_file(yaml_file)
        if data and "id" in data:
            result.append(Article.from_yaml(data))
    return result


def get_relative_path(absolute_path: Path) -> str:
    """위키 루트 기준 상대 경로."""
    try:
        return str(absolute_path.relative_to(get_wiki_root()))
    except ValueError:
        return str(absolute_path)


# ── #15: 원자적 저장 ─────────────────────────────

# ── #7: Raw Sources 계층 ─────────────────────────

def get_sources_dir() -> Path:
    d = get_wiki_root() / "sources"
    d.mkdir(parents=True, exist_ok=True)
    return d


def save_source_file(article_id: str, source_path: Path, description: str = "") -> Path:
    """원본 파일을 sources/{article_id}/에 복사. 메타데이터 기록."""
    from datetime import datetime, timezone
    dest_dir = get_sources_dir() / article_id
    dest_dir.mkdir(parents=True, exist_ok=True)
    dest = dest_dir / source_path.name
    shutil.copy2(source_path, dest)

    meta_path = dest_dir / "metadata.yaml"
    meta: dict = {"files": []}
    if meta_path.exists():
        try:
            data = yaml.safe_load(meta_path.read_text(encoding="utf-8"))
            if isinstance(data, dict):
                meta = data
        except Exception as e:
            logger.warning("소스 메타데이터 로드 실패 (%s): %s", meta_path, e)

    meta["files"].append({
        "filename": source_path.name,
        "original_path": str(source_path),
        "added_at": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
        "description": description,
        "size_bytes": dest.stat().st_size,
    })
    with open(meta_path, "w", encoding="utf-8") as f:
        yaml.dump(meta, f, allow_unicode=True, default_flow_style=False, sort_keys=False)
    return dest


def list_source_files(article_id: str) -> list[dict]:
    """문서의 원본 파일 목록 반환."""
    meta_path = get_sources_dir() / article_id / "metadata.yaml"
    if not meta_path.exists():
        return []
    try:
        data = yaml.safe_load(meta_path.read_text(encoding="utf-8"))
        return data.get("files", []) if isinstance(data, dict) else []
    except Exception as e:
        logger.warning("소스 파일 목록 로드 실패 (%s): %s", meta_path, e)
        return []


def delete_source_files(article_id: str) -> bool:
    """문서의 원본 파일 디렉토리 전체 삭제."""
    source_dir = get_sources_dir() / article_id
    if source_dir.exists():
        shutil.rmtree(source_dir)
        return True
    return False


# ── #15: 원자적 저장 ─────────────────────────────

def _atomic_save_yaml_index(article: Article, index) -> Path:
    """파일 저장 + DB upsert를 원자적으로 수행. 실패 시 롤백."""
    file_path = _article_file_path(article)
    previous = file_path.read_bytes() if file_path.exists() else None
    pending = _mark_index_pending(article, file_path)
    file_path = save_article(article)
    rel_path = get_relative_path(file_path)
    try:
        index.upsert(article, rel_path)
    except Exception:
        if hasattr(index, "conn"):
            index.conn.rollback()
        if previous is None:
            file_path.unlink(missing_ok=True)
        else:
            _atomic_write_bytes(file_path, previous)
        pending.unlink(missing_ok=True)
        raise
    pending.unlink(missing_ok=True)
    return file_path


def git_auto_commit(action: str, article_id: str = "", title: str = "") -> bool:
    """위키 변경사항을 자동 커밋. git repo가 아니면 무시."""
    wiki_root = get_wiki_root()
    if not (wiki_root / ".git").exists():
        return False
    try:
        subprocess.run(
            ["git", "add", "articles/", "data/index.yaml"],
            cwd=str(wiki_root), capture_output=True, timeout=10,
        )
        msg = f"wiki: {action}"
        if title:
            msg += f" '{title}'"
        if article_id:
            msg += f" ({article_id})"
        subprocess.run(
            ["git", "commit", "-m", msg, "--allow-empty-message"],
            cwd=str(wiki_root), capture_output=True, timeout=10,
        )
        return True
    except Exception as e:
        logger.warning("git 자동 커밋 실패 (action=%s): %s", action, e)
        return False


def _atomic_update_yaml_index(article: Article, old_path: Path | None, index) -> Path:
    """Atomically replace YAML, then reconcile the derived SQLite index."""
    file_path = _article_file_path(article)
    target_backup = file_path.read_bytes() if file_path.exists() else None
    old_backup = old_path.read_bytes() if old_path and old_path.exists() else None
    pending = _mark_index_pending(article, file_path)
    file_path = save_article(article)
    rel_path = get_relative_path(file_path)
    try:
        index.upsert(article, rel_path)
    except Exception:
        if hasattr(index, "conn"):
            index.conn.rollback()
        if target_backup is None:
            file_path.unlink(missing_ok=True)
        else:
            _atomic_write_bytes(file_path, target_backup)
        if old_path and old_backup is not None and old_path != file_path:
            _atomic_write_bytes(old_path, old_backup)
        pending.unlink(missing_ok=True)
        raise
    if old_path and old_path != file_path:
        old_path.unlink(missing_ok=True)
    pending.unlink(missing_ok=True)
    return file_path


def atomic_save(
    article: Article,
    index,
    *,
    vector_upsert: Callable[[Article], None] | None = None,
    vector_remove: Callable[[str], None] | None = None,
) -> Path:
    """Save YAML and both indexes, restoring the prior state on failure."""
    file_path = _article_file_path(article)
    previous = file_path.read_bytes() if file_path.exists() else None
    previous_article = _article_from_bytes(previous) if previous else None
    vector_pending = _mark_vector_pending(article, file_path) if vector_upsert else None
    try:
        committed_path = _atomic_save_yaml_index(article, index)
    except Exception:
        if vector_pending:
            vector_pending.unlink(missing_ok=True)
        raise
    if not vector_upsert:
        return committed_path
    try:
        vector_upsert(article)
        vector_pending.unlink(missing_ok=True)
        return committed_path
    except Exception as exc:
        rollback_error = _rollback_after_vector_failure(
            article.id, index, committed_path, previous, previous_article,
            vector_upsert, vector_remove,
        )
        if rollback_error:
            raise RuntimeError(f"storage rollback failed: {rollback_error}") from exc
        vector_pending.unlink(missing_ok=True)
        raise


def atomic_update(
    article: Article,
    old_path: Path | None,
    index,
    *,
    vector_upsert: Callable[[Article], None] | None = None,
    vector_remove: Callable[[str], None] | None = None,
) -> Path:
    """Update YAML and both indexes, restoring the prior state on failure."""
    new_path = _article_file_path(article)
    target_backup = new_path.read_bytes() if new_path.exists() else None
    old_backup = old_path.read_bytes() if old_path and old_path.exists() else None
    previous_bytes = old_backup if old_backup is not None else target_backup
    previous_article = _article_from_bytes(previous_bytes) if previous_bytes else None
    previous_path = old_path if old_backup is not None else new_path
    vector_pending = _mark_vector_pending(article, new_path) if vector_upsert else None
    try:
        committed_path = _atomic_update_yaml_index(article, old_path, index)
    except Exception:
        if vector_pending:
            vector_pending.unlink(missing_ok=True)
        raise
    if not vector_upsert:
        return committed_path
    try:
        vector_upsert(article)
        vector_pending.unlink(missing_ok=True)
        return committed_path
    except Exception as exc:
        rollback_error = _rollback_update_after_vector_failure(
            article.id, index, committed_path, previous_path,
            target_backup, old_backup, previous_article,
            vector_upsert, vector_remove,
        )
        if rollback_error:
            raise RuntimeError(f"storage rollback failed: {rollback_error}") from exc
        vector_pending.unlink(missing_ok=True)
        raise


def _article_from_bytes(data: bytes) -> Article:
    raw = yaml.safe_load(data.decode("utf-8"))
    if not isinstance(raw, dict):
        raise ValueError("stored document root must be a mapping")
    return Article.from_yaml(raw)


def _restore_index_and_vector(
    article_id: str,
    index,
    previous_article: Article | None,
    previous_path: Path,
    vector_upsert: Callable[[Article], None],
    vector_remove: Callable[[str], None] | None,
) -> Exception | None:
    try:
        if previous_article is None:
            index.remove(article_id)
            if vector_remove:
                vector_remove(article_id)
        else:
            index.upsert(previous_article, get_relative_path(previous_path))
            vector_upsert(previous_article)
        return None
    except Exception as exc:
        return exc


def _rollback_after_vector_failure(
    article_id: str,
    index,
    file_path: Path,
    previous: bytes | None,
    previous_article: Article | None,
    vector_upsert: Callable[[Article], None],
    vector_remove: Callable[[str], None] | None,
) -> Exception | None:
    try:
        if previous is None:
            file_path.unlink(missing_ok=True)
        else:
            _atomic_write_bytes(file_path, previous)
    except Exception as exc:
        return exc
    return _restore_index_and_vector(
        article_id, index, previous_article, file_path, vector_upsert, vector_remove,
    )


def _rollback_update_after_vector_failure(
    article_id: str,
    index,
    committed_path: Path,
    previous_path: Path,
    target_backup: bytes | None,
    old_backup: bytes | None,
    previous_article: Article | None,
    vector_upsert: Callable[[Article], None],
    vector_remove: Callable[[str], None] | None,
) -> Exception | None:
    try:
        if target_backup is None:
            committed_path.unlink(missing_ok=True)
        else:
            _atomic_write_bytes(committed_path, target_backup)
        if old_backup is not None and previous_path != committed_path:
            _atomic_write_bytes(previous_path, old_backup)
    except Exception as exc:
        return exc
    return _restore_index_and_vector(
        article_id, index, previous_article, previous_path, vector_upsert, vector_remove,
    )


def _mark_vector_pending(article: Article, file_path: Path) -> Path:
    """Persist a recovery marker until the vector index matches the YAML document."""
    pending_dir = get_data_dir() / "pending-vector"
    pending_dir.mkdir(parents=True, exist_ok=True)
    digest = hashlib.sha256(article.id.encode("utf-8")).hexdigest()
    marker = pending_dir / f"{digest}.json"
    payload = json.dumps({
        "article_id": article.id,
        "file_path": get_relative_path(file_path),
    }, ensure_ascii=False).encode("utf-8")
    _atomic_write_bytes(marker, payload)
    return marker


def _article_file_path(article: Article) -> Path:
    """Resolve an article path without touching the filesystem."""
    articles_dir = get_articles_dir()
    if ".." in article.category or Path(article.category).is_absolute():
        raise ValueError(f"invalid article category: {article.category}")
    category_dir = (articles_dir / article.category).resolve()
    try:
        category_dir.relative_to(articles_dir.resolve())
    except ValueError as exc:
        raise ValueError(f"category escapes articles directory: {article.category}") from exc
    slug_part = article.id.split("-", 1)[1] if "-" in article.id else article.id
    return category_dir / f"{slug_part}.yaml"


def _mark_index_pending(article: Article, file_path: Path) -> Path:
    """Persist a recovery marker before changing YAML and its derived index."""
    pending_dir = get_data_dir() / "pending-index"
    pending_dir.mkdir(parents=True, exist_ok=True)
    digest = hashlib.sha256(article.id.encode("utf-8")).hexdigest()
    marker = pending_dir / f"{digest}.json"
    payload = json.dumps({
        "article_id": article.id,
        "file_path": get_relative_path(file_path),
    }, ensure_ascii=False).encode("utf-8")
    _atomic_write_bytes(marker, payload)
    return marker
