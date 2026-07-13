"""destroy 명령 및 init 테스트 - 스킬 잔여물 방지."""
import json
import os
from pathlib import Path

import pytest
import yaml
from click.testing import CliRunner

import ai_wiki.cli as cli_module
from ai_wiki.cli import cli
from ai_wiki.index import WikiIndex


def _make_wiki(path: Path) -> None:
    """임시 디렉토리에 최소한의 위키 구조 생성."""
    (path / "articles").mkdir(parents=True, exist_ok=True)
    (path / "data").mkdir(parents=True, exist_ok=True)
    (path / "logs").mkdir(parents=True, exist_ok=True)
    idx = WikiIndex(path / "data" / "wiki.db")
    idx.close()
    del idx


def _make_wiki_with_config(
    path: Path,
    name: str = "testwiki",
    env_var: str = "TESTWIKI_ROOT",
) -> None:
    """config(.ai-wiki.yaml) 포함 위키 생성."""
    _make_wiki(path)
    config = {
        "version": "1.0",
        "name": name,
        "env_var": env_var,
        "preset": "general",
        "lang": "en",
    }
    with open(path / ".ai-wiki.yaml", "w", encoding="utf-8") as f:
        yaml.dump(config, f)


# ── destroy 기본 동작 ─────────────────────────────


def test_destroy_with_confirm_flag(tmp_path):
    """--confirm 플래그로 프롬프트 생략 후 위키 폴더 삭제."""
    _make_wiki_with_config(tmp_path, name="mywiki", env_var="MYWIKI_ROOT")

    os.environ["AI_WIKI_ROOT"] = str(tmp_path)
    runner = CliRunner()
    result = runner.invoke(cli, ["destroy", str(tmp_path), "--confirm"])

    assert result.exit_code == 0, f"Output: {result.output}"
    assert "Wiki destroyed" in result.output
    assert not tmp_path.exists()


def test_destroy_interactive_yes(tmp_path):
    """대화형 프롬프트에서 'y' 입력 시 삭제."""
    _make_wiki_with_config(tmp_path, name="mywiki2", env_var="MYWIKI2_ROOT")

    os.environ["AI_WIKI_ROOT"] = str(tmp_path)
    runner = CliRunner()
    result = runner.invoke(cli, ["destroy", str(tmp_path)], input="y\n")

    assert result.exit_code == 0, f"Output: {result.output}"
    assert "Wiki destroyed" in result.output
    assert not tmp_path.exists()


def test_destroy_interactive_no(tmp_path):
    """대화형 프롬프트에서 'N' 입력 시 중단."""
    _make_wiki_with_config(tmp_path, name="mywiki3", env_var="MYWIKI3_ROOT")

    os.environ["AI_WIKI_ROOT"] = str(tmp_path)
    runner = CliRunner()
    result = runner.invoke(cli, ["destroy", str(tmp_path)], input="N\n")

    assert result.exit_code == 0
    assert "Aborted" in result.output
    assert tmp_path.exists()  # 삭제되지 않았어야 함


def test_destroy_no_wiki(tmp_path):
    """wiki.db 없는 폴더에서 destroy 실행 시 에러."""
    os.environ["AI_WIKI_ROOT"] = str(tmp_path)
    runner = CliRunner()
    result = runner.invoke(cli, ["destroy", str(tmp_path), "--confirm"])

    # SystemExit(1)이 발생했을 때 CliRunner는 exit_code=1 반환
    assert result.exit_code != 0 or "Error" in result.output, (
        f"Expected error. exit_code={result.exit_code}, output={result.output}"
    )
    assert "No wiki found" in result.output


def test_destroy_removes_gemini_skill(tmp_path):
    '''gemini skill folder deletion check.'''
    _make_wiki_with_config(tmp_path, name="skillwiki", env_var="SKILLWIKI_ROOT")

    skill_dir = Path.home() / ".gemini" / "config" / "skills" / "skillwiki"
    legacy_skill_dir = Path.home() / ".agents" / "skills" / "skillwiki"
    skill_dir.mkdir(parents=True, exist_ok=True)
    legacy_skill_dir.mkdir(parents=True, exist_ok=True)
    (skill_dir / "SKILL.md").write_text("# test skill", encoding="utf-8")
    (legacy_skill_dir / "SKILL.md").write_text("# legacy test skill", encoding="utf-8")

    try:
        os.environ["AI_WIKI_ROOT"] = str(tmp_path)
        runner = CliRunner()
        result = runner.invoke(cli, ["destroy", str(tmp_path), "--confirm"])

        assert result.exit_code == 0, f"Output: {result.output}"
        assert not skill_dir.exists(), "gemini skill dir should be removed"
        assert not legacy_skill_dir.exists(), "legacy gemini skill dir should be removed"
    finally:
        import shutil
        for candidate in (skill_dir, legacy_skill_dir):
            if candidate.exists():
                shutil.rmtree(candidate)
