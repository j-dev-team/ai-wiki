from __future__ import annotations

import json
import os
import re
import shutil
import subprocess
import sys
import zlib
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import yaml


_PACKAGE_RE = re.compile(r"^[a-z0-9][a-z0-9-]*[a-z0-9]$")
_MODULE_RE = re.compile(r"^[A-Za-z_][A-Za-z0-9_]*$")
_COMMAND_RE = re.compile(r"^[a-z0-9][a-z0-9-]*[a-z0-9]$")


@dataclass(frozen=True)
class VariantSpec:
    package_name: str
    module_name: str
    display_name: str
    domain: str
    command_name: str
    skill_name: str
    env_prefix: str
    config_filename: str
    web_port: int
    description: str
    triggers: tuple[str, ...] = field(default_factory=tuple)

    @classmethod
    def build(
        cls,
        *,
        package_name: str,
        module_name: str | None = None,
        display_name: str | None = None,
        domain: str | None = None,
        command_name: str | None = None,
        skill_name: str | None = None,
        env_prefix: str | None = None,
        config_filename: str | None = None,
        web_port: int | None = None,
        description: str | None = None,
        triggers: list[str] | tuple[str, ...] | None = None,
    ) -> "VariantSpec":
        package_name = package_name.strip()
        module_name = (module_name or package_name.replace("-", "_")).strip()
        command_name = (command_name or package_name).strip()
        skill_name = (skill_name or command_name).strip()
        display_name = (display_name or package_name).strip()
        domain = (domain or package_name.replace("-wiki", "")).strip()
        env_prefix = (env_prefix or module_name.upper()).strip()
        config_filename = (config_filename or f".{package_name}.yaml").strip()
        web_port = web_port or 5100 + (zlib.crc32(package_name.encode("utf-8")) % 1000)
        description = (
            description
            or f"{display_name} knowledge wiki. Stores and searches structured YAML documents through the {command_name} CLI."
        ).strip()
        normalized_triggers = tuple(t.strip() for t in (triggers or ()) if t.strip())

        spec = cls(
            package_name=package_name,
            module_name=module_name,
            display_name=display_name,
            domain=domain,
            command_name=command_name,
            skill_name=skill_name,
            env_prefix=env_prefix,
            config_filename=config_filename,
            web_port=web_port,
            description=description,
            triggers=normalized_triggers,
        )
        spec.validate()
        return spec

    @classmethod
    def from_mapping(
        cls,
        raw: dict[str, Any],
        *,
        package_name: str | None = None,
        module_name: str | None = None,
        display_name: str | None = None,
        domain: str | None = None,
        command_name: str | None = None,
        skill_name: str | None = None,
        env_prefix: str | None = None,
        config_filename: str | None = None,
        web_port: int | None = None,
        description: str | None = None,
        triggers: tuple[str, ...] = (),
    ) -> "VariantSpec":
        if not isinstance(raw, dict):
            raise ValueError("variant manifest must be a YAML mapping")

        merged: dict[str, Any] = dict(raw)
        overrides = {
            "package_name": package_name,
            "module_name": module_name,
            "display_name": display_name,
            "domain": domain,
            "command_name": command_name,
            "skill_name": skill_name,
            "env_prefix": env_prefix,
            "config_filename": config_filename,
            "web_port": web_port,
            "description": description,
        }
        for key, value in overrides.items():
            if value:
                merged[key] = value
        if triggers:
            merged["triggers"] = list(triggers)

        if not merged.get("package_name"):
            raise ValueError("package_name is required in the manifest, preset, or CLI argument")

        return cls.build(
            package_name=merged["package_name"],
            module_name=merged.get("module_name"),
            display_name=merged.get("display_name"),
            domain=merged.get("domain"),
            command_name=merged.get("command_name"),
            skill_name=merged.get("skill_name"),
            env_prefix=merged.get("env_prefix"),
            config_filename=merged.get("config_filename"),
            web_port=merged.get("web_port"),
            description=merged.get("description"),
            triggers=merged.get("triggers") or (),
        )

    @classmethod
    def from_manifest(
        cls,
        manifest_path: Path,
        *,
        package_name: str | None = None,
        module_name: str | None = None,
        display_name: str | None = None,
        domain: str | None = None,
        command_name: str | None = None,
        description: str | None = None,
        triggers: tuple[str, ...] = (),
    ) -> "VariantSpec":
        raw = yaml.safe_load(manifest_path.read_text(encoding="utf-8")) or {}
        return cls.from_mapping(
            raw,
            package_name=package_name,
            module_name=module_name,
            display_name=display_name,
            domain=domain,
            command_name=command_name,
            description=description,
            triggers=triggers,
        )

    def validate(self) -> None:
        if not _PACKAGE_RE.match(self.package_name):
            raise ValueError("package_name must use lowercase letters, numbers, and hyphens")
        if not _MODULE_RE.match(self.module_name):
            raise ValueError("module_name must be a valid Python package identifier")
        if not _COMMAND_RE.match(self.command_name):
            raise ValueError("command_name must use lowercase letters, numbers, and hyphens")
        if not self.config_filename.startswith(".") or not self.config_filename.endswith(".yaml"):
            raise ValueError("config_filename must look like .name.yaml")
        if not self.env_prefix.replace("_", "").isalnum() or self.env_prefix.upper() != self.env_prefix:
            raise ValueError("env_prefix must be uppercase letters, numbers, and underscores")
        if not 1 <= self.web_port <= 65535:
            raise ValueError("web_port must be between 1 and 65535")

    def as_dict(self) -> dict[str, Any]:
        return {
            "package_name": self.package_name,
            "module_name": self.module_name,
            "display_name": self.display_name,
            "domain": self.domain,
            "command_name": self.command_name,
            "skill_name": self.skill_name,
            "env_prefix": self.env_prefix,
            "config_filename": self.config_filename,
            "web_port": self.web_port,
            "description": self.description,
            "triggers": list(self.triggers),
        }


