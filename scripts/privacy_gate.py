"""Fail a release when an archive contains wiki data, local paths, or secrets."""
from __future__ import annotations

import argparse
import io
import os
import re
import tarfile
import zipfile
from pathlib import Path


FORBIDDEN_MEMBERS = (
    re.compile(r"(^|/)(articles|missions|sources)/", re.I),
    re.compile(r"(^|/)data/.*\.(db|sqlite|sqlite3|wal|shm)$", re.I),
    re.compile(r"(^|/)\.ai-wiki(-connectors)?\.yaml$", re.I),
)
CONTENT_PATTERNS = {
    "windows_user_path": re.compile(rb"[A-Za-z]:[\\/]Users[\\/][^\\/\s]+", re.I),
    "unix_user_path": re.compile(rb"/(Users|home)/[^/\s]+", re.I),
    "private_key": re.compile(rb"-----BEGIN (RSA |EC |OPENSSH )?PRIVATE KEY-----"),
    "pypi_token": re.compile(rb"pypi-[A-Za-z0-9_-]{20,}"),
    "api_secret": re.compile(rb"(?i)(api[_-]?key|password|secret)\s*[:=]\s*['\"][^'\"]{8,}"),
    "email": re.compile(rb"[A-Z0-9._%+-]+@[A-Z0-9.-]+\.[A-Z]{2,}", re.I),
}
SAFE_EMAIL_DOMAINS = {b"example.com", b"example.org", b"example.net", b"test.com"}
PATH_PLACEHOLDERS = {b"you", b"yourname", b"username", b"user", b"name"}


def members(path: Path):
    if path.suffix == ".whl" or zipfile.is_zipfile(path):
        with zipfile.ZipFile(path) as archive:
            for item in archive.infolist():
                if not item.is_dir():
                    yield item.filename, archive.read(item)
        return
    with tarfile.open(path, "r:*") as archive:
        for item in archive.getmembers():
            if item.isfile():
                stream = archive.extractfile(item)
                yield item.name, stream.read() if stream else b""


def inspect(path: Path) -> list[dict[str, str]]:
    findings = []
    extra_terms = [item.encode() for item in os.environ.get("AI_WIKI_PRIVATE_TERMS", "").split(";") if item]
    for name, data in members(path):
        normalized = name.replace("\\", "/")
        if any(pattern.search(normalized) for pattern in FORBIDDEN_MEMBERS):
            findings.append({"archive": str(path), "member": name, "kind": "wiki_data"})
        if len(data) > 8_000_000:
            continue
        for kind, pattern in CONTENT_PATTERNS.items():
            for match in pattern.finditer(data):
                if kind == "email" and match.group().rsplit(b"@", 1)[-1].lower() in SAFE_EMAIL_DOMAINS:
                    continue
                if kind == "windows_user_path":
                    user = re.split(rb"[\\/]", match.group())[2].lower()
                    if user in PATH_PLACEHOLDERS or user.startswith((b"<", b"%")):
                        continue
                if kind == "api_secret" and Path(normalized).suffix in {".py", ".pyi"}:
                    continue
                findings.append({"archive": str(path), "member": name, "kind": kind})
                break
        for term in extra_terms:
            if term.lower() in data.lower():
                findings.append({"archive": str(path), "member": name, "kind": "private_term"})
    return findings


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("archives", nargs="+", type=Path)
    args = parser.parse_args()
    findings = [finding for archive in args.archives for finding in inspect(archive)]
    if findings:
        for finding in findings:
            print(f"{finding['kind']}: {finding['archive']}!{finding['member']}")
        return 1
    print(f"privacy gate passed for {len(args.archives)} archive(s)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
