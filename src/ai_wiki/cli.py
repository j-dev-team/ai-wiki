from __future__ import annotations

import logging
import json
import os
import re
import shutil
import subprocess
import sys
from datetime import datetime, timezone
from pathlib import Path

logger = logging.getLogger(__name__)

import click
import yaml
from pydantic import ValidationError

from ai_wiki.runtime import get_runtime
from ai_wiki.migration import migrate_article_files
from ai_wiki.schema_v2 import document_json_schema
from ai_wiki.yaml_loader import load_yaml_text
from ai_wiki.agent_protocol import (
    PROTOCOL_VERSION,
    ProtocolFailure,
    apply_json_patch,
    build_context,
    canonical_document,
    compact_document,
    failure as protocol_failure,
    load_json_input,
    project_fields,
    success as protocol_success,
    utc_now_text,
)

_RUNTIME = get_runtime()
DEFAULT_WIKI_PRESET = _RUNTIME.default_preset
COMMAND_NAME = _RUNTIME.command_name
DISPLAY_NAME = _RUNTIME.display_name
CONFIG_FILENAME = _RUNTIME.config_filename
ROOT_ENV_NAME = _RUNTIME.root_env_name
SKILL_NAME = _RUNTIME.skill_name


def _skill_templates_dir() -> Path:
    return _RUNTIME.skill_template_dir or Path(__file__).parent / "skill_templates"

# ── i18n message table ────────────────────────────────────

MESSAGES: dict[str, dict[str, str]] = {
    "en": {
        "not_found": "Article ID \'{}\' not found",
        "content_input_required": "Specify either --content-file or --content-stdin",
        "vector_search_error": "Vector search error: {}",
        "vector_index_error": "Vector index error: {}",
        "no_wiki_found": "Error: No wiki found at \'{}\' (data/wiki.db not found).",
        "destroy_confirm_prompt": "Are you sure you want to destroy this wiki? This cannot be undone. [y/N]",
        "destroy_aborted": "Aborted.",
        "destroy_done": "Wiki destroyed. Restart your terminal.",
        "destroy_dir_error": "Error: Could not remove wiki directory: {}",
        "skill_update_notice": (
            "[!] Skill update available (installed={}, package={}). "
            f"Run: {COMMAND_NAME} upgrade-skill"
        ),
        "upgrade_skill_installed_version": "Installed version : {}",
        "upgrade_skill_not_installed": "Installed version : (not installed)",
        "upgrade_skill_package_version": "Package version   : {}",
        "upgrade_skill_agents": "Agents            : {}",
        "upgrade_skill_copied": "\nCopied {} file(s) to {} ({}):",
        "upgrade_skill_file_ok": "  [ok] {}",
        "upgrade_skill_done": "\nSkills upgraded to version {}",
        "upgrade_skill_no_templates": "Error: skill_templates directory not found.",
        "upgrade_skill_no_files": "Error: No .md files found in skill_templates.",
        "destroy_wiki_path": "Wiki path: {}",
        "path_not_found_msg": "No path found within max depth {}",
        "path_not_found_hint": "Try increasing --max-depth",
    },
    "ko": {
        "not_found": "문서 ID '{}'를 찾을 수 없습니다",
        "content_input_required": "--content-file 또는 --content-stdin 중 하나를 지정하세요",
        "vector_search_error": "벡터 검색 오류: {}",
        "vector_index_error": "벡터 인덱스 오류: {}",
        "no_wiki_found": "오류: '{}'에서 위키를 찾을 수 없습니다 (data/wiki.db 없음).",
        "destroy_confirm_prompt": "이 위키를 정말로 삭제하시겠습니까? 되돌릴 수 없습니다. [y/N]",
        "destroy_aborted": "취소되었습니다.",
        "destroy_done": "위키가 삭제되었습니다. 터미널을 재시작하세요.",
        "destroy_dir_error": "오류: 위키 디렉토리를 삭제할 수 없습니다: {}",
        "skill_update_notice": (
            "[!] 스킬 업데이트 사용 가능 (설치됨={}, 패키지={}). "
            f"실행: {COMMAND_NAME} upgrade-skill"
        ),
        "upgrade_skill_installed_version": "설치된 버전 : {}",
        "upgrade_skill_not_installed": "설치된 버전 : (미설치)",
        "upgrade_skill_package_version": "패키지 버전  : {}",
        "upgrade_skill_agents": "에이전트     : {}",
        "upgrade_skill_copied": "\n{}개 파일을 {} ({})에 복사했습니다:",
        "upgrade_skill_file_ok": "  [ok] {}",
        "upgrade_skill_done": "\n스킬이 버전 {}로 업그레이드되었습니다",
        "upgrade_skill_no_templates": "오류: skill_templates 디렉토리를 찾을 수 없습니다.",
        "upgrade_skill_no_files": "오류: skill_templates에서 .md 파일을 찾을 수 없습니다.",
        "destroy_wiki_path": "위키 경로: {}",
        "path_not_found_msg": "최대 깊이 {} 내에서 경로를 찾지 못했습니다",
        "path_not_found_hint": "--max-depth를 늘려보세요",
    },
}

def _get_lang() -> str:
    """Read the 'lang' field from .ai-wiki.yaml. Returns 'en' as default."""
    try:
        from ai_wiki.storage import get_wiki_root
        config_path = get_wiki_root() / CONFIG_FILENAME
        if config_path.exists():
            cfg = yaml.safe_load(config_path.read_text(encoding="utf-8"))
            if isinstance(cfg, dict):
                return cfg.get("lang", "en")
    except Exception:
        pass
    return "en"


def msg(key: str, *args) -> str:
    """Return the localised message string for *key*, formatted with *args*."""
    lang = _get_lang()
    text = MESSAGES.get(lang, MESSAGES["en"]).get(key, MESSAGES["en"].get(key, key))
    return text.format(*args) if args else text

from ai_wiki.index import WikiIndex
from ai_wiki.models import Article
from ai_wiki.quality import validate as quality_validate
from ai_wiki.schemas import (
    TYPE_SCHEMAS,
    build_content_template,
    compute_completeness,
    determine_maturity,
    register_custom_types,
)
from ai_wiki.storage import (
    atomic_save,
    atomic_update,
    delete_article_file,
    delete_source_files,
    get_relative_path,
    get_wiki_root,
    git_auto_commit,
    list_all_articles,
    list_source_files,
    load_article,
    load_article_with_path,
    save_article,
    save_source_file,
)
from ai_wiki.utils import generate_id, output_error, output_json
from ai_wiki.wikilog import append_log, migrate_legacy_log
from ai_wiki.catalog import rebuild_catalog


def _output_protocol_failure(exc: ProtocolFailure) -> None:
    output_json(protocol_failure(
        exc.code, exc.message, details=exc.details, retryable=exc.retryable,
    ))
    raise click.exceptions.Exit(1)


# ── Skill version utilities ─────────────────────────────

def _get_skill_version(skill_file: Path) -> "str | None":
    """Extract a skill version from frontmatter or a legacy comment."""
    try:
        with open(skill_file, "r", encoding="utf-8") as f:
            for _ in range(12):
                line = f.readline()
                if not line:
                    break
                m = re.match(r"(?:#\s*)?version:\s*(\S+)", line.strip())
                if m:
                    return m.group(1)
    except Exception:
        pass
    return None


def _get_package_skill_version() -> "str | None":
    """Return the version of the SKILL.md template bundled with the package."""
    try:
        pkg_skill = _skill_templates_dir() / "SKILL.md"
        return _get_skill_version(pkg_skill)
    except Exception:
        return None


def _get_installed_skill_version() -> "str | None":
    """Return the version from the installed SKILL.md (tries ai-wiki and ai_wiki)."""
    for folder_name in (SKILL_NAME, SKILL_NAME.replace("-", "_")):
        candidate = Path.home() / ".claude" / "skills" / folder_name / "SKILL.md"
        if candidate.exists():
            return _get_skill_version(candidate)
    return None

def _check_skill_update() -> None:
    """Print an update notice to stderr if package skill version differs from installed version."""
    try:
        pkg_ver = _get_package_skill_version()
        inst_ver = _get_installed_skill_version()
        if pkg_ver and inst_ver and pkg_ver != inst_ver:
            print(msg("skill_update_notice", inst_ver, pkg_ver), file=sys.stderr)
    except Exception:
        pass  # ignore version check failure

@click.group()
@click.pass_context
def cli(ctx):
    """AI Knowledge Wiki - Structured knowledge document management CLI"""
    ctx.ensure_object(dict)
    register_custom_types(get_wiki_root() / CONFIG_FILENAME, reset=True)
    # init/destroy commands may run without a wiki, so skip index creation
    if ctx.invoked_subcommand in ("init", "destroy", "upgrade-skill", "quickstart", "template", "create-template", "variant"):
        return
    ctx.obj["index"] = WikiIndex()
    ctx.call_on_close(ctx.obj["index"].close)
    migrate_legacy_log()


# ── Init ─────────────────────────────────────────────────

# ── Agent utilities ────────────────────────────────────────

_AGENT_SKILL_PATHS = {
    "claude": lambda name: Path.home() / ".claude" / "skills" / name,
    "gemini": lambda name: Path.home() / ".gemini" / "config" / "skills" / name,
    "codex": lambda name: Path.home() / ".codex" / "skills" / name,
}

_LEGACY_AGENT_SKILL_PATHS = {
    "gemini": (
        lambda name: Path.home() / ".agents" / "skills" / name,
        lambda name: Path.home() / ".gemini" / "skills" / name,
        lambda name: Path.home() / ".gemini" / "antigravity-cli" / "skills" / name,
    ),
}

_AGENT_LABELS = {
    "1": "claude",
    "2": "gemini",
    "3": "codex",
}


def _prompt_agent_selection() -> "list[str]":
    """Interactive prompt for agent selection. Returns a list of selected agent keys."""
    click.echo("Which AI agents do you use? (comma-separated numbers)")
    click.echo("  1. Claude Code")
    click.echo("  2. Gemini via Antigravity CLI")
    click.echo("  3. GPT Codex")
    raw = click.prompt("Select [1,2,3] (default: 1)", default="1")
    selected: list[str] = []
    for token in raw.split(","):
        token = token.strip()
        agent = _AGENT_LABELS.get(token)
        if agent and agent not in selected:
            selected.append(agent)
    if not selected:
        selected = ["claude"]
    return selected


def _install_skills_for_agents(agents: "list[str]", wiki_name: str, skill_files: list) -> "list[str]":
    """Install skill files to the skill paths for the given agents. Returns a list of installed directories."""
    installed_dirs: list[str] = []
    for agent in agents:
        path_fn = _AGENT_SKILL_PATHS.get(agent)
        if path_fn is None:
            continue
        destinations = [path_fn(wiki_name)]
        if agent == "gemini":
            destinations.append(_LEGACY_AGENT_SKILL_PATHS["gemini"][0](wiki_name))
        for skill_dir in destinations:
            skill_dir.mkdir(parents=True, exist_ok=True)
            for src in skill_files:
                shutil.copy2(src, skill_dir / src.name)
            installed_dirs.append(str(skill_dir))
    return installed_dirs


def _load_agents_from_config(wiki_root: "Path") -> "list[str]":
    """Read the agents list from the wiki's .ai-wiki.yaml. Returns ['claude'] if not found (backward compat)."""
    config_path = wiki_root / CONFIG_FILENAME
    if not config_path.exists():
        return ["claude"]
    try:
        with open(config_path, "r", encoding="utf-8") as f:
            cfg = yaml.safe_load(f)
        agents = cfg.get("agents")
        if isinstance(agents, list) and agents:
            return [a for a in agents if isinstance(a, str)]
    except Exception:
        pass
    return ["claude"]


def _create_seed_document(wiki_root: "Path", lang: str) -> bool:
    """Create the AI Wiki self-reference seed document during init.

    Loads the bundled seed YAML for the given language, generates an ID,
    and saves it via atomic_save. Skips silently if the document already exists
    or if any error occurs.

    Returns True if created, False if skipped or failed.
    """
    import importlib.resources as _res

    seed_file = f"ai-wiki-about.{lang}.yaml"
    try:
        pkg_ref = _res.files("ai_wiki") / "seed_docs" / seed_file
        seed_text = pkg_ref.read_text(encoding="utf-8")
    except Exception:
        return False

    previous_root = os.environ.get("AI_WIKI_ROOT")
    os.environ["AI_WIKI_ROOT"] = str(wiki_root)
    try:
        from ai_wiki import __version__

        now_text = utc_now_text()
        uses_placeholders = "__WIKI_DISPLAY_NAME__" in seed_text
        seed_text = (
            seed_text
            .replace("__WIKI_DISPLAY_NAME__", DISPLAY_NAME)
            .replace("__WIKI_COMMAND_NAME__", COMMAND_NAME)
            .replace("__ENGINE_VERSION__", __version__)
            .replace("__NOW__", now_text)
        )
        if not uses_placeholders and DISPLAY_NAME != "AI Wiki":
            seed_text = seed_text.replace("AI Wiki", DISPLAY_NAME).replace("ai-wiki", COMMAND_NAME)
        data = yaml.safe_load(seed_text)
        if not isinstance(data, dict):
            return False

        title = data.get("title", f"About {DISPLAY_NAME}")
        category = data.get("category", "technology/ai")

        # Check if a document with this title already exists
        from ai_wiki.index import WikiIndex as _WikiIndex
        _idx = _WikiIndex(db_path=wiki_root / "data" / "wiki.db")
        existing = _idx.get_all_articles_meta(sort="title")
        for row in existing:
            if row.get("title") == title:
                _idx.close()
                return False
        _idx.close()

        article_id = generate_id(title, category)
        if data.get("schema_version") == 2:
            data["id"] = article_id
            article = Article.from_yaml(data)
        else:
            article = Article(
                id=article_id,
                title=title,
                category=category,
                content=data.get("content", {}),
                tags=data.get("tags", []),
                confidence=data.get("confidence", 0.95),
                version=data.get("version", 1),
                sources=data.get("sources", []),
                related=[],
                author=data.get("author", COMMAND_NAME),
            )

        idx2 = WikiIndex(db_path=wiki_root / "data" / "wiki.db")
        atomic_save(article, idx2)
        idx2.close()
        return True
    except Exception:
        return False
    finally:
        if previous_root is None:
            os.environ.pop("AI_WIKI_ROOT", None)
        else:
            os.environ["AI_WIKI_ROOT"] = previous_root