def create_variant_package(
    spec: VariantSpec,
    *,
    output_dir: Path,
    source_root: Path | None = None,
    force: bool = False,
) -> dict[str, Any]:
    source_root = source_root or Path(__file__).resolve().parents[2]
    package_dir = output_dir.resolve() / spec.package_name
    if package_dir.exists():
        if not force:
            raise FileExistsError(f"target package already exists: {package_dir}")
        if (package_dir / "data").exists() or (package_dir / "articles").exists():
            raise FileExistsError(f"refusing to overwrite initialized wiki package: {package_dir}")
        shutil.rmtree(package_dir)

    (package_dir / "src").mkdir(parents=True)
    target_module_dir = package_dir / "src" / spec.module_name
    _write_variant_module(target_module_dir, spec)

    _write_pyproject(package_dir / "pyproject.toml", spec)
    _write_readme(package_dir / "README.md", spec)
    _write_gitignore(package_dir / ".gitignore")
    _write_variant_manifest(package_dir / "variant.yaml", spec)
    _write_smoke_test(package_dir / "tests" / "test_import.py", spec)

    license_src = source_root / "LICENSE"
    if license_src.exists():
        shutil.copy2(license_src, package_dir / "LICENSE")

    return {
        "status": "ok",
        "action": "variant_created",
        "package_dir": str(package_dir),
        "package_name": spec.package_name,
        "module_name": spec.module_name,
        "command_name": spec.command_name,
        "web_command_name": f"{spec.command_name}-web",
        "skill_name": spec.skill_name,
        "config_file": spec.config_filename,
        "env_root": f"{spec.env_prefix}_ROOT",
        "next_steps": [
            f"cd {package_dir}",
            "pip install -e .",
            f"{spec.command_name} init ./my-wiki",
            f"{spec.command_name} doctor",
        ],
    }


def list_builtin_presets() -> list[dict[str, Any]]:
    preset_dir = _builtin_preset_dir()
    presets: list[dict[str, Any]] = []
    for resource in sorted(preset_dir.iterdir(), key=lambda item: item.name):
        if not resource.name.endswith(".yaml"):
            continue
        raw = _load_yaml_resource(resource)
        if not isinstance(raw, dict):
            continue
        presets.append(
            {
                "name": resource.name.removesuffix(".yaml"),
                "package_name": raw.get("package_name"),
                "display_name": raw.get("display_name"),
                "domain": raw.get("domain"),
                "summary": raw.get("summary") or raw.get("description"),
                "triggers": raw.get("triggers") or [],
            }
        )
    return presets


