from __future__ import annotations

import hashlib
import json
import re
import sys
import uuid

import click


def generate_id(title: str, category: str) -> str:
    """카테고리 prefix + slug + 6자 hex ID 생성."""
    # #2 fix: 한글 카테고리 지원
    first_segment = category.split("/")[0].lower()
    ascii_prefix = re.sub(r"[^a-z0-9]", "", first_segment)[:4]
    if not ascii_prefix:
        ascii_prefix = hashlib.md5(first_segment.encode("utf-8")).hexdigest()[:4]
    slug = slugify(title, max_words=5)
    short = uuid.uuid4().hex[:6]
    return f"{ascii_prefix}-{slug}-{short}" if slug else f"{ascii_prefix}-{short}"


def slugify(text: str, max_words: int = 5) -> str:
    """영문/숫자만 추출하여 하이픈 연결 slug 생성."""
    text = text.lower()
    words = re.findall(r"[a-z0-9]+", text)
    return "-".join(words[:max_words])


def output_json(data: dict) -> None:
    """JSON stdout 출력 (Windows cp949 회피, 항상 UTF-8)."""
    text = json.dumps(data, ensure_ascii=False, indent=2) + "\n"
    if sys.platform == "win32":
        sys.stdout.buffer.write(text.encode("utf-8"))
    else:
        click.echo(text, nl=False)


def output_error(message: str, code: str = "error") -> None:
    """에러 JSON 출력 후 exit(1)."""
    output_json({"status": "error", "code": code, "message": message})
    sys.exit(1)