@cli.command()
@click.argument("path", default=".", type=click.Path())
@click.pass_context
def init(ctx, path):
    """Initialize a new wiki. Creates directory structure and configuration file."""
    from pathlib import Path as P
    import yaml as _yaml

    wiki_root = P(path).resolve()

    # Check if wiki already initialized
    if (wiki_root / CONFIG_FILENAME).exists():
        output_json({
            "status": "ok",
            "action": "already_initialized",
            "wiki_root": str(wiki_root),
            "message": "Wiki is already initialized.",
        })
        return

    # ── 1. Language selection ────────────────────────────────
    click.echo("Select language / 언어를 선택하세요")
    click.echo("  1. English")
    click.echo("  2. 한국어")
    automated = os.environ.get("AI_WIKI_INIT_AUTOMATED") == "1"
    lang_raw = ("2" if os.environ.get("AI_WIKI_INIT_LANG") == "ko" else "1") if automated else click.prompt("Select [1,2] (default: 1)", default="1").strip()
    lang = "ko" if lang_raw == "2" else "en"

    # ── 2. Wiki name input ────────────────────────────────
    if lang == "ko":
        wiki_name = (os.environ.get("AI_WIKI_INIT_NAME") or wiki_root.name) if automated else click.prompt(
            "위키 이름을 입력하세요 (웹 UI에 표시됩니다)",
            default=wiki_root.name,
        ).strip() or wiki_root.name
    else:
        wiki_name = (os.environ.get("AI_WIKI_INIT_NAME") or wiki_root.name) if automated else click.prompt(
            "Enter wiki name (displayed in the web UI)",
            default=wiki_root.name,
        ).strip() or wiki_root.name

    # ── 3. Agent selection ─────────────────────────────
    if automated:
        raw_agents = os.environ.get("AI_WIKI_INIT_AGENTS", "")
        agents = [agent.strip() for agent in raw_agents.split(",") if agent.strip() in _AGENT_SKILL_PATHS]
    else:
        agents = _prompt_agent_selection()

    # ── 4. Preset selection ──────────────────────────────
    if DEFAULT_WIKI_PRESET:
        preset = DEFAULT_WIKI_PRESET
        label = "적용된 전용 프리셋" if lang == "ko" else "Dedicated preset"
        click.echo(f"{label}: {preset}")
    else:
        if lang == "ko":
            click.echo("프리셋을 선택하세요:")
            click.echo("  1. general  - 범용 지식 (기본값)")
            click.echo("  2. tech     - 기술 / 개발")
            click.echo("  3. business - 비즈니스 / 경영")
            click.echo("  4. research - 연구 / 학술")
            preset_raw = click.prompt("Select [1,2,3,4] (default: 1)", default="1").strip()
        else:
            click.echo("Select a preset:")
            click.echo("  1. general  - General knowledge (default)")
            click.echo("  2. tech     - Technology / development")
            click.echo("  3. business - Business / management")
            click.echo("  4. research - Research / academic")
            preset_raw = click.prompt("Select [1,2,3,4] (default: 1)", default="1").strip()

        _preset_map = {"1": "general", "2": "tech", "3": "business", "4": "research"}
        preset = _preset_map.get(preset_raw, "general")

    # Create directories
    dirs = ["articles", "data", "logs", "sources"]
    for d in dirs:
        (wiki_root / d).mkdir(parents=True, exist_ok=True)

    # Create config file
    config = {
        "version": "1.0",
        "name": wiki_name,
        "lang": lang,
        "preset": preset,
        "agents": agents,
        "created_at": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
        "settings": {
            "min_content_keys": 5,
            "min_content_chars": 200,
            "min_sources": 1,
            "default_confidence": 0.8,
        },
    }
    config_path = wiki_root / CONFIG_FILENAME
    with open(config_path, "w", encoding="utf-8") as f:
        _yaml.dump(config, f, allow_unicode=True, default_flow_style=False, sort_keys=False)

    # Create .gitignore
    gitignore_path = wiki_root / ".gitignore"
    if not gitignore_path.exists():
        gitignore_path.write_text(
            "data/*.db\ndata/*.db-wal\ndata/*.db-shm\n__pycache__/\n*.pyc\n.venv/\n",
            encoding="utf-8",
        )

    # Initialize DB
    idx = WikiIndex(db_path=wiki_root / "data" / "wiki.db")
    idx.close()

    # Copy skill files — install only to selected agent paths
    _skill_templates = _skill_templates_dir()
    _skill_files = list(_skill_templates.glob("*.md")) if _skill_templates.exists() else []

    installed_skill_dirs: list[str] = []
    if _skill_files:
        installed_skill_dirs = _install_skills_for_agents(agents, wiki_root.name, _skill_files)

    # Generate seed document for this wiki
    _seed_generated = _create_seed_document(wiki_root, lang)

    output_json({
        "status": "ok",
        "action": "initialized",
        "wiki_root": str(wiki_root),
        "directories": dirs,
        "config_file": CONFIG_FILENAME,
        "agents": agents,
        "skill_dirs": installed_skill_dirs,
        "next_steps": [
            f"Set {ROOT_ENV_NAME} to {wiki_root}",
            f"Run: {COMMAND_NAME} doctor",
            f"Run: {COMMAND_NAME} vindex",
            f"Run: {COMMAND_NAME} template technology --output content.yaml",
            f"Run: {COMMAND_NAME} create --title \"...\" --category \"{_RUNTIME.domain}/...\" --source \"...\" --content-file content.yaml",
        ],
        "hint": f"Set the environment variable with: export {ROOT_ENV_NAME}='{wiki_root}'",
    })


@cli.command()
def quickstart():
    """Print the shortest path from install to first searchable document."""
    output_json({
        "status": "ok",
        "steps": [
            {"step": 1, "title": "Initialize a wiki", "command": f"{COMMAND_NAME} init ./my-wiki"},
            {"step": 2, "title": "Point commands at it", "command": f"set {ROOT_ENV_NAME}=./my-wiki"},
            {"step": 3, "title": "Check storage and vector readiness", "command": f"{COMMAND_NAME} doctor"},
            {"step": 4, "title": "Create a starter YAML template", "command": f"{COMMAND_NAME} template technology --output content.yaml"},
            {
                "step": 5,
                "title": "Fill content.yaml, then create a document",
                "command": f"{COMMAND_NAME} create --title \"My Topic\" --category \"{_RUNTIME.domain}/general\" --source \"https://example.com\" --content-file content.yaml",
            },
            {"step": 6, "title": "Build semantic search index", "command": f"{COMMAND_NAME} vindex"},
            {"step": 7, "title": "Search", "command": f"{COMMAND_NAME} search \"my topic\""},
        ],
        "template_types": sorted(TYPE_SCHEMAS.keys()),
    })


@cli.group()
def variant():
    """Create independent purpose-specific wiki packages."""


@variant.command("presets")
def variant_presets():
    """List bundled variant presets."""
    from ai_wiki.variant import list_builtin_presets

    presets = list_builtin_presets()
    output_json({
        "status": "ok",
        "count": len(presets),
        "presets": presets,
    })


@variant.command("show-preset")
@click.argument("name")
def variant_show_preset(name):
    """Show a bundled preset manifest."""
    from ai_wiki.variant import load_builtin_preset

    try:
        manifest = load_builtin_preset(name)
    except Exception as exc:
        output_error(str(exc), code="variant_preset_failed")
        raise click.Abort() from exc
    output_json({
        "status": "ok",
        "preset": name,
        "manifest": manifest,
    })


@variant.command("init-manifest")
@click.argument("package_name", required=False)
@click.option("--preset", default=None, help="Bundled preset to use as a starting point")
@click.option("--output", "-o", default=None, type=click.Path(), help="Manifest output path")
@click.option("--module", "module_name", default=None, help="Python module name, e.g. law_wiki")
@click.option("--display-name", default=None, help="Human-readable wiki name")
@click.option("--domain", default=None, help="Primary domain/category prefix")
@click.option("--command", "command_name", default=None, help="CLI command name")
@click.option("--description", default=None, help="Skill/package description")
@click.option("--trigger", "triggers", multiple=True, help="Skill trigger keyword; repeatable")
@click.option("--force", is_flag=True, default=False, help="Overwrite the manifest file")
def variant_init_manifest(package_name, preset, output, module_name, display_name, domain, command_name, description, triggers, force):
    """Write a custom variant manifest file."""
    from ai_wiki.variant import VariantSpec, load_builtin_preset, write_manifest_file

    try:
        if preset:
            raw = load_builtin_preset(preset)
            spec = VariantSpec.from_mapping(
                raw,
                package_name=package_name,
                module_name=module_name,
                display_name=display_name,
                domain=domain,
                command_name=command_name,
                description=description,
                triggers=tuple(triggers),
            )
        else:
            if not package_name:
                raise ValueError("package_name is required unless --preset is provided")
            spec = VariantSpec.build(
                package_name=package_name,
                module_name=module_name,
                display_name=display_name,
                domain=domain,
                command_name=command_name,
                description=description,
                triggers=tuple(triggers),
            )
        output_path = Path(output) if output else Path(f"{spec.package_name}.variant.yaml")
        result = write_manifest_file(output_path, spec, force=force)
        result["spec"] = spec.as_dict()
    except Exception as exc:
        output_error(str(exc), code="variant_manifest_failed")
        raise click.Abort() from exc
    output_json(result)


@variant.command("create")
@click.argument("package_name", required=False)
@click.option("--manifest", type=click.Path(exists=True), default=None, help="YAML variant manifest to load")
@click.option("--preset", default=None, help="Bundled preset to use instead of --manifest")
@click.option("--output-dir", "-o", default=".", type=click.Path(), help="Parent directory for the new package")
@click.option("--module", "module_name", default=None, help="Python module name, e.g. law_wiki")
@click.option("--display-name", default=None, help="Human-readable wiki name")
@click.option("--domain", default=None, help="Primary domain/category prefix")
@click.option("--command", "command_name", default=None, help="CLI command name")
@click.option("--description", default=None, help="Skill/package description")
@click.option("--trigger", "triggers", multiple=True, help="Skill trigger keyword; repeatable")
@click.option("--force", is_flag=True, default=False, help="Overwrite the target package directory")
@click.option("--install", is_flag=True, default=False, help="Run pip install -e on the generated package")
@click.option("--python", "python_executable", default=None, help="Python executable to use with --install")
def variant_create(package_name, manifest, preset, output_dir, module_name, display_name, domain, command_name, description, triggers, force, install, python_executable):
    """Generate a standalone wiki package and matching skill template."""
    from ai_wiki.variant import VariantSpec, create_variant_package, install_variant_package, load_builtin_preset

    try:
        if manifest and preset:
            raise ValueError("Use either --manifest or --preset, not both")
        if manifest:
            spec = VariantSpec.from_manifest(
                Path(manifest),
                package_name=package_name,
                module_name=module_name,
                display_name=display_name,
                domain=domain,
                command_name=command_name,
                description=description,
                triggers=tuple(triggers),
            )
        elif preset:
            spec = VariantSpec.from_mapping(
                load_builtin_preset(preset),
                package_name=package_name,
                module_name=module_name,
                display_name=display_name,
                domain=domain,
                command_name=command_name,
                description=description,
                triggers=tuple(triggers),
            )
        else:
            if not package_name:
                raise ValueError("package_name is required unless --manifest or --preset is provided")
            spec = VariantSpec.build(
                package_name=package_name,
                module_name=module_name,
                display_name=display_name,
                domain=domain,
                command_name=command_name,
                description=description,
                triggers=tuple(triggers),
            )
        result = create_variant_package(spec, output_dir=Path(output_dir), force=force)
        if install:
            result["install"] = install_variant_package(
                Path(result["package_dir"]),
                python_executable=python_executable,
            )
    except Exception as exc:
        output_error(str(exc), code="variant_create_failed")
        raise click.Abort() from exc

    output_json(result)


@variant.command("install")
@click.argument("package_name", required=False)
@click.option("--manifest", type=click.Path(exists=True), default=None)
@click.option("--preset", default=None, help="Bundled preset to install")
@click.option("--output-dir", "-o", default=".", type=click.Path())
@click.option("--display-name", default=None)
@click.option("--agent", "agents", multiple=True, type=click.Choice(["claude", "gemini", "codex"]), default=("codex",))
@click.option("--lang", type=click.Choice(["ko", "en"]), default="ko")
@click.option("--python", "python_executable", default=None)
def variant_install(package_name, manifest, preset, output_dir, display_name, agents, lang, python_executable):
    """Create, install, initialize, index, and diagnose a dedicated wiki."""
    from ai_wiki.variant import VariantSpec, load_builtin_preset, provision_variant_package

    try:
        if manifest and preset:
            raise ValueError("Use either --manifest or --preset, not both")
        if manifest:
            spec = VariantSpec.from_manifest(Path(manifest), package_name=package_name, display_name=display_name)
        elif preset:
            spec = VariantSpec.from_mapping(load_builtin_preset(preset), package_name=package_name, display_name=display_name)
        elif package_name:
            spec = VariantSpec.build(package_name=package_name, display_name=display_name)
        else:
            raise ValueError("package_name is required unless --manifest or --preset is provided")
        result = provision_variant_package(
            spec,
            output_dir=Path(output_dir),
            agents=tuple(agents),
            lang=lang,
            python_executable=python_executable,
        )
    except Exception as exc:
        output_error(str(exc), code="variant_install_failed")
        raise click.Abort() from exc
    output_json(result)


