from __future__ import annotations

import logging
import os
import shutil
import sqlite3
import subprocess
from pathlib import Path

import yaml

from ai_wiki.models import Article

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
        with open(yaml_file, "r", encoding="utf-8") as f:
            return yaml.safe_load(f)
    except yaml.YAMLError as e:
        logger.warning("YAML 파싱 오류: %s: %s", yaml_file, e)
    except (OSError, UnicodeDecodeError) as e:
        logger.error("파일 읽기 오류: %s: %s", yaml_file, e)
    except Exception as e:
        logger.error("예상치 못한 오류: %s: %s", yaml_file, e)
    return None


# ── CRUD ──────────────────────────────────────────

def save_article(article: Article) -> Path:
    """Article을 YAML 파일로 저장."""
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
    with open(file_path, "w", encoding="utf-8") as f:
        yaml.dump(data, f, allow_unicode=True, default_flow_style=False, sort_keys=False)

    return file_path


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

def atomic_save(article: Article, index) -> Path:
    """파일 저장 + DB upsert를 원자적으로 수행. 실패 시 롤백."""
    file_path = save_article(article)
    rel_path = get_relative_path(file_path)
    try:
        index.upsert(article, rel_path)
    except Exception:
        if file_path.exists():
            file_path.unlink()
        raise
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


def atomic_update(article: Article, old_path: Path | None, index) -> Path:
    """기존 파일 백업 후 새 파일+DB 저장. 실패 시 원복."""
    backup_data = None
    if old_path and old_path.exists():
        backup_data = old_path.read_bytes()
        old_path.unlink()

    file_path = save_article(article)
    rel_path = get_relative_path(file_path)
    try:
        index.upsert(article, rel_path)
    except Exception:
        if file_path.exists():
            file_path.unlink()
        if backup_data and old_path:
            old_path.write_bytes(backup_data)
        raise
    return file_path