def load_builtin_preset(name: str) -> dict[str, Any]:
    name = name.strip().lower()
    if not re.match(r"^[a-z0-9][a-z0-9-]*$", name):
        raise ValueError("preset name must use lowercase letters, numbers, and hyphens")
    resource = _builtin_preset_dir() / f"{name}.yaml"
    if not resource.is_file():
        available = ", ".join(p["name"] for p in list_builtin_presets())
        raise ValueError(f"unknown preset '{name}'. Available presets: {available}")
    raw = _load_yaml_resource(resource)
    if not isinstance(raw, dict):
        raise ValueError(f"preset '{name}' must be a YAML mapping")
    return raw


def write_manifest_file(path: Path, spec: VariantSpec, *, force: bool = False) -> dict[str, Any]:
    path = path.resolve()
    if path.exists() and not force:
        raise FileExistsError(f"manifest already exists: {path}")
    path.parent.mkdir(parents=True, exist_ok=True)
    _write_variant_manifest(path, spec)
    return {
        "status": "ok",
        "action": "manifest_written",
        "manifest": str(path),
        "package_name": spec.package_name,
        "preset": spec.domain,
    }


def _builtin_preset_dir():
    import importlib.resources as resources

    return resources.files("ai_wiki") / "variant_presets"


def _load_yaml_resource(resource) -> dict[str, Any] | None:
    return yaml.safe_load(resource.read_text(encoding="utf-8")) or {}


def install_variant_package(package_dir: Path, *, python_executable: str | None = None) -> dict[str, Any]:
    package_dir = package_dir.resolve()
    if not (package_dir / "pyproject.toml").exists():
        raise FileNotFoundError(f"pyproject.toml not found in generated package: {package_dir}")

    python_executable = python_executable or sys.executable
    command = [python_executable, "-m", "pip", "install", "-e", str(package_dir)]
    completed = subprocess.run(
        command,
        cwd=str(package_dir),
        text=True,
        capture_output=True,
        check=False,
    )
    if completed.returncode != 0:
        details = (completed.stderr or completed.stdout or "").strip()
        if len(details) > 4000:
            details = details[-4000:]
        raise RuntimeError(f"pip install -e failed with exit code {completed.returncode}: {details}")

    return {
        "installed": True,
        "command": command,
        "returncode": completed.returncode,
        "stdout": completed.stdout[-4000:] if completed.stdout else "",
        "stderr": completed.stderr[-4000:] if completed.stderr else "",
    }


def provision_variant_package(
    spec: VariantSpec,
    *,
    output_dir: Path,
    agents: tuple[str, ...] = ("codex",),
    lang: str = "ko",
    python_executable: str | None = None,
) -> dict[str, Any]:
    invalid_agents = sorted(set(agents) - {"claude", "gemini", "codex"})
    if invalid_agents:
        raise ValueError(f"unsupported agents: {', '.join(invalid_agents)}")
    if lang not in {"ko", "en"}:
        raise ValueError("lang must be 'ko' or 'en'")

    result = create_variant_package(spec, output_dir=output_dir)
    package_dir = Path(result["package_dir"])
    result["install"] = install_variant_package(package_dir, python_executable=python_executable)

    executable = _find_installed_command(spec.command_name, python_executable)
    if not executable:
        raise RuntimeError(f"installed command not found on PATH: {spec.command_name}")

    env = os.environ.copy()
    env[f"{spec.env_prefix}_ROOT"] = str(package_dir)
    env["AI_WIKI_INIT_AUTOMATED"] = "1"
    env["AI_WIKI_INIT_LANG"] = lang
    env["AI_WIKI_INIT_NAME"] = spec.display_name
    env["AI_WIKI_INIT_AGENTS"] = ",".join(agents)

    result["initialize"] = _run_variant_command([executable, "init", str(package_dir)], package_dir, env)
    installed_agents = result["initialize"]["result"].get("agents", [])
    if installed_agents != list(agents):
        raise RuntimeError(f"skill agent mismatch: requested={list(agents)} installed={installed_agents}")
    result["vector_index"] = _run_variant_command([executable, "vindex"], package_dir, env)
    result["doctor"] = _run_variant_command([executable, "doctor"], package_dir, env)
    vector_status = result["doctor"]["result"].get("vector", {})
    if not vector_status.get("ready"):
        raise RuntimeError(f"vector search is not ready after install: {vector_status}")
    result["action"] = "variant_installed"
    result["agents"] = list(agents)
    result["lang"] = lang
    return result