@variant.command("backup")
@click.argument("package_dir", type=click.Path(exists=True, file_okay=False))
@click.option("--output", "-o", type=click.Path(), default=None)
def variant_backup(package_dir, output):
    """Create a restorable package and data archive."""
    from ai_wiki.lifecycle import backup_variant
    output_json(backup_variant(Path(package_dir), Path(output) if output else None))


@variant.command("restore")
@click.argument("archive", type=click.Path(exists=True, dir_okay=False))
@click.argument("package_dir", type=click.Path())
def variant_restore(archive, package_dir):
    """Replace a package with the contents of a verified backup."""
    from ai_wiki.lifecycle import restore_variant
    output_json(restore_variant(Path(archive), Path(package_dir)))


@variant.command("migrate")
@click.argument("package_dir", type=click.Path(exists=True, file_okay=False))
@click.option("--python", "python_executable", default=None)
def variant_migrate(package_dir, python_executable):
    """Convert a legacy copied package to the shared engine layout."""
    from ai_wiki.lifecycle import refresh_variant
    output_json(refresh_variant(Path(package_dir), action="variant_migrated", python_executable=python_executable))


@variant.command("upgrade")
@click.argument("package_dir", type=click.Path(exists=True, file_okay=False))
@click.option("--python", "python_executable", default=None)
def variant_upgrade(package_dir, python_executable):
    """Back up and refresh a package, rolling back on validation failure."""
    from ai_wiki.lifecycle import refresh_variant
    output_json(refresh_variant(Path(package_dir), action="variant_upgraded", python_executable=python_executable))


@variant.command("uninstall")
@click.argument("package_dir", type=click.Path(exists=True, file_okay=False))
@click.option("--purge", is_flag=True, help="Delete the package root after creating a backup")
@click.option("--yes", is_flag=True, help="Confirm destructive --purge")
@click.option("--no-backup", is_flag=True, help="Skip backup for non-purge uninstall")
@click.option("--python", "python_executable", default=None)
def variant_uninstall(package_dir, purge, yes, no_backup, python_executable):
    """Uninstall commands and skills; preserve data unless --purge is confirmed."""
    from ai_wiki.lifecycle import uninstall_variant
    if purge and not yes:
        raise click.UsageError("--purge requires --yes")
    if purge and no_backup:
        raise click.UsageError("--purge cannot be used with --no-backup")
    output_json(uninstall_variant(Path(package_dir), purge=purge, create_backup=not no_backup, python_executable=python_executable))


@variant.command("audit-isolation")
@click.argument("package_dirs", nargs=-1, required=True, type=click.Path(exists=True, file_okay=False))
def variant_audit_isolation(package_dirs):
    """Verify roots, configs, databases, commands, environment prefixes, and ports are unique."""
    from ai_wiki.lifecycle import audit_variant_isolation
    result = audit_variant_isolation([Path(path) for path in package_dirs])
    output_json(result)
    if not result["isolated"]:
        raise click.ClickException("variant isolation audit failed")


@variant.command("install-skills")
@click.argument("package_dir", type=click.Path(exists=True, file_okay=False))
@click.option("--agent", "agents", multiple=True, required=True, type=click.Choice(["claude", "gemini", "codex"]))
def variant_install_skills(package_dir, agents):
    """Install a variant skill for one or more supported agents."""
    from ai_wiki.skill_routing import install_variant_skills
    output_json(install_variant_skills(Path(package_dir), tuple(agents)))


@variant.command("audit-skills")
@click.argument("package_dirs", nargs=-1, required=True, type=click.Path(exists=True, file_okay=False))
def variant_audit_skills(package_dirs):
    """Audit installed skill files and routing collision test cases."""
    from ai_wiki.skill_routing import audit_skill_installation
    result = audit_skill_installation([Path(path) for path in package_dirs])
    output_json(result)
    if result["status"] != "ok":
        raise click.ClickException("skill audit failed")


@cli.command("template")
@click.argument("type_name", default="technology", required=False)
@click.option("--output", "-o", default=None, type=click.Path(), help="Write YAML to this file instead of stdout")
@click.option("--no-optional", is_flag=True, help="Only include required fields")
@click.option("--list", "list_types", is_flag=True, help="List available content types")
def template_cmd(type_name, output, no_optional, list_types):
    """Generate a starter YAML content template for a document type."""
    if list_types:
        output_json({"status": "ok", "types": sorted(TYPE_SCHEMAS.keys())})
        return

    if type_name not in TYPE_SCHEMAS:
        output_error(
            f"Unknown type '{type_name}'. Run: {COMMAND_NAME} template --list",
            "unknown_type",
        )

    content = build_content_template(type_name, include_optional=not no_optional)
    text = yaml.dump(content, allow_unicode=True, default_flow_style=False, sort_keys=False)

    if output:
        Path(output).write_text(text, encoding="utf-8")
        output_json({
            "status": "ok",
            "action": "template_written",
            "type": type_name,
            "output": output,
        })
    else:
        click.echo(text)


cli.add_command(template_cmd, "create-template")


@cli.command()
@click.argument("query")
@click.option("--category", "-c", default=None, help="Category filter")
@click.option("--tag", "-t", multiple=True, help="Tag filter (multiple allowed)")
@click.option("--limit", "-n", default=20, help="Maximum number of results")
@click.option("--min-confidence", default=0.0, help="Minimum confidence")
@click.pass_context
def search(ctx, query, category, tag, limit, min_confidence):
    """Search documents by keyword."""
    idx: WikiIndex = ctx.obj["index"]
    tags = list(tag) if tag else None
    results = idx.search(query, category=category, tags=tags,
                         limit=limit, min_confidence=min_confidence)
    vector = _vector_status(idx=idx, load_model=False)
    append_log("search", details=f"query='{query}' count={len(results)}")
    idx.log_access("search", query=query, result_count=len(results))
    output_json({
        "status": "ok",
        "count": len(results),
        "results": results,
        "vector": {
            "ready": vector["ready"],
            "indexed": vector["indexed"],
            "vector_count": vector["vector_count"],
            "article_count": vector["article_count"],
            "actions": vector["actions"],
        },
    })

@cli.command('list')
@click.option('--sort', default='modified',
              type=click.Choice(['title', 'category', 'confidence', 'modified']),
              help='Sort key (default: modified)')
@click.option('--category', '-c', default=None, help='Category filter (e.g. technology/python)')
@click.option('--limit', '-n', default=50, help='Maximum number of documents to return (default: 50)')
@click.option('--offset', default=0, help='Pagination offset (default: 0)')
@click.pass_context
def list_articles(ctx, sort, category, limit, offset):
    """List all documents (DB metadata only, no file loading)."""
    idx: WikiIndex = ctx.obj["index"]

    # Total count (without LIMIT/OFFSET)
    total = idx.count()
    if category:
        cur = idx.conn.cursor()
        cur.execute(
            "SELECT COUNT(*) FROM articles_meta WHERE (category = ? OR category LIKE ? || '/%')",
            (category, category),
        )
        total = cur.fetchone()[0]

    articles = idx.get_all_articles_meta(sort=sort, category=category, limit=limit, offset=offset)

    result_list = []
    for a in articles:
        result_list.append({
            "id": a["id"],
            "title": a["title"],
            "category": a["category"],
            "confidence": a["confidence"],
            "version": a["version"],
            "last_modified": a["last_modified"],
            "tags": a["tags"],
        })

    output_json({
        "status": "ok",
        "total": total,
        "showing": len(result_list),
        "offset": offset,
        "articles": result_list,
    })


@cli.command()
@click.argument("article_id")
@click.option("--meta-only", is_flag=True, help="Return metadata only")
@click.option("--view", type=click.Choice(["compact", "full", "raw"]), default="compact",
              show_default=True, help="AI document view")
@click.option("--fields", default=None, help="Comma-separated compact field projections")
@click.option("--legacy", is_flag=True, help="Return the v0.3 response for compatibility")
@click.pass_context
def get(ctx, article_id, meta_only, view, fields, legacy):
    """Retrieve a document by ID."""
    idx: WikiIndex = ctx.obj["index"]
    article, path = load_article_with_path(article_id)
    if not article:
        if legacy:
            output_error(msg("not_found", article_id), "not_found")
        _output_protocol_failure(ProtocolFailure("not_found", msg("not_found", article_id)))

    idx.log_access("get", article_id=article_id)
    if legacy and meta_only:
        output_json({"status": "ok", "article": article.meta_dict()})
        return
    if legacy:
        output_json({"status": "ok", "article": article.to_dict()})
        return
    try:
        if meta_only:
            document = article.meta_dict()
        elif view == "full":
            document = canonical_document(article)
        elif view == "raw":
            document = {
                "id": article.id,
                "version": article.version,
                "raw_yaml": path.read_text(encoding="utf-8") if path else "",
            }
        else:
            document, _ = compact_document(article)
            document = project_fields(document, fields)
        output_json(protocol_success({"document": document}, meta={"view": view}))
    except ProtocolFailure as exc:
        _output_protocol_failure(exc)


@cli.command()
def capabilities():
    """Describe the stable AI protocol and available operations."""
    output_json(protocol_success({
        "protocol_version": PROTOCOL_VERSION,
        "commands": {
            "get": {"views": ["compact", "full", "raw"], "default": "compact"},
            "context": {"default_max_tokens": 4000, "default_limit": 8},
            "record-use": {"outcomes": ["answered", "insufficient"]},
            "patch": {"operations": ["test", "add", "replace", "remove"], "if_version_required": True},
            "create": {"document_file": "JSON path or - for stdin"},
        },
        "content_types": sorted(TYPE_SCHEMAS),
        "schema": document_json_schema(),
    }))


@cli.command("context")
@click.argument("query")
@click.option("--max-tokens", default=4000, type=int, show_default=True)
@click.option("--limit", default=8, type=int, show_default=True)
@click.option("--category", default=None)
@click.option("--tag", "tags", multiple=True)
@click.option("--include-unverified", is_flag=True)
@click.pass_context
def context_cmd(ctx, query, max_tokens, limit, category, tags, include_unverified):
    """Build an evidence-linked context package within a token budget."""
    try:
        envelope = build_context(
            ctx.obj["index"], query, max_tokens=max_tokens, limit=limit,
            category=category, tags=list(tags) or None,
            include_unverified=include_unverified,
        )
        output_json(envelope)
    except ProtocolFailure as exc:
        _output_protocol_failure(exc)


@cli.command("record-use")
@click.argument("context_id")
@click.option("--citation", "citations", multiple=True)
@click.option("--outcome", type=click.Choice(["answered", "insufficient"]), required=True)
@click.pass_context
def record_use(ctx, context_id, citations, outcome):
    """Record which context citations an agent actually used."""
    try:
        result = ctx.obj["index"].record_context_usage(context_id, list(citations), outcome)
        output_json(protocol_success(result))
    except ValueError as exc:
        if str(exc) == "context_not_found":
            _output_protocol_failure(ProtocolFailure("context_not_found", "Context ID was not found"))
        try:
            unknown = json.loads(str(exc))
        except json.JSONDecodeError:
            unknown = []
        _output_protocol_failure(ProtocolFailure(
            "invalid_citation", "Citation was not issued for this context", details={"unknown": unknown},
        ))


@cli.command("patch")
@click.argument("article_id")
@click.option("--operations-file", required=True, help="JSON Patch file or - for stdin")
@click.option("--if-version", required=True, type=int)
@click.option("--dry-run", is_flag=True)
@click.pass_context
def patch_article(ctx, article_id, operations_file, if_version, dry_run):
    """Apply a validated RFC 6902 subset with optimistic concurrency."""
    idx: WikiIndex = ctx.obj["index"]
    article, old_path = load_article_with_path(article_id)
    if not article:
        _output_protocol_failure(ProtocolFailure("not_found", msg("not_found", article_id)))
    if article.version != if_version:
        _output_protocol_failure(ProtocolFailure(
            "version_conflict", "Document version changed",
            details={"expected": if_version, "current": article.version}, retryable=True,
        ))
    try:
        operations = load_json_input(operations_file)
        before_document = canonical_document(article)
        patched, changed = apply_json_patch(before_document, operations)
        patched["metadata"]["document_version"] = article.version + 1
        patched["metadata"]["modified_at"] = utc_now_text()
        patched.setdefault("history", []).append({
            "at": utc_now_text(), "action": "patched", "fields": changed,
            "note": "AI protocol JSON Patch",
        })
        updated = Article.from_yaml(patched)
        before_quality = quality_validate(article)
        after_quality = quality_validate(updated)
        if (after_quality.quality_score < before_quality.quality_score or
                len(after_quality.errors) > len(before_quality.errors)):
            raise ProtocolFailure(
                "quality_regression", "Patch would reduce document quality",
                details={"before": before_quality.to_dict(), "after": after_quality.to_dict()},
            )
        compact, _ = compact_document(updated)
        response = {
            "article_id": article_id,
            "previous_version": article.version,
            "version": updated.version,
            "changed_paths": changed,
            "dry_run": dry_run,
            "document": compact,
            "quality": after_quality.to_dict(),
        }
        if dry_run:
            output_json(protocol_success(response))
            return
        try:
            atomic_update(
                updated, old_path, idx,
                vector_upsert=_vector_upsert, vector_remove=_vector_remove,
            )
        except Exception as exc:
            raise ProtocolFailure(
                "storage_failed", "Document and indexes could not be synchronized",
                details=str(exc), retryable=True,
            ) from exc
        rebuild_catalog()
        git_auto_commit("patch", article_id, updated.title)
        append_log("patch", article_id=article_id, title=updated.title,
                   details=f"v{updated.version} paths={changed}")
        output_json(protocol_success(response))
    except ProtocolFailure as exc:
        _output_protocol_failure(exc)
    except (ValidationError, ValueError, OSError) as exc:
        _output_protocol_failure(ProtocolFailure(
            "validation_failed", "Patched document failed validation", details=str(exc),
        ))


