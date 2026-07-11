from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Mapping


@dataclass(frozen=True)
class RuntimeConfig:
    command_name: str = "ai-wiki"
    display_name: str = "AI Wiki"
    domain: str = "general"
    skill_name: str = "ai-wiki"
    env_prefix: str = "AI_WIKI"
    config_filename: str = ".ai-wiki.yaml"
    web_port: int = 5000
    default_preset: str | None = None
    skill_template_dir: Path | None = None

    @property
    def root_env_name(self) -> str:
        return f"{self.env_prefix}_ROOT"


def get_runtime() -> RuntimeConfig:
    template_dir = os.environ.get("AI_WIKI_SKILL_TEMPLATE_DIR")
    return RuntimeConfig(
        command_name=os.environ.get("AI_WIKI_COMMAND_NAME", "ai-wiki"),
        display_name=os.environ.get("AI_WIKI_DISPLAY_NAME", "AI Wiki"),
        domain=os.environ.get("AI_WIKI_DOMAIN", "general"),
        skill_name=os.environ.get("AI_WIKI_SKILL_NAME", "ai-wiki"),
        env_prefix=os.environ.get("AI_WIKI_ENV_PREFIX", "AI_WIKI"),
        config_filename=os.environ.get("AI_WIKI_CONFIG_FILENAME", ".ai-wiki.yaml"),
        web_port=int(os.environ.get("AI_WIKI_WEB_PORT", "5000")),
        default_preset=os.environ.get("AI_WIKI_DEFAULT_PRESET") or None,
        skill_template_dir=Path(template_dir) if template_dir else None,
    )


def activate_variant(
    spec: Mapping[str, Any],
    *,
    skill_template_dir: str | Path | None = None,
    default_root: str | Path | None = None,
) -> RuntimeConfig:
    required = {
        "command_name",
        "display_name",
        "domain",
        "skill_name",
        "env_prefix",
        "config_filename",
    }
    missing = sorted(key for key in required if not spec.get(key))
    if missing:
        raise ValueError(f"variant runtime fields are missing: {', '.join(missing)}")

    values = {
        "AI_WIKI_COMMAND_NAME": str(spec["command_name"]),
        "AI_WIKI_DISPLAY_NAME": str(spec["display_name"]),
        "AI_WIKI_DOMAIN": str(spec["domain"]),
        "AI_WIKI_SKILL_NAME": str(spec["skill_name"]),
        "AI_WIKI_ENV_PREFIX": str(spec["env_prefix"]),
        "AI_WIKI_CONFIG_FILENAME": str(spec["config_filename"]),
        "AI_WIKI_WEB_PORT": str(spec.get("web_port", 5000)),
        "AI_WIKI_DEFAULT_PRESET": str(spec["domain"]),
        "AI_WIKI_VARIANT": "1",
    }
    for key, value in values.items():
        os.environ[key] = value

    if skill_template_dir is not None:
        os.environ["AI_WIKI_SKILL_TEMPLATE_DIR"] = str(Path(skill_template_dir).resolve())

    root_env_name = f"{spec['env_prefix']}_ROOT"
    fallback_root = Path.cwd().resolve()
    if default_root is not None:
        candidate = Path(default_root).resolve()
        if (candidate / "variant.yaml").exists():
            fallback_root = candidate
    os.environ["AI_WIKI_ROOT"] = os.environ.get(root_env_name, str(fallback_root))
    return get_runtime()