def test_destroy_removes_claude_skill(tmp_path):
    """claude 스킬 폴더도 함께 삭제되는지 확인."""
    import shutil as _shutil

    _make_wiki_with_config(tmp_path, name="claudewiki", env_var="CLAUDEWIKI_ROOT")

    # 가짜 claude 스킬 폴더 생성
    claude_skill_dir = Path.home() / ".claude" / "skills" / "claudewiki"
    claude_skill_dir.mkdir(parents=True, exist_ok=True)
    (claude_skill_dir / "SKILL.md").write_text("# test claude skill", encoding="utf-8")

    try:
        os.environ["AI_WIKI_ROOT"] = str(tmp_path)
        runner = CliRunner()
        result = runner.invoke(cli, ["destroy", str(tmp_path), "--confirm"])

        assert result.exit_code == 0, f"Output: {result.output}"
        assert not claude_skill_dir.exists(), "claude skill dir should be removed"
    finally:
        # 혹시 남아있으면 정리
        if claude_skill_dir.exists():
            _shutil.rmtree(claude_skill_dir)


def test_destroy_removes_all_skill_dirs(tmp_path):
    '''claude, gemini, codex skill folder deletion check.'''
    import shutil as _shutil

    _make_wiki_with_config(tmp_path, name="bothwiki", env_var="BOTHWIKI_ROOT")

    claude_skill_dir = Path.home() / ".claude" / "skills" / "bothwiki"
    gemini_skill_dir = Path.home() / ".gemini" / "config" / "skills" / "bothwiki"
    codex_skill_dir = Path.home() / ".codex" / "skills" / "bothwiki"

    claude_skill_dir.mkdir(parents=True, exist_ok=True)
    (claude_skill_dir / "SKILL.md").write_text("# claude skill", encoding="utf-8")
    gemini_skill_dir.mkdir(parents=True, exist_ok=True)
    (gemini_skill_dir / "SKILL.md").write_text("# gemini skill", encoding="utf-8")
    codex_skill_dir.mkdir(parents=True, exist_ok=True)
    (codex_skill_dir / "SKILL.md").write_text("# codex skill", encoding="utf-8")

    try:
        os.environ["AI_WIKI_ROOT"] = str(tmp_path)
        runner = CliRunner()
        result = runner.invoke(cli, ["destroy", str(tmp_path), "--confirm"])

        assert result.exit_code == 0, f"Output: {result.output}"
        assert not claude_skill_dir.exists(), "claude skill dir should be removed"
        assert not gemini_skill_dir.exists(), "gemini skill dir should be removed"
        assert not codex_skill_dir.exists(), "codex skill dir should be removed"
    finally:
        for d in (claude_skill_dir, gemini_skill_dir, codex_skill_dir):
            if d.exists():
                _shutil.rmtree(d)
def test_destroy_no_config(tmp_path):
    """config(.ai-wiki.yaml) 없이도 폴더 삭제 가능."""
    _make_wiki(tmp_path)  # config 없이 wiki.db만 있는 위키

    os.environ["AI_WIKI_ROOT"] = str(tmp_path)
    runner = CliRunner()
    result = runner.invoke(cli, ["destroy", str(tmp_path), "--confirm"])

    assert result.exit_code == 0, f"Output: {result.output}"
    assert "Wiki destroyed" in result.output
    assert not tmp_path.exists()


# ── init 명령 테스트 (스킬 잔여물 방지) ───────────────────────────


