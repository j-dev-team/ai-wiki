from __future__ import annotations

import json
import os
import shutil
import subprocess
import sys
import tempfile
import zipfile
from datetime import datetime, timezone
from pathlib import Path, PurePosixPath
from typing import Any

import yaml

from ai_wiki.variant import VariantSpec, _find_installed_command, create_variant_package, install_variant_package


_EXCLUDED_DIRS = {".git", ".venv", "__pycache__", ".pytest_cache", "build", "dist"}


def load_variant_spec(package_dir: Path) -> VariantSpec:
    manifest = package_dir.resolve() / "variant.yaml"
    if not manifest.exists():
        raise FileNotFoundError(f"variant.yaml not found: {manifest}")
    return VariantSpec.from_manifest(manifest)


def backup_variant(package_dir: Path, output: Path | None = None) -> dict[str, Any]:
    package_dir = package_dir.resolve()
    spec = load_variant_spec(package_dir)
    timestamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    if output is None:
        output = package_dir.parent / f"{package_dir.name}-backups" / f"{spec.package_name}-{timestamp}.zip"
    output = output.resolve()
    output.parent.mkdir(parents=True, exist_ok=True)

    files = [path for path in package_dir.rglob("*") if path.is_file() and _include_backup_file(path, package_dir)]
    metadata = {
        "format": 1,
        "created_at": datetime.now(timezone.utc).isoformat(),
        "package_dir_name": package_dir.name,
        "spec": spec.as_dict(),
        "file_count": len(files),
    }
    with zipfile.ZipFile(output, "w", compression=zipfile.ZIP_DEFLATED, compresslevel=6) as archive:
        archive.writestr(".ai-wiki-backup.json", json.dumps(metadata, ensure_ascii=False, indent=2))
        for path in files:
            archive.write(path, path.relative_to(package_dir).as_posix())
    return {"status": "ok", "action": "variant_backed_up", "archive": str(output), **metadata}


def restore_variant(archive_path: Path, package_dir: Path, *, replace: bool = True) -> dict[str, Any]:
    archive_path = archive_path.resolve()
    package_dir = package_dir.resolve()
    with zipfile.ZipFile(archive_path, "r") as archive:
        members = archive.infolist()
        _validate_archive_members(members)
        metadata = json.loads(archive.read(".ai-wiki-backup.json").decode("utf-8"))
        if replace and package_dir.exists():
            _clear_restore_target(package_dir)
        package_dir.mkdir(parents=True, exist_ok=True)
        for member in members:
            if member.filename == ".ai-wiki-backup.json" or member.is_dir():
                continue
            destination = package_dir / PurePosixPath(member.filename)
            destination.parent.mkdir(parents=True, exist_ok=True)
            with archive.open(member) as source, destination.open("wb") as target:
                shutil.copyfileobj(source, target)
    return {
        "status": "ok",
        "action": "variant_restored",
        "archive": str(archive_path),
        "package_dir": str(package_dir),
        "file_count": metadata.get("file_count", 0),
        "package_name": metadata.get("spec", {}).get("package_name"),
    }


def refresh_variant(package_dir: Path, *, action: str = "variant_upgraded", python_executable: str | None = None) -> dict[str, Any]:
    package_dir = package_dir.resolve()
    spec = load_variant_spec(package_dir)
    backup = backup_variant(package_dir)
    legacy = (package_dir / "src" / spec.module_name / "storage.py").exists()
    try:
        with tempfile.TemporaryDirectory(prefix="ai-wiki-refresh-") as temp_dir:
            staged = Path(create_variant_package(spec, output_dir=Path(temp_dir))["package_dir"])
            target_module = package_dir / "src" / spec.module_name
            if target_module.exists():
                shutil.rmtree(target_module)
            legacy_core = package_dir / "src" / "ai_wiki"
            if spec.module_name != "ai_wiki" and legacy_core.exists():
                shutil.rmtree(legacy_core)
            target_module.parent.mkdir(parents=True, exist_ok=True)
            shutil.copytree(staged / "src" / spec.module_name, target_module)
            for name in ("pyproject.toml", "README.md", "variant.yaml"):
                shutil.copy2(staged / name, package_dir / name)
        install = install_variant_package(package_dir, python_executable=python_executable)
        validation = _validate_refreshed_variant(spec, package_dir)
    except Exception:
        restore_variant(Path(backup["archive"]), package_dir)
        install_variant_package(package_dir, python_executable=python_executable)
        raise
    return {
        "status": "ok",
        "action": action,
        "package_dir": str(package_dir),
        "backup": backup["archive"],
        "legacy_layout_migrated": legacy,
        "install": install,
        "validation": validation,
    }