def _run_variant_command(command: list[str], cwd: Path, env: dict[str, str]) -> dict[str, Any]:
    completed = subprocess.run(command, cwd=str(cwd), env=env, text=True, capture_output=True, check=False)
    if completed.returncode != 0:
        details = (completed.stderr or completed.stdout or "").strip()
        raise RuntimeError(f"command failed ({' '.join(command)}): {details[-4000:]}")
    output = completed.stdout.strip()
    start = output.find("{")
    parsed = json.loads(output[start:]) if start >= 0 else {"output": output}
    return {"returncode": completed.returncode, "result": parsed}


def _find_installed_command(command_name: str, python_executable: str | None = None) -> str | None:
    executable = shutil.which(command_name)
    if executable:
        return executable
    python_path = Path(python_executable or sys.executable).resolve()
    suffix = ".exe" if os.name == "nt" else ""
    candidate = python_path.parent / f"{command_name}{suffix}"
    return str(candidate) if candidate.exists() else None


def _write_variant_module(target_dir: Path, spec: VariantSpec) -> None:
    target_dir.mkdir(parents=True, exist_ok=True)
    runtime_spec = repr(spec.as_dict())
    (target_dir / "__init__.py").write_text('__version__ = "0.5.0"\n', encoding="utf-8")
    (target_dir / "cli.py").write_text(
        "from pathlib import Path\n\n"
        "from ai_wiki.runtime import activate_variant\n\n"
        f"SPEC = {runtime_spec}\n"
        "activate_variant(\n"
        "    SPEC,\n"
        "    skill_template_dir=Path(__file__).parent / \"skill_templates\",\n"
        "    default_root=Path(__file__).resolve().parents[2],\n"
        ")\n\n"
        "from ai_wiki.cli import cli\n\n"
        '__all__ = ["cli"]\n',
        encoding="utf-8",
    )
    (target_dir / "web.py").write_text(
        "from pathlib import Path\n\n"
        "from ai_wiki.runtime import activate_variant\n\n"
        f"SPEC = {runtime_spec}\n"
        "activate_variant(SPEC, default_root=Path(__file__).resolve().parents[2])\n\n"
        "from ai_wiki.web import app, main\n\n"
        '__all__ = ["app", "main"]\n',
        encoding="utf-8",
    )
    skill_dir = target_dir / "skill_templates"
    skill_dir.mkdir(parents=True, exist_ok=True)
    (skill_dir / "SKILL.md").write_text(_render_skill(spec), encoding="utf-8")
    _write_variant_manifest(target_dir / "variant.yaml", spec)


def _write_pyproject(path: Path, spec: VariantSpec) -> None:
    text = f"""[build-system]
requires = ["setuptools>=68.0", "wheel"]
build-backend = "setuptools.build_meta"

[project]
name = "{spec.package_name}"
version = "0.5.0"
description = "{_toml_string(spec.description)}"
readme = "README.md"
license = "MIT"
requires-python = ">=3.11"
dependencies = ["ai-wiki>=0.5,<0.6"]

[project.scripts]
{spec.command_name} = "{spec.module_name}.cli:cli"
{spec.command_name}-web = "{spec.module_name}.web:main"

[project.optional-dependencies]
test = ["pytest>=7.0"]

[tool.pytest.ini_options]
testpaths = ["tests"]

[tool.setuptools.packages.find]
where = ["src"]
include = ["{spec.module_name}*"]

[tool.setuptools.package-data]
{spec.module_name} = ["skill_templates/*.md", "variant.yaml"]
"""
    path.write_text(text, encoding="utf-8")