@cli.command()
@click.option("--title", "-t", required=False, help="Document title")
@click.option("--category", "-c", required=False, help="Category")
@click.option("--tags", default="", help="Comma-separated tags")
@click.option("--confidence", default=0.8, type=float, help="Confidence score (0.0-1.0)")
@click.option("--source", "-s", multiple=True, help="Source URL")
@click.option("--related", default="", help="Comma-separated related article IDs")
@click.option("--author", default="unknown", help="Author")
@click.option("--content-file", type=click.Path(exists=True), default=None,
              help="YAML content file")
@click.option("--content-stdin", is_flag=True, help="Read YAML content from stdin")
@click.option("--force", is_flag=True, help="Ignore duplicate warning")
@click.option("--document-file", default=None, help="AI JSON document file or - for stdin")
@click.option("--dry-run", is_flag=True, help="Validate without writing (document mode)")
@click.pass_context
def create(ctx, title, category, tags, confidence, source, related, author,
           content_file, content_stdin, force, document_file, dry_run):
    """Create a new document. Content must be YAML structured data."""
    idx: WikiIndex = ctx.obj["index"]

    if document_file is not None:
        if any([title, category, tags, source, related, content_file, content_stdin, force]):
            _output_protocol_failure(ProtocolFailure(
                "conflicting_input", "--document-file cannot be combined with legacy create options",
            ))
        try:
            payload = load_json_input(document_file)
            if not isinstance(payload, dict):
                raise ProtocolFailure("invalid_document", "Document input must be a JSON object")
            if payload.get("schema_version") == 2:
                article = Article.from_yaml(payload)
            else:
                doc_title = payload.get("title")
                doc_category = payload.get("category")
                doc_content = payload.get("content")
                if not doc_title or not doc_category or not isinstance(doc_content, dict):
                    raise ProtocolFailure(
                        "invalid_document", "title, category, and content object are required",
                    )
                if set(doc_content) == {"type", "data"} and isinstance(doc_content["data"], dict):
                    doc_content = {"type": doc_content["type"], **doc_content["data"]}
                raw_sources = payload.get("sources", [])
                source_urls = [
                    item["url"] if isinstance(item, dict) else item for item in raw_sources
                ]
                article = Article(
                    id=payload.get("id") or generate_id(doc_title, doc_category),
                    title=doc_title, category=doc_category, content=doc_content,
                    tags=payload.get("tags", []), confidence=float(payload.get("confidence", 0.8)),
                    sources=source_urls, related=payload.get("related", []),
                    author=payload.get("author", "ai-agent"),
                )
            similar = idx.find_similar_titles(article.title)
            if similar:
                _output_protocol_failure(ProtocolFailure(
                    "duplicate_conflict", "A similar document already exists",
                    details={"candidates": similar}, retryable=True,
                ))
            if not article.sources:
                article.confidence = min(article.confidence, 0.5)
                article.metadata["verification_status"] = "pending"
                article.verification = [{
                    "path": "/content/data", "level": "unverified",
                    "source_ids": [], "note": "Awaiting source verification",
                }]
            elif not article.verification:
                article.verification = [{
                    "path": "/content/data", "level": "sourced",
                    "source_ids": [f"src-{index}" for index in range(1, len(article.sources) + 1)],
                }]
            canonical_document(article)
            report = quality_validate(article)
            blocking = [error for error in report.errors if not (
                not article.sources and error.code == "MIN_SOURCES"
            )]
            if blocking:
                raise ProtocolFailure(
                    "quality_rejected", "Document failed the AI quality gate",
                    details=report.to_dict(),
                )
            compact, _ = compact_document(article)
            response = {
                "article_id": article.id, "dry_run": dry_run,
                "verification_status": article.metadata.get("verification_status", "active"),
                "document": compact, "quality": report.to_dict(),
            }
            if dry_run:
                output_json(protocol_success(response))
                return
            try:
                file_path = atomic_save(
                    article, idx,
                    vector_upsert=_vector_upsert, vector_remove=_vector_remove,
                )
            except Exception as exc:
                raise ProtocolFailure(
                    "storage_failed", "Document and indexes could not be synchronized",
                    details=str(exc), retryable=True,
                ) from exc
            rebuild_catalog()
            git_auto_commit("create", article.id, article.title)
            append_log("create", article_id=article.id, title=article.title, author=article.author)
            response["file_path"] = get_relative_path(file_path)
            output_json(protocol_success(response))
            return
        except ProtocolFailure as exc:
            _output_protocol_failure(exc)
        except (ValidationError, ValueError, OSError) as exc:
            _output_protocol_failure(ProtocolFailure(
                "validation_failed", "Document failed validation", details=str(exc),
            ))

    if dry_run:
        output_error("--dry-run is only supported with --document-file", "invalid_input")
    if not title or not category:
        output_error("--title and --category are required in legacy create mode", "invalid_input")

    content = _read_yaml_content(content_file, content_stdin)
    if content is None:
        output_error(msg("content_input_required"), "invalid_input")

    if not force:
        similar = idx.find_similar_titles(title)
        if similar:
            output_json({
                "status": "duplicate_warning",
                "message": "A document with a similar title already exists",
                "similar": similar,
                "hint": "Use --force flag to override",
            })
            sys.exit(1)

    article_id = generate_id(title, category)
    tag_list = [t.strip() for t in tags.split(",") if t.strip()] if tags else []
    related_list = [r.strip() for r in related.split(",") if r.strip()] if related else []

    article = Article(
        id=article_id, title=title, category=category, content=content,
        tags=tag_list, confidence=confidence, sources=list(source),
        related=related_list, author=author,
    )

    # Auto-generate _meta + _changelog
    comp, missing, hints = compute_completeness(content)
    article.set_meta({
        "maturity": determine_maturity(comp, len(article.sources), len(article.related), confidence),
        "completeness": comp,
        "missing_fields": missing[:10],
        "enrichment_hints": hints[:5],
    })
    article.append_changelog("created", list(article.content_keys()), f"document created (type={content.get('type','')})")

    # Quality gate
    report = quality_validate(article)
    if not report.passed and not force:
        append_log("quality_rejected", article_id=article_id, title=title,
                   details=f"errors={len(report.errors)}")
        output_json({
            "status": "quality_rejected",
            "message": f"Quality validation failed: {len(report.errors)} error(s)",
            "report": report.to_dict(),
            "hint": "Use --force to bypass quality gate (confidence will be lowered automatically)",
        })
        sys.exit(1)

    if not report.passed and force:
        article.confidence = min(article.confidence, 0.5)

    # #15: Atomic save
    file_path = atomic_save(
        article, idx,
        vector_upsert=_vector_upsert, vector_remove=_vector_remove,
    )

    # #8: Auto cross-referencing
    auto_linked = []
    if not related_list:
        candidates = idx.find_related_candidates(article)
        strong = [c for c in candidates if c["score"] >= 0.5]
        if strong:
            article.related = [c["id"] for c in strong]
            atomic_update(article, file_path, idx)
            # Add reverse links
            for c in strong:
                _add_reverse_link(idx, c["id"], article_id)
            auto_linked = [{"id": c["id"], "title": c["title"], "reason": c["reason"]}
                           for c in strong]

    rebuild_catalog()
    git_auto_commit("create", article_id, title)
    append_log("create", article_id=article_id, title=title, author=author)

    result = {
        "status": "ok",
        "action": "created",
        "article_id": article_id,
        "file_path": get_relative_path(file_path),
        "maturity": report.maturity,
        "quality_score": report.quality_score,
    }
    if auto_linked:
        result["auto_linked"] = auto_linked
    if report.warnings:
        result["quality_warnings"] = [w.message for w in report.warnings]
    output_json(result)


@cli.command()
@click.argument("article_id")
@click.option("--title", default=None, help="Change title")
@click.option("--tags", default=None, help="Replace tags (comma-separated)")
@click.option("--confidence", default=None, type=float, help="Change confidence")
@click.option("--source", "-s", multiple=True, help="Add source URL")
@click.option("--related", default=None, help="Add related articles (comma-separated)")
@click.option("--content-file", type=click.Path(exists=True), default=None)
@click.option("--content-stdin", is_flag=True)
@click.pass_context
def update(ctx, article_id, title, tags, confidence, source, related,
           content_file, content_stdin):
    """Update an existing document."""
    idx: WikiIndex = ctx.obj["index"]

    article, old_path = load_article_with_path(article_id)
    if not article:
        output_error(msg("not_found", article_id), "not_found")

    changes = []
    if title is not None:
        article.title = title
        changes.append("title")
    if tags is not None:
        article.tags = [t.strip() for t in tags.split(",") if t.strip()]
        changes.append("tags")
    if confidence is not None:
        article.confidence = confidence
        changes.append("confidence")
    if source:
        article.sources = list(set(article.sources + list(source)))
        changes.append("sources")
    if related is not None:
        # #1 fix: overwrite → merge (preserve order, deduplicate)
        new_related = [r.strip() for r in related.split(",") if r.strip()]
        article.related = list(dict.fromkeys(article.related + new_related))
        changes.append("related")

    content = _read_yaml_content(content_file, content_stdin)
    if content is not None:
        # Merge mode: overwrite only new fields, keep existing keys
        if isinstance(article.content, dict) and isinstance(content, dict):
            article.content.update(content)
        else:
            article.content = content
        changes.append("content")

    article.version += 1
    article.last_modified = datetime.now(timezone.utc)

    # #15: Atomic update
    atomic_update(
        article, old_path, idx,
        vector_upsert=_vector_upsert, vector_remove=_vector_remove,
    )
    rebuild_catalog()
    git_auto_commit("update", article_id, article.title)
    append_log("update", article_id=article_id, title=article.title,
               details=f"v{article.version} changed=[{','.join(changes)}]")

    output_json({
        "status": "ok",
        "action": "updated",
        "article_id": article_id,
        "version": article.version,
    })


@cli.command()
@click.argument("article_id")
@click.option("--confirm", is_flag=True, required=True, help="Confirm deletion")
@click.pass_context
def delete(ctx, article_id, confirm):
    """Delete a document."""
    idx: WikiIndex = ctx.obj["index"]

    article = load_article(article_id)
    if not article:
        output_error(msg("not_found", article_id), "not_found")

    delete_article_file(article_id)
    delete_source_files(article_id)
    idx.remove(article_id)
    _vector_remove(article_id)
    rebuild_catalog()
    git_auto_commit("delete", article_id, article.title)
    append_log("delete", article_id=article_id, title=article.title)

    output_json({"status": "ok", "action": "deleted", "article_id": article_id})


@cli.command()
@click.argument("article_id")
@click.option("--human", is_flag=True, default=False, help="Mark as human-verified (human_verified=True)")
@click.option("--by", default="human", help="Verifier name (used with --human)")
@click.pass_context
def verify(ctx, article_id, human, by):
    """Update the document verification date to now. Use --human to mark as human-verified."""
    idx: WikiIndex = ctx.obj["index"]

    article, old_path = load_article_with_path(article_id)
    if not article:
        output_error(msg("not_found", article_id), "not_found")

    now = datetime.now(timezone.utc)
    article.last_verified = now
    article.last_modified = now

    # P2-2: Handle human verification
    if human:
        meta = article.get_meta() or {}
        meta["human_verified"] = True
        meta["verified_by"] = by
        meta["verified_at"] = Article._fmt(now)
        article.set_meta(meta)
        idx.set_human_verified(article_id, by, Article._fmt(now))

    # #15: Atomic update
    atomic_update(article, old_path, idx)
    append_log("verify", article_id=article_id, title=article.title,
               details=f"human={human} by={by}" if human else "")

    result = {
        "status": "ok",
        "action": "human_verified" if human else "verified",
        "article_id": article_id,
        "last_verified": Article._fmt(now),
    }
    if human:
        result["human_verified"] = True
        result["verified_by"] = by
        result["verified_at"] = Article._fmt(now)
    output_json(result)


@cli.command()
@click.option("--days", "-d", default=90, help="Threshold in days (default: 90)")
@click.option("--category", "-c", default=None, help="Category filter")
@click.pass_context
def stale(ctx, days, category):
    """List stale documents."""
    idx: WikiIndex = ctx.obj["index"]
    results = idx.get_stale(days, category)
    output_json({
        "status": "ok",
        "threshold_days": days,
        "count": len(results),
        "stale_articles": results,
    })


@cli.command()
@click.option("--limit", "-n", default=50, help="Maximum number of tags")
@click.option("--min-count", default=1, help="Minimum document count")
@click.pass_context
def tags(ctx, limit, min_count):
    """List all tags and the document count for each tag."""
    idx: WikiIndex = ctx.obj["index"]
    all_tags = idx.get_all_tags()
    filtered = [t for t in all_tags if t["count"] >= min_count][:limit]
    output_json({
        "status": "ok",
        "total_unique_tags": len(all_tags),
        "shown": len(filtered),
        "tags": filtered,
    })


