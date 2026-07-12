import json
import py_compile
import subprocess
from pathlib import Path

import yaml
from click.testing import CliRunner

from ai_wiki.cli import cli
from ai_wiki.variant import VariantSpec, create_variant_package, install_variant_package, list_builtin_presets, load_builtin_preset


def _json_from_output(output: str) -> dict:
    start = output.find("{")
    assert start >= 0, output
    return json.loads(output[start:])


def test_variant_spec_defaults():
    spec = VariantSpec.build(package_name="law-wiki", display_name="Law Wiki", domain="law")

    assert spec.module_name == "law_wiki"
    assert spec.command_name == "law-wiki"
    assert spec.skill_name == "law-wiki"
    assert spec.env_prefix == "LAW_WIKI"
    assert spec.config_filename == ".law-wiki.yaml"


def test_builtin_presets_are_available():
    presets = {preset["name"]: preset for preset in list_builtin_presets()}

    assert {"general", "law", "labor", "tax", "corporate", "business", "research", "tech", "personal"} <= set(presets)
    assert presets["law"]["package_name"] == "law-wiki"
    assert "계약" in presets["law"]["triggers"]

    law = load_builtin_preset("law")
    assert law["display_name"] == "법률위키"
    assert law["domain"] == "law"


def test_variant_create_generates_independent_package(tmp_path):
    runner = CliRunner()
    result = runner.invoke(
        cli,
        [
            "variant",
            "create",
            "law-wiki",
            "--display-name",
            "Law Wiki",
            "--domain",
            "law",
            "--trigger",
            "contracts",
            "--trigger",
            "litigation",
            "--output-dir",
            str(tmp_path),
        ],
    )

    assert result.exit_code == 0, result.output
    data = _json_from_output(result.output)
    package_dir = Path(data["package_dir"])

    assert data["status"] == "ok"
    assert data["command_name"] == "law-wiki"
    assert (package_dir / "pyproject.toml").exists()
    assert (package_dir / "src" / "law_wiki" / "cli.py").exists()
    assert not (package_dir / "src" / "ai_wiki").exists()

    pyproject = (package_dir / "pyproject.toml").read_text(encoding="utf-8")
    assert 'name = "law-wiki"' in pyproject
    assert 'law-wiki = "law_wiki.cli:cli"' in pyproject
    assert 'law-wiki-web = "law_wiki.web:main"' in pyproject
    assert 'dependencies = ["ai-wiki>=0.5,<0.6"]' in pyproject
    assert '"variant.yaml"' in pyproject

    cli_text = (package_dir / "src" / "law_wiki" / "cli.py").read_text(encoding="utf-8")
    assert "from ai_wiki.runtime import activate_variant" in cli_text
    assert "from ai_wiki.cli import cli" in cli_text
    assert "'env_prefix': 'LAW_WIKI'" in cli_text
    assert len(cli_text.splitlines()) < 20
    assert not (package_dir / "src" / "law_wiki" / "storage.py").exists()
    assert not (package_dir / "src" / "law_wiki" / "templates").exists()

    skill_text = (package_dir / "src" / "law_wiki" / "skill_templates" / "SKILL.md").read_text(encoding="utf-8")
    assert "name: law-wiki" in skill_text
    assert "law-wiki context" in skill_text
    assert "contracts, litigation" in skill_text
    assert (package_dir / "src" / "law_wiki" / "variant.yaml").exists()

    manifest = yaml.safe_load((package_dir / "variant.yaml").read_text(encoding="utf-8"))
    assert manifest["package_name"] == "law-wiki"
    assert manifest["module_name"] == "law_wiki"
    assert manifest["triggers"] == ["contracts", "litigation"]

    for file_path in [
        package_dir / "src" / "law_wiki" / "cli.py",
        package_dir / "src" / "law_wiki" / "web.py",
    ]:
        py_compile.compile(str(file_path), doraise=True)


def test_variant_create_from_manifest(tmp_path):
    manifest_path = tmp_path / "tax.yaml"
    manifest_path.write_text(
        yaml.safe_dump(
            {
                "package_name": "tax-wiki",
                "display_name": "Tax Wiki",
                "domain": "tax",
                "triggers": ["vat", "income-tax"],
            },
            sort_keys=False,
        ),
        encoding="utf-8",
    )

    runner = CliRunner()
    result = runner.invoke(cli, ["variant", "create", "--manifest", str(manifest_path), "--output-dir", str(tmp_path)])

    assert result.exit_code == 0, result.output
    data = _json_from_output(result.output)
    package_dir = Path(data["package_dir"])

    assert (package_dir / "src" / "tax_wiki").exists()
    assert data["env_root"] == "TAX_WIKI_ROOT"
    assert data["config_file"] == ".tax-wiki.yaml"


def test_variant_create_from_preset(tmp_path):
    runner = CliRunner()
    result = runner.invoke(cli, ["variant", "create", "--preset", "law", "--output-dir", str(tmp_path)])

    assert result.exit_code == 0, result.output
    data = _json_from_output(result.output)
    package_dir = Path(data["package_dir"])

    assert data["package_name"] == "law-wiki"
    assert data["module_name"] == "law_wiki"
    assert (package_dir / "src" / "law_wiki" / "skill_templates" / "SKILL.md").exists()