def _toml_string(value: str) -> str:
    return value.replace("\\", "\\\\").replace('"', '\\"')


def _write_readme(path: Path, spec: VariantSpec) -> None:
    triggers = ", ".join(spec.triggers) if spec.triggers else spec.domain
    text = f"""# {spec.display_name}

This package is an AI Wiki variant generated from `ai-wiki`.

- Package: `{spec.package_name}`
- Python module: `{spec.module_name}`
- CLI: `{spec.command_name}`
- Web CLI: `{spec.command_name}-web`
- Config file: `{spec.config_filename}`
- Root environment variable: `{spec.env_prefix}_ROOT`
- Domain: `{spec.domain}`
- Triggers: {triggers}

## Install

```bash
pip install -e .
```

## Initialize

```bash
{spec.command_name} init ./my-wiki
{spec.command_name} doctor
{spec.command_name} vindex
```
"""
    path.write_text(text, encoding="utf-8")


def _write_gitignore(path: Path) -> None:
    path.write_text(
        ".venv/\n__pycache__/\n*.pyc\n*.egg-info/\nbuild/\ndist/\n.pytest_cache/\n",
        encoding="utf-8",
    )


def _write_variant_manifest(path: Path, spec: VariantSpec) -> None:
    path.write_text(yaml.safe_dump(spec.as_dict(), allow_unicode=True, sort_keys=False), encoding="utf-8")


def _write_smoke_test(path: Path, spec: VariantSpec) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        f"""def test_imports():
    import {spec.module_name}
    from {spec.module_name}.cli import cli

    assert {spec.module_name}.__version__
    assert cli.name == "cli"
""",
        encoding="utf-8",
    )


def _render_skill(spec: VariantSpec) -> str:
    trigger_text = ", ".join(spec.triggers) if spec.triggers else spec.domain
    routing_text = (
        "Use this dedicated wiki when its manifest triggers match the request. "
        "Keep private records in their originating wiki and use ai-wiki only when no dedicated wiki applies."
    )
    return f"""---
name: {spec.skill_name}
version: 0.5.0
description: {spec.description} Use this skill whenever the request is about {trigger_text} knowledge, research, records, or retrieval in this dedicated domain. {routing_text}
user-invocable: true
argument-hint: "[capabilities|context|get|record-use|patch|create] [query or options]"
---

# {spec.display_name} Skill

{spec.display_name} is an AI-operated encyclopedia with an isolated YAML source of truth and a stable CLI JSON protocol.

## When To Use

Use this skill when the user asks about knowledge or records in this domain:

- domain: `{spec.domain}`
- triggers: {trigger_text}
- reusable notes, research, explanations, entity records, decisions, and follow-up context for this wiki

## Mandatory Workflow

Before answering, retrieve an evidence-linked context package:

```bash
{spec.command_name} context "question" --max-tokens 4000
```

Answer using returned citation keys, then record actual use:

```bash
{spec.command_name} record-use <context-id> --citation "doc:<id>#<path>" --outcome answered
```

If context is insufficient, record that outcome, research, patch or create reusable knowledge, and run context again. Never delete autonomously.

## Routing Priority

{routing_text}

## Common Commands

```bash
{spec.command_name} capabilities
{spec.command_name} context "question" --max-tokens 4000
{spec.command_name} get <document-id>
{spec.command_name} patch <document-id> --operations-file patch.json --if-version <version> --dry-run
{spec.command_name} create --document-file document.json --dry-run
{spec.command_name} record-use <context-id> --citation "doc:<id>#<path>" --outcome answered
{spec.command_name} doctor
{spec.command_name} quality <document-id>
```

## Storage Rules

When writing knowledge:

- use the JSON create/patch protocol; YAML is the internal source of truth
- include sources for factual claims whenever possible
- mark uncertainty with lower `confidence`, `limitations`, or verification metadata
- prefer categories under `{spec.domain}/...`
- treat `version_conflict` and `duplicate_conflict` as required re-read decisions
"""