@cli.command()
@click.argument("tagname")
@click.option("--limit", "-n", default=20, help="Maximum number of results")
@click.pass_context
def tag(ctx, tagname, limit):
    """List documents for a specific tag."""
    idx: WikiIndex = ctx.obj["index"]
    results = idx.get_articles_by_tag(tagname)[:limit]
    output_json({
        "status": "ok",
        "tag": tagname,
        "count": len(results),
        "articles": results,
    })


@cli.command()
@click.pass_context
def reindex(ctx):
    """Scan the articles/ directory and rebuild the index."""
    from ai_wiki.storage import get_articles_dir, _load_yaml_file

    idx: WikiIndex = ctx.obj["index"]
    articles_dir = get_articles_dir()
    items = []

    if articles_dir.exists():
        for yaml_file in articles_dir.rglob("*.yaml"):
            data = _load_yaml_file(yaml_file)
            if data and "id" in data:
                article = Article.from_yaml(data)
                rel_path = get_relative_path(yaml_file)
                items.append((article, rel_path))

    idx.rebuild(items)
    rebuild_catalog()
    append_log("reindex", details=f"article_count={len(items)}")

    output_json({
        "status": "ok",
        "action": "reindexed",
        "article_count": len(items),
    })


@cli.command("migrate-schema")
@click.option("--apply", "apply_changes", is_flag=True,
              help="Write migrated v2 files; default is dry-run")
@click.option("--no-backup", is_flag=True,
              help="Do not retain v1 copies under backups/ (only with --apply)")
@click.pass_context
def migrate_schema(ctx, apply_changes, no_backup):
    """Validate and migrate legacy article YAML files to schema v2."""
    root = get_wiki_root()
    report = migrate_article_files(
        root, dry_run=not apply_changes, backup=not no_backup,
    )
    if apply_changes:
        from ai_wiki.storage import get_articles_dir, _load_yaml_file
        idx: WikiIndex = ctx.obj["index"]
        items = []
        articles_dir = get_articles_dir()
        for path in articles_dir.rglob("*.yaml") if articles_dir.exists() else []:
            data = _load_yaml_file(path)
            if data and "id" in data:
                items.append((Article.from_yaml(data), get_relative_path(path)))
        idx.rebuild(items)
        rebuild_catalog()
    output_json({
        "status": "ok" if not report["failed"] else "partial",
        "action": "schema_migration",
        **report,
    })


@cli.command("schema-json")
def schema_json():
    """Print the canonical schema-v2 JSON Schema."""
    output_json(document_json_schema())


@cli.command()
@click.argument("article_id")
@click.pass_context
def backlinks(ctx, article_id):
    """List all documents that reference this document."""
    idx: WikiIndex = ctx.obj["index"]
    article = load_article(article_id)
    if not article:
        output_error(msg("not_found", article_id), "not_found")

    results = idx.get_backlinks(article_id)
    output_json({
        "status": "ok",
        "article_id": article_id,
        "title": article.title,
        "backlink_count": len(results),
        "backlinks": results,
    })


@cli.command("sync-backlinks")
@click.pass_context
def sync_backlinks(ctx):
    """Bulk-synchronize bidirectional backlinks across all documents."""
    idx: WikiIndex = ctx.obj["index"]
    result = idx.sync_all_backlinks()
    from ai_wiki.catalog import rebuild_catalog
    rebuild_catalog()
    output_json({
        "status": "ok",
        "action": "sync_backlinks",
        "added_count": result["total"],
        "added": result["added"],
        "failed": result["failed"],
    })



@cli.command()
@click.option("--fix", is_flag=True, help="Fix auto-correctable issues")
@click.pass_context
def lint(ctx, fix):
    """Wiki health check. Use --fix for automatic repairs."""
    idx: WikiIndex = ctx.obj["index"]

    orphans = idx.get_orphans()
    broken_refs = idx.get_broken_refs()
    one_way_links = idx.get_one_way_links()
    no_sources = idx.get_no_sources()
    low_confidence = idx.get_low_confidence()

    # #10: Conflict detection assist
    potential_conflicts = idx.find_potential_conflicts()

    fixes_applied = []

    if fix:
        # Bulk load: collect needed document IDs first and load all at once
        needed_ids = set()
        for link in one_way_links:
            needed_ids.add(link["to"])
        for ref in broken_refs:
            needed_ids.add(ref["from"])

        loaded_cache: dict[str, tuple] = {}
        for aid in needed_ids:
            loaded_cache[aid] = load_article_with_path(aid)

        # #11: One-way link → auto-add reverse link
        for link in one_way_links:
            target, target_path = loaded_cache.get(link["to"], (None, None))
            if target and link["from"] not in target.related:
                target.related.append(link["from"])
                target.last_modified = datetime.now(timezone.utc)
                try:
                    atomic_update(target, target_path, idx)
                    fixes_applied.append({
                        "type": "added_reverse_link",
                        "from": link["to"], "to": link["from"],
                    })
                except Exception as e:
                    logger.debug("lint --fix: failed to add reverse link (%s): %s", link["to"], e)

        # #11: Broken reference → remove from related
        for ref in broken_refs:
            article, article_path = loaded_cache.get(ref["from"], (None, None))
            if article and ref["to"] in article.related:
                article.related.remove(ref["to"])
                article.last_modified = datetime.now(timezone.utc)
                try:
                    atomic_update(article, article_path, idx)
                    fixes_applied.append({
                        "type": "removed_broken_ref",
                        "from": ref["from"], "removed": ref["to"],
                    })
                except Exception as e:
                    logger.debug("lint --fix: failed to remove broken reference (%s): %s", ref["from"], e)

        if fixes_applied:
            rebuild_catalog()
            # Re-check after fixes
            orphans = idx.get_orphans()
            broken_refs = idx.get_broken_refs()
            one_way_links = idx.get_one_way_links()

    issues = {
        "orphan_articles": orphans,
        "broken_references": broken_refs,
        "one_way_links": one_way_links,
        "no_sources": no_sources,
        "low_confidence": low_confidence,
        "potential_conflicts": potential_conflicts,
    }
    total_issues = sum(len(v) for v in issues.values())

    append_log("lint", details=f"issues={total_issues} fixes={len(fixes_applied)}")
    result = {
        "status": "ok",
        "total_articles": idx.count(),
        "total_issues": total_issues,
        "issues": issues,
    }
    if fixes_applied:
        result["fixes_applied"] = fixes_applied
        result["fixes_count"] = len(fixes_applied)
    output_json(result)


# ── Quality commands ────────────────────────────────────

@cli.command()
@click.argument("article_id")
@click.pass_context
def quality(ctx, article_id):
    """Print quality report for a single document."""
    article = load_article(article_id)
    if not article:
        output_error(msg("not_found", article_id), "not_found")

    report = quality_validate(article)
    append_log("quality", article_id=article_id, title=article.title,
               details=f"score={report.quality_score} maturity={report.maturity}")
    output_json({"status": "ok", **report.to_dict()})


@cli.command("quality-all")
@click.option("--maturity", "-m", default=None,
              type=click.Choice(["stub", "draft", "review", "mature"]))
@click.option("--cached", "use_cached", is_flag=True, default=False,
              help="DB metadata only (no file loading). Fast, but quality_score reflects last indexing.")
@click.pass_context
def quality_all(ctx, maturity, use_cached):
    """Batch quality check for all documents. --cached: DB metadata only (no file loading)."""
    idx: WikiIndex = ctx.obj["index"]

    if use_cached:
        # DB metadata-only mode: use stored quality_score/maturity without file loading
        all_meta = idx.get_all_articles_meta()
        reports = []
        summary = {"stub": 0, "draft": 0, "review": 0, "mature": 0}
        for m in all_meta:
            m_maturity = m.get("maturity") or "stub"
            m_score = m.get("quality_score") or 0.0
            summary[m_maturity] = summary.get(m_maturity, 0) + 1
            if maturity and m_maturity != maturity:
                continue
            reports.append({
                "article_id": m["id"], "title": m["title"],
                "maturity": m_maturity, "quality_score": m_score,
                "errors": 0, "warnings": 0,
            })
        reports.sort(key=lambda r: r["quality_score"])
        avg = round(sum(r["quality_score"] for r in reports) / max(len(reports), 1), 3)
        output_json({
            "status": "ok",
            "total_articles": len(all_meta),
            "filtered": len(reports),
            "avg_score": avg,
            "maturity_summary": summary,
            "reports": reports,
            "cached": True,
        })
        return

    articles = list_all_articles()
    reports = []
    summary = {"stub": 0, "draft": 0, "review": 0, "mature": 0}

    for article in articles:
        report = quality_validate(article)
        summary[report.maturity] = summary.get(report.maturity, 0) + 1
        if maturity and report.maturity != maturity:
            continue
        reports.append({
            "article_id": article.id, "title": article.title,
            "maturity": report.maturity, "quality_score": report.quality_score,
            "errors": len(report.errors), "warnings": len(report.warnings),
        })

    reports.sort(key=lambda r: r["quality_score"])
    avg = round(sum(r["quality_score"] for r in reports) / max(len(reports), 1), 3)

    output_json({
        "status": "ok",
        "total_articles": len(articles),
        "filtered": len(reports),
        "avg_score": avg,
        "maturity_summary": summary,
        "reports": reports,
    })


@cli.command()
@click.argument("article_id")
@click.pass_context
def review(ctx, article_id):
    """Generate a validation checklist for a document."""
    idx: WikiIndex = ctx.obj["index"]
    article = load_article(article_id)
    if not article:
        output_error(msg("not_found", article_id), "not_found")

    report = quality_validate(article)
    checklist = []

    # Quality violations
    for v in report.violations:
        checklist.append({"type": v.level, "code": v.code, "message": v.message})

    # Empty fields
    if isinstance(article.content, dict):
        for k, v in article.content.items():
            if k.startswith("_"):
                continue
            if not v or (isinstance(v, (list, dict)) and len(v) == 0):
                checklist.append({"type": "empty_field", "code": "EMPTY", "message": f"'{k}' is empty"})

    # Unlinked related documents
    candidates = idx.find_related_candidates(article)
    for c in candidates:
        if c["id"] not in article.related and c["score"] >= 0.4:
            checklist.append({
                "type": "suggest", "code": "UNLINKED",
                "message": f"Unlinked related document: {c['title']} (score={c['score']})",
            })

    # _meta.missing_fields
    meta = article.get_meta()
    for mf in meta.get("missing_fields", [])[:10]:
        checklist.append({"type": "suggest", "code": "MISSING_FIELD", "message": f"Missing field: {mf}"})

    append_log("review", article_id=article_id, title=article.title,
               details=f"items={len(checklist)}")
    output_json({
        "status": "ok",
        "article_id": article_id,
        "title": article.title,
        "maturity": report.maturity,
        "quality_score": report.quality_score,
        "checklist_count": len(checklist),
        "checklist": checklist,
    })


# ── #9: Ingest pipeline ───────────────────────────────

@cli.command()
@click.argument("file_path", type=click.Path(exists=True))
@click.option("--title", "-t", default=None, help="Document title (defaults to filename)")
@click.option("--category", "-c", default="misc", help="Category")
@click.option("--tags", default="", help="Comma-separated tags")
@click.option("--source-url", "-s", default="", help="Source URL")
@click.option("--author", default="unknown", help="Author")
@click.pass_context
def ingest(ctx, file_path, title, category, tags, source_url, author):
    """Ingest a source file. Saves to sources/ and creates a stub document."""
    idx: WikiIndex = ctx.obj["index"]
    source = Path(file_path)

    if not title:
        title = source.stem.replace("-", " ").replace("_", " ").title()

    article_id = generate_id(title, category)

    # 1. Copy source file to sources/
    saved_path = save_source_file(article_id, source)

    # 2. Create stub document
    tag_list = [t.strip() for t in tags.split(",") if t.strip()] if tags else []
    tag_list.append("ingested")

    ext = source.suffix.lower()
    content = {
        "type": "ingested",
        "original_filename": source.name,
        "file_type": ext.lstrip("."),
        "file_size_bytes": source.stat().st_size,
        "status": "pending_review",
        "notes": "The LLM should analyze the source file and populate the content",
    }

    sources_list = [source_url] if source_url else []

    article = Article(
        id=article_id, title=title, category=category,
        content=content, tags=tag_list, confidence=0.3,
        sources=sources_list, author=author,
    )

    article_path = atomic_save(
        article, idx,
        vector_upsert=_vector_upsert, vector_remove=_vector_remove,
    )
    rebuild_catalog()
    append_log("ingest", article_id=article_id, title=title,
               details=f"file={source.name}")

    output_json({
        "status": "ok",
        "action": "ingested",
        "article_id": article_id,
        "source_file": get_relative_path(saved_path),
        "article_file": get_relative_path(article_path),
        "hint": f"Use {COMMAND_NAME} update to populate the content",
    })


# ── #12: Data gap analysis ──────────────────────────────