def uninstall_variant(
    package_dir: Path,
    *,
    purge: bool = False,
    create_backup: bool = True,
    python_executable: str | None = None,
) -> dict[str, Any]:
    package_dir = package_dir.resolve()
    spec = load_variant_spec(package_dir)
    backup = backup_variant(package_dir) if create_backup else None
    python_executable = python_executable or sys.executable
    command = [python_executable, "-m", "pip", "uninstall", "-y", spec.package_name]
    completed = subprocess.run(command, text=True, capture_output=True, check=False)
    if completed.returncode != 0:
        raise RuntimeError((completed.stderr or completed.stdout or "pip uninstall failed").strip())

    removed_skills: list[str] = []
    config_path = package_dir / spec.config_filename
    agents: list[str] = []
    if config_path.exists():
        config = yaml.safe_load(config_path.read_text(encoding="utf-8")) or {}
        agents = config.get("agents") or []
    bases = {
        "claude": [Path.home() / ".claude" / "skills"],
        "gemini": [
            Path.home() / ".gemini" / "config" / "skills",
            Path.home() / ".agents" / "skills",
            Path.home() / ".gemini" / "skills",
            Path.home() / ".gemini" / "antigravity-cli" / "skills",
        ],
        "codex": [Path.home() / ".codex" / "skills"],
    }
    for agent in agents:
        for base in bases.get(agent, []):
            skill_dir = base / package_dir.name
            if skill_dir.exists():
                shutil.rmtree(skill_dir)
                removed_skills.append(str(skill_dir))
    if purge:
        shutil.rmtree(package_dir)
    return {
        "status": "ok",
        "action": "variant_uninstalled",
        "package_name": spec.package_name,
        "package_dir": str(package_dir),
        "purged": purge,
        "data_preserved": not purge,
        "backup": backup["archive"] if backup else None,
        "removed_skills": removed_skills,
    }


def audit_variant_isolation(package_dirs: list[Path]) -> dict[str, Any]:
    rows: list[dict[str, Any]] = []
    errors: list[str] = []
    seen: dict[str, dict[Any, str]] = {
        "root": {},
        "env_prefix": {},
        "config_filename": {},
        "command_name": {},
        "web_port": {},
        "wiki_db": {},
        "vectors_db": {},
    }
    for raw_dir in package_dirs:
        package_dir = raw_dir.resolve()
        spec = load_variant_spec(package_dir)
        values = {
            "root": str(package_dir).lower(),
            "env_prefix": spec.env_prefix,
            "config_filename": spec.config_filename,
            "command_name": spec.command_name,
            "web_port": spec.web_port,
            "wiki_db": str((package_dir / "data" / "wiki.db").resolve()).lower(),
            "vectors_db": str((package_dir / "data" / "vectors.db").resolve()).lower(),
        }
        for key, value in values.items():
            prior = seen[key].get(value)
            if prior:
                errors.append(f"duplicate {key}: {prior} and {spec.package_name} ({value})")
            else:
                seen[key][value] = spec.package_name
        config_exists = (package_dir / spec.config_filename).exists()
        wiki_db_exists = (package_dir / "data" / "wiki.db").exists()
        vectors_db_exists = (package_dir / "data" / "vectors.db").exists()
        if not config_exists:
            errors.append(f"missing config: {spec.package_name}/{spec.config_filename}")
        if not wiki_db_exists:
            errors.append(f"missing wiki DB: {spec.package_name}")
        if not vectors_db_exists:
            errors.append(f"missing vector DB: {spec.package_name}")
        dynamic_root = None
        executable = _find_installed_command(spec.command_name)
        if executable:
            env = os.environ.copy()
            env.pop("AI_WIKI_ROOT", None)
            env.pop(f"{spec.env_prefix}_ROOT", None)
            completed = subprocess.run([executable, "doctor"], cwd=str(package_dir.parent), env=env, text=True, capture_output=True, check=False)
            if completed.returncode == 0 and "{" in completed.stdout:
                dynamic_root = json.loads(completed.stdout[completed.stdout.find("{"):]).get("wiki_root")
            if not dynamic_root or Path(dynamic_root).resolve() != package_dir:
                errors.append(f"runtime root mismatch: {spec.package_name} expected={package_dir} actual={dynamic_root}")
        else:
            errors.append(f"command unavailable for runtime audit: {spec.command_name}")
        rows.append({"package_name": spec.package_name, **values, "config_exists": config_exists, "wiki_db_exists": wiki_db_exists, "vectors_db_exists": vectors_db_exists, "dynamic_root": dynamic_root})
    return {"status": "ok" if not errors else "error", "isolated": not errors, "count": len(rows), "variants": rows, "errors": errors}


def _validate_refreshed_variant(spec: VariantSpec, package_dir: Path) -> dict[str, Any]:
    executable = _find_installed_command(spec.command_name)
    if not executable:
        raise RuntimeError(f"command not found after refresh: {spec.command_name}")
    env = os.environ.copy()
    env[f"{spec.env_prefix}_ROOT"] = str(package_dir)
    completed = subprocess.run([executable, "doctor"], cwd=str(package_dir), env=env, text=True, capture_output=True, check=False)
    if completed.returncode != 0:
        raise RuntimeError((completed.stderr or completed.stdout).strip())
    start = completed.stdout.find("{")
    result = json.loads(completed.stdout[start:])
    if not result.get("vector", {}).get("ready"):
        raise RuntimeError(f"vector validation failed after refresh: {result.get('vector')}")
    return result


def _include_backup_file(path: Path, root: Path) -> bool:
    relative = path.relative_to(root)
    if any(part in _EXCLUDED_DIRS or part.endswith(".egg-info") for part in relative.parts):
        return False
    return path.suffix not in {".pyc", ".pyo"}


def _validate_archive_members(members: list[zipfile.ZipInfo]) -> None:
    names = {member.filename for member in members}
    if ".ai-wiki-backup.json" not in names:
        raise ValueError("not an AI Wiki backup archive")
    for member in members:
        path = PurePosixPath(member.filename)
        if path.is_absolute() or ".." in path.parts:
            raise ValueError(f"unsafe backup member: {member.filename}")


def _clear_restore_target(package_dir: Path) -> None:
    for child in package_dir.iterdir():
        if child.name in {".git", ".venv"}:
            continue
        if child.is_dir():
            shutil.rmtree(child)
        else:
            child.unlink()