def test_variant_create_from_preset_with_package_override(tmp_path):
    runner = CliRunner()
    result = runner.invoke(
        cli,
        [
            "variant",
            "create",
            "client-records-wiki",
            "--preset",
            "business",
            "--display-name",
            "Client Records Wiki",
            "--output-dir",
            str(tmp_path),
        ],
    )

    assert result.exit_code == 0, result.output
    data = _json_from_output(result.output)

    assert data["package_name"] == "client-records-wiki"
    assert data["module_name"] == "client_records_wiki"
    assert data["command_name"] == "client-records-wiki"


def test_variant_presets_cli():
    runner = CliRunner()
    result = runner.invoke(cli, ["variant", "presets"])

    assert result.exit_code == 0, result.output
    data = _json_from_output(result.output)

    assert data["status"] == "ok"
    assert data["count"] >= 9
    assert any(preset["name"] == "tax" for preset in data["presets"])


def test_variant_show_preset_cli():
    runner = CliRunner()
    result = runner.invoke(cli, ["variant", "show-preset", "labor"])

    assert result.exit_code == 0, result.output
    data = _json_from_output(result.output)

    assert data["manifest"]["package_name"] == "labor-wiki"
    assert "노무" in data["manifest"]["triggers"]


def test_variant_init_manifest_from_preset(tmp_path):
    output = tmp_path / "client-records.variant.yaml"
    runner = CliRunner()
    result = runner.invoke(
        cli,
        [
            "variant",
            "init-manifest",
            "client-records-wiki",
            "--preset",
            "business",
            "--display-name",
            "Client Records Wiki",
            "--output",
            str(output),
        ],
    )

    assert result.exit_code == 0, result.output
    data = _json_from_output(result.output)
    manifest = yaml.safe_load(output.read_text(encoding="utf-8"))

    assert data["action"] == "manifest_written"
    assert manifest["package_name"] == "client-records-wiki"
    assert manifest["module_name"] == "client_records_wiki"
    assert manifest["display_name"] == "Client Records Wiki"


def test_install_variant_package_runs_editable_pip(tmp_path, monkeypatch):
    package_dir = tmp_path / "law-wiki"
    package_dir.mkdir()
    (package_dir / "pyproject.toml").write_text("[project]\nname = \"law-wiki\"\n", encoding="utf-8")
    calls = []

    def fake_run(command, cwd, text, capture_output, check):
        calls.append(
            {
                "command": command,
                "cwd": cwd,
                "text": text,
                "capture_output": capture_output,
                "check": check,
            }
        )
        return subprocess.CompletedProcess(command, 0, stdout="installed\n", stderr="")

    monkeypatch.setattr("ai_wiki.variant.subprocess.run", fake_run)

    result = install_variant_package(package_dir, python_executable="python-test")

    assert result["installed"] is True
    assert calls == [
        {
            "command": ["python-test", "-m", "pip", "install", "-e", str(package_dir.resolve())],
            "cwd": str(package_dir.resolve()),
            "text": True,
            "capture_output": True,
            "check": False,
        }
    ]


def test_variant_create_install_option(tmp_path, monkeypatch):
    calls = []

    def fake_run(command, cwd, text, capture_output, check):
        calls.append(command)
        return subprocess.CompletedProcess(command, 0, stdout="installed\n", stderr="")

    monkeypatch.setattr("ai_wiki.variant.subprocess.run", fake_run)

    runner = CliRunner()
    result = runner.invoke(
        cli,
        [
            "variant",
            "create",
            "labor-wiki",
            "--display-name",
            "Labor Wiki",
            "--domain",
            "labor",
            "--output-dir",
            str(tmp_path),
            "--install",
            "--python",
            "python-test",
        ],
    )

    assert result.exit_code == 0, result.output
    data = _json_from_output(result.output)

    assert data["install"]["installed"] is True
    assert calls == ["python-test -m pip install -e".split() + [str((tmp_path / "labor-wiki").resolve())]]


def test_force_refuses_to_overwrite_initialized_wiki(tmp_path):
    spec = VariantSpec.build(package_name="protected-wiki")
    package_dir = tmp_path / "protected-wiki"
    (package_dir / "data").mkdir(parents=True)

    try:
        create_variant_package(spec, output_dir=tmp_path, force=True)
    except FileExistsError as exc:
        assert "refusing to overwrite initialized wiki" in str(exc)
    else:
        raise AssertionError("initialized wiki should not be overwritten")


def test_variant_install_cli_orchestrates_full_provision(tmp_path, monkeypatch):
    captured = {}

    def fake_provision(spec, **kwargs):
        captured["spec"] = spec
        captured.update(kwargs)
        return {"status": "ok", "action": "variant_installed", "package_name": spec.package_name}

    monkeypatch.setattr("ai_wiki.variant.provision_variant_package", fake_provision)
    result = CliRunner().invoke(
        cli,
        ["variant", "install", "legal-team-wiki", "--preset", "law", "--output-dir", str(tmp_path), "--agent", "codex", "--lang", "ko"],
    )

    assert result.exit_code == 0, result.output
    data = _json_from_output(result.output)
    assert data["action"] == "variant_installed"
    assert captured["spec"].domain == "law"
    assert captured["agents"] == ("codex",)
    assert captured["lang"] == "ko"