@cli.command()
@click.pass_context
def gaps(ctx):
    """Analyze data gaps by category. Identifies weak areas."""
    idx: WikiIndex = ctx.obj["index"]
    cur = idx.conn.cursor()

    cur.execute("""
        SELECT category, COUNT(*) as cnt, AVG(confidence) as avg_conf,
               MIN(confidence) as min_conf
        FROM articles_meta
        GROUP BY category ORDER BY category
    """)

    categories = []
    for row in cur.fetchall():
        r = dict(row)
        # Cross-reference density
        cur2 = idx.conn.cursor()
        cur2.execute("""
            SELECT COUNT(*) as rel_count FROM article_relations
            WHERE from_id IN (SELECT id FROM articles_meta WHERE category = ?)
        """, (r["category"],))
        rel_row = cur2.fetchone()
        total_rels = rel_row["rel_count"] if rel_row else 0
        avg_rels = round(total_rels / r["cnt"], 1) if r["cnt"] > 0 else 0

        count_score = min(r["cnt"] / 10, 1.0)
        conf_score = r["avg_conf"]
        ref_score = min(avg_rels / 3, 1.0)
        health = round(count_score * 0.3 + conf_score * 0.4 + ref_score * 0.3, 2)

        categories.append({
            "category": r["category"],
            "article_count": r["cnt"],
            "avg_confidence": round(r["avg_conf"], 2),
            "min_confidence": round(r["min_conf"], 2),
            "avg_cross_references": avg_rels,
            "health_score": health,
        })

    categories.sort(key=lambda x: x["health_score"])
    weak = [c for c in categories if c["health_score"] < 0.5]

    recommendations = []
    for w in weak:
        if w["article_count"] < 3:
            recommendations.append(f"{w['category']}: insufficient articles ({w['article_count']} articles)")
        if w["avg_confidence"] < 0.7:
            recommendations.append(f"{w['category']}: low avg confidence ({w['avg_confidence']})")
        if w["avg_cross_references"] < 1:
            recommendations.append(f"{w['category']}: insufficient cross-refs (avg {w['avg_cross_references']} articles)")

    append_log("gaps", details=f"categories={len(categories)} weak={len(weak)}")
    output_json({
        "status": "ok",
        "total_categories": len(categories),
        "categories": categories,
        "weak_areas": weak,
        "recommendations": recommendations,
    })


@cli.command()
@click.option("--limit", "-n", default=10, help="Top N item count")
@click.pass_context
def stats(ctx, limit):
    """Print access statistics. Top N most-viewed documents and Top N search queries."""
    idx: WikiIndex = ctx.obj["index"]
    data = idx.get_access_stats(limit=limit)
    output_json({
        "status": "ok",
        **data,
    })


# ── #19: Version history ───────────────────────────────

@cli.command()
@click.argument("article_id")
@click.option("--limit", "-n", default=10, help="Maximum number of history entries")
@click.pass_context
def history(ctx, article_id, limit):
    """Retrieve the Git change history for a document."""
    article, file_path = load_article_with_path(article_id)
    if not article:
        output_error(msg("not_found", article_id), "not_found")

    wiki_root = get_wiki_root()
    git_dir = wiki_root / ".git"

    if not git_dir.exists():
        output_json({
            "status": "ok",
            "article_id": article_id,
            "title": article.title,
            "current_version": article.version,
            "history_count": 0,
            "history": [],
            "hint": "Git is not initialized. Run: 'git init && git add -A && git commit -m init'.",
        })
        return

    rel_path = str(file_path.relative_to(wiki_root))
    result = subprocess.run(
        ["git", "log", f"--max-count={limit}",
         "--pretty=format:%H|%ai|%s", "--follow", "--", rel_path],
        cwd=str(wiki_root), capture_output=True, text=True,
    )

    entries = []
    if result.returncode == 0 and result.stdout.strip():
        for line in result.stdout.strip().split("\n"):
            parts = line.split("|", 2)
            if len(parts) == 3:
                entries.append({
                    "commit": parts[0],
                    "date": parts[1],
                    "message": parts[2],
                })

    output_json({
        "status": "ok",
        "article_id": article_id,
        "title": article.title,
        "current_version": article.version,
        "history_count": len(entries),
        "history": entries,
    })


# ── #5: Verification queue ──────────────────────────────

@cli.command("verify-queue")
@click.pass_context
def verify_queue(ctx):
    """List documents with confidence < 0.5 or insufficient sources. Requires verification."""
    from ai_wiki.quality import auto_confidence
    idx: WikiIndex = ctx.obj["index"]

    # First filter: extract only document IDs with confidence < 0.5 from DB (no file loading)
    low_conf_meta = idx.get_low_confidence(threshold=0.5)
    low_conf_ids = {m["id"] for m in low_conf_meta}

    # Also include documents with no sources: query DB for articles absent from article_sources
    cur = idx.conn.cursor()
    cur.execute("""
        SELECT m.id FROM articles_meta m
        LEFT JOIN article_sources s ON m.id = s.article_id
        WHERE s.article_id IS NULL
    """)
    no_source_ids = {row["id"] for row in cur.fetchall()}

    # Union of target IDs
    candidate_ids = low_conf_ids | no_source_ids

    if not candidate_ids:
        output_json({"status": "ok", "count": 0, "queue": []})
        return

    # Second pass: load only candidate documents individually to compute auto_confidence
    queue = []
    for article_id in candidate_ids:
        a = load_article(article_id)
        if not a:
            continue
        auto_conf = auto_confidence(a)
        if a.confidence < 0.5 or auto_conf < 0.5 or not a.sources:
            queue.append({
                "id": a.id, "title": a.title,
                "current_confidence": a.confidence,
                "suggested_confidence": auto_conf,
                "sources": len(a.sources),
                "reason": "no_sources" if not a.sources else "low_confidence",
            })
    queue.sort(key=lambda x: x["current_confidence"])
    output_json({"status": "ok", "count": len(queue), "queue": queue})


# ── #6: Wiki→AI TODO system ──────────────────────────

@cli.command()
@click.option("--max-items", "-n", default=10, help="Maximum number of items")
@click.pass_context
def todo(ctx, max_items):
    """Auto-generated to-do list from the entire wiki."""
    idx: WikiIndex = ctx.obj["index"]
    tasks = []

    # First pass: collect items from DB meta without file loading
    all_meta = idx.get_all_articles_meta()

    # Document IDs with no sources (DB-based)
    cur = idx.conn.cursor()
    cur.execute("""
        SELECT m.id FROM articles_meta m
        LEFT JOIN article_sources s ON m.id = s.article_id
        WHERE s.article_id IS NULL
    """)
    no_source_ids = {row["id"] for row in cur.fetchall()}

    # Tasks handleable with DB meta only + collect IDs needing full load for missing_fields
    need_full_load_ids = set()
    for m in all_meta:
        aid = m["id"]
        m_maturity = m.get("maturity") or "unknown"

        # Enrich stub/draft documents
        if m_maturity in ("stub", "draft"):
            tasks.append({
                "priority": "high" if m_maturity == "stub" else "medium",
                "type": "enrich",
                "article_id": aid,
                "title": m["title"],
                "action": f"maturity '{m_maturity}' → needs enrichment",
            })
            # missing_fields requires actual file loading
            need_full_load_ids.add(aid)

        # No sources (DB-based)
        if aid in no_source_ids:
            tasks.append({
                "priority": "high",
                "type": "add_source",
                "article_id": aid,
                "title": m["title"],
                "action": "no source → add source or lower confidence",
            })

        # Low confidence (DB-based)
        if (m.get("confidence") or 1.0) < 0.5:
            tasks.append({
                "priority": "high",
                "type": "verify",
                "article_id": aid,
                "title": m["title"],
                "action": f"confidence {m['confidence']} → needs verification",
            })

    # Second pass: load only documents that need missing_fields
    for aid in need_full_load_ids:
        a = load_article(aid)
        if not a:
            continue
        meta = a.get_meta()
        for mf in meta.get("missing_fields", [])[:3]:
            tasks.append({
                "priority": "medium",
                "type": "fill_field",
                "article_id": a.id,
                "title": a.title,
                "action": f"Missing field: {mf}",
            })

    # Orphan documents
    orphans = idx.get_orphans()
    for o in orphans:
        tasks.append({
            "priority": "medium",
            "type": "link",
            "article_id": o["id"],
            "title": o["title"],
            "action": "no cross-refs → add related",
        })

    # One-way links
    one_way = idx.get_one_way_links()
    for ow in one_way[:5]:
        tasks.append({
            "priority": "low",
            "type": "fix_link",
            "article_id": ow["from"],
            "title": "",
            "action": f"one-way link → {ow['to']}: add reverse link (lint --fix)",
        })

    # Sort by priority
    priority_order = {"high": 0, "medium": 1, "low": 2}
    tasks.sort(key=lambda t: priority_order.get(t["priority"], 9))

    append_log("todo", details=f"tasks={len(tasks)}")
    output_json({
        "status": "ok",
        "total_tasks": len(tasks),
        "tasks": tasks[:max_items],
    })


# ── #11: Vector search ───────────────────────────────

def _vector_status(idx: WikiIndex | None = None, load_model: bool = False) -> dict:
    """Return operational status for the required vector search subsystem."""
    from ai_wiki.storage import get_data_dir

    vec_db_path = get_data_dir() / "vectors.db"
    article_count = idx.count() if idx is not None else None
    status = {
        "required": True,
        "ready": False,
        "indexed": False,
        "sqlite_vec": False,
        "sentence_transformers": False,
        "model": "not_checked",
        "vectors_db": str(vec_db_path),
        "vectors_db_exists": vec_db_path.exists(),
        "vector_count": 0,
        "article_count": article_count,
        "warnings": [],
        "actions": [],
    }

    import importlib.util

    if importlib.util.find_spec("sqlite_vec") is not None:
        status["sqlite_vec"] = True
    else:
        status["warnings"].append("sqlite-vec unavailable")
        status["actions"].append(f"Install sqlite-vec, then run: {COMMAND_NAME} vindex")

    if importlib.util.find_spec("sentence_transformers") is not None:
        status["sentence_transformers"] = True
    else:
        status["warnings"].append("sentence-transformers unavailable")
        status["actions"].append(f"Install sentence-transformers, then run: {COMMAND_NAME} vindex")

    if load_model and status["sentence_transformers"]:
        try:
            from ai_wiki.vector import _get_model
            _get_model()
            status["model"] = "loaded"
        except Exception as e:
            status["model"] = "error"
            status["warnings"].append(f"embedding model load failed: {e}")
            status["actions"].append(f"Check network/model cache, then run: {COMMAND_NAME} doctor --load-model")
    elif not load_model:
        status["model"] = "skipped"

    if status["sqlite_vec"] and vec_db_path.exists():
        try:
            from ai_wiki.vector import VectorIndex
            vidx = VectorIndex(db_path=vec_db_path)
            status["vector_count"] = vidx.count()
            vidx.close()
        except Exception as e:
            status["warnings"].append(f"vector index unavailable: {e}")
            status["actions"].append(f"Rebuild vector index: {COMMAND_NAME} vindex")

    if article_count is not None:
        status["indexed"] = article_count == 0 or status["vector_count"] >= article_count
        if article_count > 0 and status["vector_count"] < article_count:
            status["actions"].append(f"Vector index is stale or empty. Run: {COMMAND_NAME} vindex")
    else:
        status["indexed"] = status["vector_count"] > 0

    status["ready"] = (
        status["sqlite_vec"]
        and status["sentence_transformers"]
        and status["indexed"]
        and (article_count == 0 or status["vector_count"] > 0)
        and status["model"] != "error"
    )
    status["actions"] = list(dict.fromkeys(status["actions"]))
    return status


@cli.command()
@click.option("--load-model", is_flag=True,
              help="Also instantiate the embedding model. This can download/load model files.")
@click.pass_context
def doctor(ctx, load_model):
    """Diagnose wiki storage, search index, and required vector search readiness."""
    idx: WikiIndex = ctx.obj["index"]
    output_json({
        "status": "ok",
        "wiki_root": str(get_wiki_root()),
        "article_count": idx.count(),
        "vector": _vector_status(idx=idx, load_model=load_model),
    })


@cli.command()
@click.argument("query")
@click.option("--limit", "-n", default=10, help="Maximum number of results")
@click.pass_context
def vsearch(ctx, query, limit):
    """Semantic vector search (sentence-transformers)."""
    try:
        from ai_wiki.vector import VectorIndex
        vidx = VectorIndex()
        results = vidx.search(query, limit=limit)
        vidx.close()
        append_log("vsearch", details=f"query='{query}' count={len(results)}")
        output_json({"status": "ok", "count": len(results), "results": results})
    except Exception as e:
        output_error(msg("vector_search_error", e), "vector_error")


@cli.command()
@click.argument("article_id")
@click.option("--limit", "-n", default=10, help="Maximum number of results")
@click.option("--method", "-m", default="auto",
              type=click.Choice(["auto", "fts", "vector", "tags"]),
              help="Search method (auto=auto-select, fts=FTS5, vector=vector, tags=tags)")
@click.pass_context
def similar(ctx, article_id, limit, method):
    """Find documents similar to the specified document."""
    idx: WikiIndex = ctx.obj["index"]
    article = load_article(article_id)
    if not article:
        output_error(msg("not_found", article_id), "not_found")

    results = []
    used_method = method

    # Try vector search
    if method in ("auto", "vector"):
        try:
            from ai_wiki.vector import VectorIndex
            vidx = VectorIndex()
            # Build query from title + tags
            query_text = article.title + " " + " ".join(article.tags)
            vec_results = vidx.search(query_text, limit=limit + 1)
            vidx.close()
            # Exclude self
            vec_results = [r for r in vec_results if r.get("id") != article_id][:limit]
            if vec_results:
                results = vec_results
                used_method = "vector"
        except Exception:
            pass

    # Try FTS5 search (if vector failed or fts selected)
    if not results and method in ("auto", "fts"):
        try:
            # Search by title words via FTS
            query_words = article.title.split()[:5]
            fts_query = " OR ".join(query_words) if query_words else article.title
            fts_results = idx.search(fts_query, limit=limit + 5)
            # Exclude self, normalize rank
            filtered = [r for r in fts_results if r["id"] != article_id][:limit]
            if filtered:
                results = [{"id": r["id"], "title": r["title"],
                            "category": r["category"], "score": abs(r.get("rank", 0)),
                            "snippet": r.get("snippet", "")} for r in filtered]
                used_method = "fts"
        except Exception:
            pass

    # Tag/category-based fallback
    if not results or method == "tags":
        candidates = idx.find_related_candidates(article, limit=limit)
        candidates = [c for c in candidates if c["id"] != article_id]
        results = [{"id": c["id"], "title": c["title"],
                    "category": c.get("category", ""),
                    "score": c["score"], "reason": c["reason"]}
                   for c in candidates]
        if method == "tags":
            used_method = "tags"
        elif not results:
            used_method = "tags (fallback)"

    append_log("similar", article_id=article_id,
               details=f"method={used_method} count={len(results)}")
    output_json({
        "status": "ok",
        "article_id": article_id,
        "title": article.title,
        "method": used_method,
        "count": len(results),
        "results": results,
    })