def test_init_creates_wiki_structure(tmp_path):
    """init 명령이 위키 디렉토리 구조를 올바르게 생성하는지 확인."""
    # tmp_path를 사용하므로 실제 위키 디렉토리에 영향 없음
    new_wiki_dir = tmp_path / "mywiki"
    new_wiki_dir.mkdir()

    os.environ["AI_WIKI_ROOT"] = str(tmp_path)
    runner = CliRunner()
    # 대화형 입력: 언어(1=English), 위키이름(Enter=기본값), 에이전트(1=Claude), 프리셋(1=general)
    result = runner.invoke(cli, ["init", str(new_wiki_dir)], input="1\n\n1\n1\n")

    assert result.exit_code == 0, f"Output: {result.output}"
    json_start = result.output.rfind("{")
    data = json.loads(result.output[json_start:])
    assert data["status"] == "ok"
    assert data["action"] == "initialized"

    # 디렉토리 구조 확인
    assert (new_wiki_dir / "articles").exists()
    assert (new_wiki_dir / "data").exists()
    assert (new_wiki_dir / "logs").exists()
    assert (new_wiki_dir / "data" / "wiki.db").exists()
    assert (new_wiki_dir / ".ai-wiki.yaml").exists()
    seed_files = list((new_wiki_dir / "articles").rglob("*.yaml"))
    assert len(seed_files) == 1
    seed_document = yaml.safe_load(seed_files[0].read_text(encoding="utf-8"))
    assert seed_document["schema_version"] == 2
    assert "Self-Reference" in seed_document["title"]
    assert not (tmp_path / "articles").exists()


def test_init_creates_skill_dirs(tmp_path, monkeypatch):
    '''init creates skill dirs for selected agents.

    Selects all 3 agents (1,2,3) to test all skill dirs are created.
    Only created when skill_templates directory has .md files.
    Cleanup skill dirs after test.
    '''
    fake_skills = tmp_path / "agent-skills"
    wiki_name = "ai-wiki"
    monkeypatch.setitem(cli_module._AGENT_SKILL_PATHS, "claude", lambda name: fake_skills / "claude" / name)
    monkeypatch.setitem(cli_module._AGENT_SKILL_PATHS, "gemini", lambda name: fake_skills / "gemini" / name)
    monkeypatch.setitem(cli_module._AGENT_SKILL_PATHS, "codex", lambda name: fake_skills / "codex" / name)
    monkeypatch.setitem(cli_module._LEGACY_AGENT_SKILL_PATHS, "gemini", (lambda name: fake_skills / "agents" / name,))
    claude_skill_dir = fake_skills / "claude" / wiki_name
    gemini_skill_dir = fake_skills / "gemini" / wiki_name
    gemini_compat_skill_dir = fake_skills / "agents" / wiki_name
    codex_skill_dir = fake_skills / "codex" / wiki_name

    assert not claude_skill_dir.exists(), f"{claude_skill_dir} should not exist before test"
    assert not gemini_skill_dir.exists(), f"{gemini_skill_dir} should not exist before test"
    assert not gemini_compat_skill_dir.exists(), f"{gemini_compat_skill_dir} should not exist before test"
    assert not codex_skill_dir.exists(), f"{codex_skill_dir} should not exist before test"

    os.environ["AI_WIKI_ROOT"] = str(tmp_path)
    runner = CliRunner()
    # 대화형 입력: 언어(1=English), 위키이름(Enter=기본값), 에이전트(1,2,3=모두), 프리셋(1=general)
    result = runner.invoke(cli, ["init", str(tmp_path)], input="1\n\n1,2,3\n1\n")

    try:
        assert result.exit_code == 0, f"Output: {result.output}"

        from pathlib import Path as _P
        skill_templates = _P(__file__).parent.parent / "src" / "ai_wiki" / "skill_templates"
        has_templates = skill_templates.exists() and bool(list(skill_templates.glob("*.md")))

        if has_templates:
            assert claude_skill_dir.exists(), f"claude skill dir should be created: {claude_skill_dir}"
            assert gemini_skill_dir.exists(), f"gemini skill dir should be created: {gemini_skill_dir}"
            assert gemini_compat_skill_dir.exists(), f"gemini compatibility skill dir should be created: {gemini_compat_skill_dir}"
            assert (gemini_skill_dir / "SKILL.md").read_bytes() == (gemini_compat_skill_dir / "SKILL.md").read_bytes()
            assert codex_skill_dir.exists(), f"codex skill dir should be created: {codex_skill_dir}"
    finally:
        pass
def test_init_already_initialized(tmp_path):
    """이미 초기화된 위키에 init 재실행 시 already_initialized 반환."""
    _make_wiki_with_config(tmp_path, name="existing", env_var="EXISTING_ROOT")

    os.environ["AI_WIKI_ROOT"] = str(tmp_path)
    runner = CliRunner()
    result = runner.invoke(cli, ["init", str(tmp_path)])

    assert result.exit_code == 0, f"Output: {result.output}"
    data = json.loads(result.output)
    assert data["action"] == "already_initialized"