@cli.command("vindex")
@click.pass_context
def vector_reindex(ctx):
    """Rebuild the vector index (embed all documents)."""
    try:
        from ai_wiki.vector import VectorIndex
        articles = list_all_articles()
        vidx = VectorIndex()
        count = vidx.rebuild(articles)
        vidx.close()
        append_log("vindex", details=f"indexed={count}")
        output_json({"status": "ok", "action": "vector_reindexed", "article_count": count})
    except Exception as e:
        output_error(msg("vector_index_error", e), "vector_error")


# ── #10: Autonomous maintenance ────────────────────────

@cli.command()
@click.option("--fix", is_flag=True, default=True, help="Auto-fix (enabled by default)")
@click.pass_context
def maintain(ctx, fix):
    """Run lint + quality-all + todo in one step. Includes automatic fixes."""
    idx: WikiIndex = ctx.obj["index"]
    articles = list_all_articles()
    result = {"status": "ok", "actions": []}

    # 1. Lint + auto-fix
    orphans = idx.get_orphans()
    broken_refs = idx.get_broken_refs()
    one_way_links = idx.get_one_way_links()
    fixes = []

    if fix:
        # Bulk load: collect needed document IDs first and load all at once
        needed_ids = set()
        for link in one_way_links:
            needed_ids.add(link["to"])
        for ref in broken_refs:
            needed_ids.add(ref["from"])

        loaded_cache: dict[str, tuple] = {}
        for aid in needed_ids:
            loaded_cache[aid] = load_article_with_path(aid)

        for link in one_way_links:
            target, target_path = loaded_cache.get(link["to"], (None, None))
            if target and link["from"] not in target.related:
                target.related.append(link["from"])
                target.last_modified = datetime.now(timezone.utc)
                try:
                    atomic_update(target, target_path, idx)
                    fixes.append(f"reverse_link: {link['to']} ← {link['from']}")
                except Exception as e:
                    logger.debug("maintain: failed to add reverse link (%s): %s", link["to"], e)

        for ref in broken_refs:
            article, article_path = loaded_cache.get(ref["from"], (None, None))
            if article and ref["to"] in article.related:
                article.related.remove(ref["to"])
                article.last_modified = datetime.now(timezone.utc)
                try:
                    atomic_update(article, article_path, idx)
                    fixes.append(f"removed_broken: {ref['from']} → {ref['to']}")
                except Exception as e:
                    logger.debug("maintain: failed to remove broken reference (%s): %s", ref["from"], e)

    if fixes:
        rebuild_catalog()
        git_auto_commit("maintain", title="auto-fix lint issues")

    result["lint"] = {
        "orphans": len(orphans),
        "broken_refs": len(broken_refs),
        "one_way_links": len(one_way_links),
        "fixes_applied": len(fixes),
    }

    # 2. Quality summary
    summary = {"stub": 0, "draft": 0, "review": 0, "mature": 0}
    low_quality = []
    for article in articles:
        report = quality_validate(article)
        summary[report.maturity] = summary.get(report.maturity, 0) + 1
        if report.quality_score < 0.5:
            low_quality.append({
                "id": article.id, "title": article.title,
                "score": report.quality_score, "maturity": report.maturity,
            })

    low_quality.sort(key=lambda x: x["score"])
    result["quality"] = {
        "maturity_summary": summary,
        "low_quality_count": len(low_quality),
        "low_quality": low_quality[:5],
    }

    # 3. Top TODO items
    tasks = []
    for a in articles:
        meta = a.get_meta()
        maturity = meta.get("maturity", "unknown")
        if maturity in ("stub", "draft"):
            tasks.append({"priority": "high", "id": a.id, "title": a.title,
                          "action": f"maturity '{maturity}' → enrich"})
        if not a.sources:
            tasks.append({"priority": "high", "id": a.id, "title": a.title,
                          "action": "no source"})
        if a.confidence < 0.5:
            tasks.append({"priority": "high", "id": a.id, "title": a.title,
                          "action": f"confidence {a.confidence}"})

    priority_order = {"high": 0, "medium": 1, "low": 2}
    tasks.sort(key=lambda t: priority_order.get(t["priority"], 9))
    result["todo"] = {"count": len(tasks), "top": tasks[:5]}

    result["total_articles"] = len(articles)
    append_log("maintain", details=f"articles={len(articles)} fixes={len(fixes)}")
    output_json(result)


# ── Phase 1: Discovery / path / cluster ─────────────────

@cli.command()
@click.option("--isolated", is_flag=True, default=False, help="Show only isolated documents (no related links)")
@click.option("--low-quality", is_flag=True, default=False, help="Show only low-quality documents")
@click.option("--stale-days", default=90, help="Threshold days for stale documents")
@click.option("--limit", "-n", default=20, help="Maximum number of results")
@click.pass_context
def discover(ctx, isolated, low_quality, stale_days, limit):
    """Suggest isolated, low-quality, and stale documents in one command."""
    idx: WikiIndex = ctx.obj["index"]

    results = {
        "isolated": [],
        "low_quality": [],
        "stale": [],
        "recommendations": [],
    }

    # Isolated documents: handled by DB query only (no file loading)
    if not isolated or isolated:
        orphan_rows = idx.get_orphans()
        # get_orphans returns id, title from DB
        # Fetch DB meta to add category
        for o in orphan_rows[:limit]:
            cur = idx.conn.cursor()
            cur.execute("SELECT category FROM articles_meta WHERE id = ?", (o["id"],))
            row = cur.fetchone()
            cat = row["category"] if row else ""
            results["isolated"].append({
                "id": o["id"], "title": o["title"], "category": cat,
                "reason": "no related links",
            })
        results["isolated"] = results["isolated"][:limit]

    # Low-quality documents: first filter by DB meta quality_score/maturity, load if needed
    if not low_quality or low_quality:
        all_meta = idx.get_all_articles_meta()
        # First filter from DB meta (quality_score < 0.4 or maturity == stub)
        low_q_candidates = [
            m for m in all_meta
            if (m.get("quality_score") or 0.0) < 0.4 or (m.get("maturity") or "stub") == "stub"
        ]
        # Use DB values without file loading (speed over accuracy)
        for m in low_q_candidates:
            m_score = m.get("quality_score") or 0.0
            m_maturity = m.get("maturity") or "stub"
            results["low_quality"].append({
                "id": m["id"], "title": m["title"], "category": m["category"],
                "score": m_score, "maturity": m_maturity,
                "reason": f"quality={m_score:.2f}, maturity={m_maturity}",
            })
        results["low_quality"].sort(key=lambda x: x["score"])
        results["low_quality"] = results["low_quality"][:limit]

    # Stale documents: handled by DB query only (no file loading)
    stale_list = idx.get_stale(stale_days)
    results["stale"] = stale_list[:limit]
    
    # Recommendation messages
    if results["isolated"]:
        results["recommendations"].append(
            f"isolated documents {len(results['isolated'])} → use '{COMMAND_NAME} lint --fix' or add related manually"
        )
    if results["low_quality"]:
        results["recommendations"].append(
            f"low-quality documents {len(results['low_quality'])} → use '{COMMAND_NAME} quality <id>' for details"
        )
    if results["stale"]:
        results["recommendations"].append(
            f"stale documents {len(results['stale'])} → use '{COMMAND_NAME} verify <id>' to update verification date"
        )
    
    total = len(results["isolated"]) + len(results["low_quality"]) + len(results["stale"])
    append_log("discover", details=f"total_issues={total}")
    output_json({
        "status": "ok",
        "total_issues": total,
        "isolated_count": len(results["isolated"]),
        "low_quality_count": len(results["low_quality"]),
        "stale_count": len(results["stale"]),
        **results,
    })


@cli.command()
@click.argument("id1")
@click.argument("id2")
@click.option("--max-depth", default=5, help="Maximum search depth")
@click.pass_context
def path(ctx, id1, id2, max_depth):
    """Find the shortest connection path between two documents (BFS)."""
    idx: WikiIndex = ctx.obj["index"]
    
    # Verify both documents exist
    art1 = load_article(id1)
    art2 = load_article(id2)
    if not art1:
        output_error(msg("not_found", id1), "not_found")
    if not art2:
        output_error(msg("not_found", id2), "not_found")
    
    # Load the full relationship graph (bidirectional)
    all_articles = list_all_articles()
    graph: dict[str, set] = {}
    for a in all_articles:
        if a.id not in graph:
            graph[a.id] = set()
        for rel in a.related:
            graph[a.id].add(rel)
            if rel not in graph:
                graph[rel] = set()
            graph[rel].add(a.id)  # bidirectional
    
    # BFS
    from collections import deque
    queue = deque([(id1, [id1])])
    visited = {id1}
    found_path = None
    
    while queue:
        current, current_path = queue.popleft()
        if current == id2:
            found_path = current_path
            break
        if len(current_path) >= max_depth + 1:
            continue
        for neighbor in graph.get(current, set()):
            if neighbor not in visited:
                visited.add(neighbor)
                queue.append((neighbor, current_path + [neighbor]))
    
    if found_path:
        # Look up document titles along the path
        title_map = {a.id: a.title for a in all_articles}
        path_details = [
            {"id": pid, "title": title_map.get(pid, pid)}
            for pid in found_path
        ]
        append_log("path", details=f"{id1} -> {id2} steps={len(found_path)-1}")
        output_json({
            "status": "ok",
            "found": True,
            "from": {"id": id1, "title": art1.title},
            "to": {"id": id2, "title": art2.title},
            "steps": len(found_path) - 1,
            "path": path_details,
        })
    else:
        append_log("path", details=f"{id1} -> {id2} not_found depth={max_depth}")
        output_json({
            "status": "ok",
            "found": False,
            "from": {"id": id1, "title": art1.title},
            "to": {"id": id2, "title": art2.title},
            "message": msg("path_not_found_msg", max_depth),
            "hint": msg("path_not_found_hint"),
        })


@cli.command()
@click.option("--min-size", default=2, help="Minimum cluster size")
@click.option("--limit", "-n", default=10, help="Maximum number of clusters")
@click.pass_context
def cluster(ctx, min_size, limit):
    """Cluster documents into topic groups (tag/category based)."""
    idx: WikiIndex = ctx.obj["index"]
    # DB metadata only (no file loading): only id, title, category, tags, confidence needed
    all_meta = idx.get_all_articles_meta()

    if not all_meta:
        output_json({"status": "ok", "clusters": [], "total_clusters": 0, "total_articles": 0})
        return

    # Tag-based clustering: group documents with common tags
    # Connect documents sharing the same tag using Union-Find
    parent = {m["id"]: m["id"] for m in all_meta}

    def find(x):
        while parent[x] != x:
            parent[x] = parent[parent[x]]
            x = parent[x]
        return x

    def union(x, y):
        px, py = find(x), find(y)
        if px != py:
            parent[px] = py

    # Connect documents with the same tags
    tag_to_articles: dict[str, list] = {}
    for m in all_meta:
        for tag in m["tags"]:
            if tag not in tag_to_articles:
                tag_to_articles[tag] = []
            tag_to_articles[tag].append(m["id"])

    for tag, article_ids in tag_to_articles.items():
        for i in range(1, len(article_ids)):
            union(article_ids[0], article_ids[i])

    # Also connect documents in the same top-level category
    cat_to_articles: dict[str, list] = {}
    for m in all_meta:
        top_cat = m["category"].split("/")[0]
        if top_cat not in cat_to_articles:
            cat_to_articles[top_cat] = []
        cat_to_articles[top_cat].append(m["id"])

    for cat, article_ids in cat_to_articles.items():
        for i in range(1, len(article_ids)):
            union(article_ids[0], article_ids[i])

    # Build cluster groups
    cluster_map: dict[str, list] = {}
    for m in all_meta:
        root = find(m["id"])
        if root not in cluster_map:
            cluster_map[root] = []
        cluster_map[root].append(m)

    # Build cluster metadata
    clusters = []
    for root_id, members in cluster_map.items():
        if len(members) < min_size:
            continue
        # Representative tags for the cluster (most frequent shared tags)
        tag_freq: dict[str, int] = {}
        for m in members:
            for t in m["tags"]:
                tag_freq[t] = tag_freq.get(t, 0) + 1
        top_tags = sorted(tag_freq.items(), key=lambda x: -x[1])[:5]
        # Representative category
        cat_freq: dict[str, int] = {}
        for m in members:
            cat = m["category"].split("/")[0]
            cat_freq[cat] = cat_freq.get(cat, 0) + 1
        main_cat = max(cat_freq, key=cat_freq.get)

        avg_conf = round(sum(m["confidence"] for m in members) / len(members), 2)
        clusters.append({
            "cluster_id": root_id,
            "size": len(members),
            "main_category": main_cat,
            "top_tags": [{"tag": t, "count": c} for t, c in top_tags],
            "avg_confidence": avg_conf,
            "articles": [{"id": m["id"], "title": m["title"], "category": m["category"]}
                         for m in sorted(members, key=lambda x: x["title"])[:10]],
        })

    clusters.sort(key=lambda x: -x["size"])
    clusters = clusters[:limit]

    append_log("cluster", details=f"clusters={len(clusters)} total_articles={len(all_meta)}")
    output_json({
        "status": "ok",
        "total_articles": len(all_meta),
        "total_clusters": len(clusters),
        "min_size": min_size,
        "clusters": clusters,
    })


# ── Helpers ──────────────────────────────────────

def _vector_upsert(article) -> None:
    """Add or update a document in the required vector index."""
    import io, contextlib
    vidx = None
    try:
        from ai_wiki.vector import VectorIndex
        with contextlib.redirect_stderr(io.StringIO()), \
             contextlib.redirect_stdout(io.StringIO()):
            vidx = VectorIndex()
            vidx.upsert(article)
    finally:
        if vidx is not None:
            vidx.close()


def _vector_remove(article_id: str) -> None:
    """Remove a document from the required vector index."""
    vidx = None
    try:
        from ai_wiki.vector import VectorIndex
        vidx = VectorIndex()
        vidx.remove(article_id)
    finally:
        if vidx is not None:
            vidx.close()


def _add_reverse_link(idx: WikiIndex, target_id: str, source_id: str) -> None:
    """Add source_id as a related entry to the target_id document (bidirectional)."""
    target, target_path = load_article_with_path(target_id)
    if target and source_id not in target.related:
        target.related.append(source_id)
        target.last_modified = datetime.now(timezone.utc)
        try:
            atomic_update(target, target_path, idx)
        except Exception as e:
            logger.debug("Failed to add reverse link (%s → %s): %s", source_id, target_id, e)


def _sanitize_surrogates(text: str) -> str:
    """Remove surrogate characters (\\udcXX etc.). Handles lone surrogates caused by Windows cp949↔utf-8 mismatch."""
    return text.encode("utf-8", errors="replace").decode("utf-8", errors="replace")


def _read_yaml_content(content_file: str | None, content_stdin: bool) -> dict | None:
    """Read YAML content from file or stdin and return as a dict."""
    raw = None
    if content_file:
        # errors='surrogateescape': absorb OS-level bytes then sanitize explicitly
        try:
            with open(content_file, "r", encoding="utf-8", errors="surrogateescape") as f:
                raw = f.read()
        except (OSError, UnicodeDecodeError):
            # fallback: read as binary and decode with replace
            with open(content_file, "rb") as fb:
                raw = fb.read().decode("utf-8", errors="replace")
        raw = _sanitize_surrogates(raw)
    elif content_stdin:
        if hasattr(sys.stdin, "buffer"):
            raw = sys.stdin.buffer.read().decode("utf-8", errors="replace")
        else:
            raw = sys.stdin.read()
        raw = _sanitize_surrogates(raw)

    if raw is None:
        return None

    # Pre-process: strip BOM and convert tabs to spaces
    raw = raw.lstrip("\ufeff")
    if "\t" in raw:
        raw = raw.replace("\t", "  ")

    try:
        data = load_yaml_text(raw)
        if isinstance(data, dict):
            return data
        return {"text": raw}
    except yaml.YAMLError as e:
        # Notify user of parse failure
        output_json({
            "status": "error",
            "code": "yaml_parse_error",
            "message": f"YAML parse error: {e}",
            "hint": "Check YAML syntax (no tabs for indentation, space required after colon)",
        })
        sys.exit(1)







def _article_to_md(article) -> str:
    """Convert an Article to a Markdown string."""
    lines = [f"# {article.title}", ""]
    lines.append(f"**Category:** {article.category}  ")
    lines.append(f"**Confidence:** {article.confidence}  ")
    lines.append(f"**Tags:** {', '.join(article.tags)}  ")
    if article.sources:
        lines.append(f"**Sources:** {', '.join(article.sources)}  ")
    lines.append("")

    content = article.content
    if isinstance(content, dict):
        for key, value in content.items():
            if key.startswith("_"):
                continue
            if key == "type":
                continue
            lines.append(f"## {key}")
            lines.append(_value_to_md(value))
            lines.append("")
    return "\n".join(lines)


def _value_to_md(value, indent: int = 0) -> str:
    """Convert a value to Markdown format."""
    prefix = "  " * indent
    if isinstance(value, dict):
        parts = []
        for k, v in value.items():
            if isinstance(v, (list, dict)):
                parts.append(f"{prefix}**{k}**:")
                parts.append(_value_to_md(v, indent + 1))
            else:
                parts.append(f"{prefix}**{k}**: {v}")
        return "\n".join(parts)
    elif isinstance(value, list):
        parts = []
        for item in value:
            if isinstance(item, dict):
                parts.append(f"{prefix}-")
                parts.append(_value_to_md(item, indent + 1))
            else:
                parts.append(f"{prefix}- {item}")
        return "\n".join(parts)
    else:
        return f"{prefix}{value}"


@cli.command()
@click.argument("article_id")
@click.option("--format", "fmt", default="md",
              type=click.Choice(["md", "yaml"]), help="Output format")
@click.option("--output", "-o", default=None, type=click.Path(), help="Output file path (stdout if not specified)")
@click.pass_context
def export(ctx, article_id, fmt, output):
    """Export a document as Markdown or YAML."""
    article = load_article(article_id)
    if not article:
        output_error(msg("not_found", article_id), "not_found")

    if fmt == "md":
        text = _article_to_md(article)
    else:
        text = yaml.dump(article.to_dict(), allow_unicode=True, default_flow_style=False, sort_keys=False)

    if output:
        Path(output).write_text(text, encoding="utf-8")
        output_json({
            "status": "ok",
            "action": "exported",
            "article_id": article_id,
            "format": fmt,
            "output": output,
        })
    else:
        click.echo(text)


@cli.command("export-all")
@click.option("--format", "fmt", default="md",
              type=click.Choice(["md", "yaml"]), help="Output format")
@click.option("--output-dir", "-d", default=".", type=click.Path(), help="Output directory")
@click.pass_context
def export_all(ctx, fmt, output_dir):
    """Export all documents as Markdown or YAML."""
    articles = list_all_articles()
    out_dir = Path(output_dir)
    out_dir.mkdir(parents=True, exist_ok=True)

    exported = []
    for article in articles:
        if fmt == "md":
            text = _article_to_md(article)
            filename = f"{article.id}.md"
        else:
            text = yaml.dump(article.to_dict(), allow_unicode=True, default_flow_style=False, sort_keys=False)
            filename = f"{article.id}.yaml"
        file_path = out_dir / filename
        file_path.write_text(text, encoding="utf-8")
        exported.append(str(file_path))

    output_json({
        "status": "ok",
        "action": "exported_all",
        "format": fmt,
        "output_dir": str(out_dir),
        "exported_count": len(exported),
        "files": exported,
    })


@cli.command()
@click.argument("path", required=False, default=".", type=click.Path())
@click.option("--confirm", is_flag=True, default=False,
              help="Skip confirmation prompt (for CI/automation)")
@click.pass_context
def destroy(ctx, path, confirm):
    """Destroy a wiki: remove skill files, env var, and the wiki directory."""
    import platform
    import shutil

    wiki_root = Path(path).resolve()

    # 1. Check if wiki exists (data/wiki.db present)
    db_path = wiki_root / "data" / "wiki.db"
    if not db_path.exists():
        click.echo(msg("no_wiki_found", wiki_root))
        sys.exit(1)

    # 2. Confirmation prompt (skipped with --confirm)
    if not confirm:
        click.echo(msg("destroy_wiki_path", wiki_root))
        answer = click.prompt(
            msg("destroy_confirm_prompt"),
            default="N",
        ).strip().lower()
        if answer != "y":
            click.echo(msg("destroy_aborted"))
            return

    # 3. Read name and env_var from .ai-wiki.yaml
    config_path = wiki_root / CONFIG_FILENAME
    wiki_name = None
    env_var_name = None
    destroy_agents: list[str] = []
    if config_path.exists():
        try:
            with open(config_path, "r", encoding="utf-8") as _cfg:
                _config = yaml.safe_load(_cfg)
            wiki_name = _config.get("name")
            env_var_name = _config.get("env_var")
            _agents_raw = _config.get("agents")
            if isinstance(_agents_raw, list) and _agents_raw:
                destroy_agents = [a for a in _agents_raw if isinstance(a, str)]
        except Exception as _e:
            logger.warning("Could not read .ai-wiki.yaml: %s", _e)
    if not destroy_agents:
        destroy_agents = ["claude", "gemini", "codex"]  # backward compat: try all


    # 4. Remove environment variable (OS-specific)
    system = platform.system()
    if env_var_name:
        if system == "Windows":
            try:
                import subprocess as _sp
                _sp.run(
                    ["setx", env_var_name, ""],
                    capture_output=True, timeout=10,
                )
            except Exception as _e:
                logger.warning("Could not remove env var via setx: %s", _e)
        else:
            for _rc_file in ["~/.bashrc", "~/.zshrc", "~/.bash_profile"]:
                _rc_path = Path(_rc_file).expanduser()
                if not _rc_path.exists():
                    continue
                try:
                    _rc_lines = _rc_path.read_text(encoding="utf-8", errors="replace").splitlines()
                    _new_lines = []
                    _skip_next = False
                    for _line in _rc_lines:
                        if _line.strip() == "# AI Wiki":
                            _skip_next = True
                            continue
                        if _skip_next and env_var_name in _line:
                            _skip_next = False
                            continue
                        _skip_next = False
                        if env_var_name in _line:
                            continue
                        _new_lines.append(_line)
                    _rc_path.write_text("\n".join(_new_lines) + "\n", encoding="utf-8")
                except Exception as _e:
                    logger.warning("Could not update %s: %s", _rc_file, _e)

    # 5. Remove skill directories (based on agents list in .ai-wiki.yaml)
    if wiki_name:
        for _agent in destroy_agents:
            _path_fn = _AGENT_SKILL_PATHS.get(_agent)
            _path_fns = ([_path_fn] if _path_fn is not None else []) + list(
                _LEGACY_AGENT_SKILL_PATHS.get(_agent, ())
            )
            for _candidate_fn in _path_fns:
                _skill_dir = _candidate_fn(wiki_name)
                if _skill_dir.exists():
                    try:
                        shutil.rmtree(_skill_dir)
                    except Exception as _e:
                        logger.warning("Could not remove %s skill dir: %s", _agent, _e)

    # 8. Remove the entire wiki directory
    try:
        shutil.rmtree(wiki_root)
    except Exception as _e:
        click.echo(msg("destroy_dir_error", _e))
        sys.exit(1)

    click.echo(msg("destroy_done"))





# ── upgrade-skill ─────────────────────────────────
# ── upgrade-skill ─────────────────────────────────────────────────

@cli.command("upgrade-skill")
def upgrade_skill():
    """Install the latest skill files bundled with the package to each agent-specific path.
    
    Reads the agents list from .ai-wiki.yaml and updates only the relevant agent paths.
    If the agents field is absent, defaults to ['claude'] (backward compatible).
    """
    import os as _os

    templates_dir = _skill_templates_dir()

    if not templates_dir.exists():
        click.echo(msg("upgrade_skill_no_templates"), err=True)
        sys.exit(1)

    skill_files = list(templates_dir.glob("*.md"))
    if not skill_files:
        click.echo(msg("upgrade_skill_no_files"), err=True)
        sys.exit(1)

    # Read wiki name and agents from .ai-wiki.yaml
    wiki_root_env = _os.environ.get(ROOT_ENV_NAME, _os.environ.get("AI_WIKI_ROOT", "."))
    wiki_root = Path(wiki_root_env).resolve()
    wiki_name = wiki_root.name
    _cfg_path = wiki_root / CONFIG_FILENAME
    if _cfg_path.exists():
        try:
            import yaml as _yaml
            with open(_cfg_path, "r", encoding="utf-8") as _f:
                _cfg = _yaml.safe_load(_f)
            wiki_name = _cfg.get("name") or wiki_name
        except Exception:
            pass
    agents = _load_agents_from_config(wiki_root)

    # Version check
    pkg_ver = _get_package_skill_version()
    inst_ver = _get_installed_skill_version()
    if inst_ver:
        click.echo(msg("upgrade_skill_installed_version", inst_ver))
    else:
        click.echo(msg("upgrade_skill_not_installed"))
    if pkg_ver:
        click.echo(msg("upgrade_skill_package_version", pkg_ver))

    click.echo(msg("upgrade_skill_agents", ', '.join(agents)))

    # Copy skill files only to the selected agent paths
    _AGENT_DISPLAY = {
        "claude": "Claude Code",
        "gemini": "Gemini via Antigravity CLI",
        "codex": "GPT Codex",
    }
    for agent in agents:
        path_fn = _AGENT_SKILL_PATHS.get(agent)
        if path_fn is None:
            continue
        destinations = [path_fn(wiki_name)]
        if agent == "gemini":
            destinations.append(_LEGACY_AGENT_SKILL_PATHS["gemini"][0](wiki_name))
        for dest_dir in destinations:
            dest_dir.mkdir(parents=True, exist_ok=True)
            copied = []
            for src in skill_files:
                shutil.copy2(src, dest_dir / src.name)
                copied.append(src.name)
            label = _AGENT_DISPLAY.get(agent, agent)
            click.echo(msg("upgrade_skill_copied", len(copied), dest_dir, label))
            for fname in sorted(copied):
                click.echo(msg("upgrade_skill_file_ok", fname))

    version_str = pkg_ver or "unknown"
    click.echo(msg("upgrade_skill_done", version_str))
