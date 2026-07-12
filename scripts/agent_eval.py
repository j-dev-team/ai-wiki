"""Live Codex and Gemini acceptance gate for the AI-first protocol.

Claude remains available through ``--agents claude`` when credentials exist,
but is not part of the default release gate.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
import shutil
import sqlite3
import subprocess
import sys
from pathlib import Path

import yaml

REPO_ROOT = Path(__file__).resolve().parents[1]
SRC_DIR = REPO_ROOT / "src"
if str(SRC_DIR) not in sys.path:
    sys.path.insert(0, str(SRC_DIR))
MAIN_ID = "tech-live-agent-eval-abc123"
TASKS = list(range(1, 13))


def _seed(root: Path) -> tuple[Path, str]:
    from ai_wiki.index import WikiIndex
    from ai_wiki.models import Article
    from ai_wiki.storage import get_relative_path, save_article

    for name in ("articles", "data", "logs"):
        (root / name).mkdir(parents=True, exist_ok=True)
    previous = os.environ.get("AI_WIKI_ROOT")
    os.environ["AI_WIKI_ROOT"] = str(root)
    try:
        article = Article(
            id=MAIN_ID,
            title="Live Agent Protocol Subject",
            category="technology/agents",
            tags=["agent", "evaluation"],
            confidence=0.95,
            sources=["https://example.com/live-agent-protocol"],
            author="evaluation-harness",
            content={
                "type": "technology",
                "what": "quantumwidget is a synthetic subject for live AI Wiki protocol evaluation.",
                "facts": [
                    "quantumwidget context must contain an evidence-linked citation.",
                    "AI writes require optimistic version checks and schema validation.",
                    "Pending source-free drafts are excluded from default context retrieval.",
                ],
                "use_cases": ["Context retrieval", "Citation tracking", "Safe patching"],
                "limitations": ["Synthetic evaluation only", "No real customer data"],
                "best_practices": ["Run context first", "Record citation use"],
            },
            verification=[{
                "path": "/content/data/facts", "level": "verified", "source_ids": ["src-1"],
            }],
        )
        path = save_article(article)
        index = WikiIndex(root / "data" / "wiki.db")
        index.upsert(article, get_relative_path(path))
        index.close()

        legacy = {
            "id": "tech-untouched-legacy-def456",
            "title": "Untouched Legacy Control",
            "category": "technology/agents",
            "tags": ["legacy", "control"],
            "confidence": 0.8,
            "version": 1,
            "created_at": "2026-01-01T00:00:00Z",
            "last_modified": "2026-01-01T00:00:00Z",
            "last_verified": "2026-01-01T00:00:00Z",
            "sources": ["https://example.com/legacy-control"],
            "related": [],
            "author": "evaluation-harness",
            "content": {
                "type": "technology", "what": "Legacy control document",
                "facts": ["This file must remain byte-identical throughout live evaluation."],
                "use_cases": ["Migration control"], "limitations": ["Synthetic"],
                "best_practices": ["Do not modify"],
            },
        }
        legacy_path = root / "articles" / "untouched-legacy.yaml"
        legacy_path.write_text(yaml.safe_dump(legacy, sort_keys=False), encoding="utf-8")
        return legacy_path, hashlib.sha256(legacy_path.read_bytes()).hexdigest()
    finally:
        if previous is None:
            os.environ.pop("AI_WIKI_ROOT", None)
        else:
            os.environ["AI_WIKI_ROOT"] = previous


def _write_inputs(root: Path) -> None:
    (root / "patch.json").write_text(json.dumps([
        {"op": "test", "path": "/metadata/document_version", "value": 1},
        {"op": "add", "path": "/content/data/facts/-",
         "value": "Live agent evaluation patch was applied safely."},
    ]), encoding="utf-8")
    long_text = "This synthetic draft captures reusable agent protocol knowledge. " * 6
    (root / "draft.json").write_text(json.dumps({
        "title": "Live Pending Draft",
        "category": "technology/agents",
        "tags": ["agent", "pending"],
        "confidence": 0.9,
        "content": {
            "type": "technology", "what": long_text,
            "facts": [long_text, "The draft intentionally has no external source."],
            "use_cases": ["Pending verification", "Autonomous writeback"],
            "limitations": ["Unverified synthetic knowledge", "Evaluation only"],
            "best_practices": ["Exclude from default context", "Verify before promotion"],
        },
    }), encoding="utf-8")


def _write_wrapper(root: Path, executable: str) -> Path:
    bin_dir = root / "eval-bin"
    bin_dir.mkdir()
    log_path = root / "commands.log"
    if os.name == "nt":
        wrapper = bin_dir / "ai-wiki.cmd"
        wrapper.write_text(
            "@echo off\r\n"
            f"echo %*>>\"{log_path}\"\r\n"
            f"\"{executable}\" %*\r\n"
            "exit /b %ERRORLEVEL%\r\n",
            encoding="utf-8",
        )
    else:
        wrapper = bin_dir / "ai-wiki"
        wrapper.write_text(
            "#!/bin/sh\n"
            f"printf '%s\\n' \"$*\" >> {json.dumps(str(log_path))}\n"
            f"exec {json.dumps(executable)} \"$@\"\n",
            encoding="utf-8",
        )
        wrapper.chmod(0o755)
    return bin_dir


def _schema() -> dict:
    return {
        "type": "object",
        "properties": {
            "passed_tasks": {"type": "array", "items": {"type": "integer"}},
            "citation": {"type": "string"},
            "notes": {"type": "array", "items": {"type": "string"}},
        },
        "required": ["passed_tasks", "citation", "notes"],
        "additionalProperties": False,
    }


def _prompt() -> str:
    return f"""You are running a live acceptance test in an isolated synthetic wiki.
Use only the ai-wiki CLI and the provided patch.json and draft.json files. Do not edit YAML directly.
Execute every task even when an expected command returns a non-zero conflict response.

1. Run ai-wiki capabilities.
2. Run ai-wiki context "quantumwidget" --max-tokens 1200 and retain context_id and one citation.
3. Run ai-wiki get {MAIN_ID} and verify compact content.
4. Run ai-wiki get {MAIN_ID} --view full.
5. Run ai-wiki get {MAIN_ID} --view raw.
6. Run ai-wiki get {MAIN_ID} --fields id,title,content.facts,sources.
7. Record the retained citation with record-use and outcome answered.
8. Dry-run patch.json against {MAIN_ID} with if-version 1.
9. Apply the same patch for real with if-version 1.
10. Apply it once more with stale if-version 1 and verify version_conflict.
11. Create draft.json through --document-file and verify it becomes pending with confidence <= 0.5.
12. Context-search "Live Pending Draft" without and then with --include-unverified; verify exclusion then inclusion.

Return only a JSON object matching the supplied schema. passed_tasks must contain every integer 1 through 12
only when you actually executed and verified every task. citation must be the real citation key used in task 7.
"""


def _extract_result(agent: str, stdout: str, final_file: Path) -> dict:
    text = final_file.read_text(encoding="utf-8") if final_file.exists() else stdout
    try:
        parsed = json.loads(text)
    except json.JSONDecodeError:
        start, end = text.find("{"), text.rfind("}")
        if start < 0 or end <= start:
            return {}
        parsed = json.loads(text[start:end + 1])
    if agent in {"claude", "gemini"} and isinstance(parsed, dict):
        nested = parsed.get("result") or parsed.get("response")
        if isinstance(nested, str):
            try:
                return json.loads(nested)
            except json.JSONDecodeError:
                start, end = nested.find("{"), nested.rfind("}")
                if start >= 0 and end > start:
                    return json.loads(nested[start:end + 1])
    return parsed if isinstance(parsed, dict) else {}


def _command(agent: str, root: Path, schema_path: Path, final_file: Path) -> list[str]:
    prompt = _prompt()
    executable_name = "agy" if agent == "gemini" else agent
    executable = shutil.which(executable_name) or shutil.which(f"{executable_name}.cmd")
    if agent == "gemini" and not executable:
        local_app_data = os.environ.get("LOCALAPPDATA")
        candidate = Path(local_app_data) / "agy" / "bin" / "agy.exe" if local_app_data else None
        executable = str(candidate) if candidate and candidate.exists() else None
    if not executable:
        raise FileNotFoundError(f"{executable_name} executable not found")
    if agent == "codex":
        return [executable, "exec", "--skip-git-repo-check", "--ephemeral",
                "--ignore-user-config", "--ignore-rules",
                "--dangerously-bypass-approvals-and-sandbox", "-C", str(root),
                "--output-schema", str(schema_path), "-o", str(final_file), prompt]
    if agent == "claude":
        return [executable, "-p", prompt, "--dangerously-skip-permissions",
                "--no-session-persistence", "--output-format", "json",
                "--json-schema", json.dumps(_schema()), "--max-budget-usd", "2.00"]
    if agent == "gemini":
        return [executable, "--print", prompt, "--dangerously-skip-permissions",
                "--print-timeout", "20m"]
    raise ValueError(agent)


def _grade(root: Path, legacy_path: Path, legacy_hash: str, result: dict) -> dict:
    command_log = (root / "commands.log").read_text(encoding="utf-8") if (root / "commands.log").exists() else ""
    checks = {
        "reported_all_tasks": sorted(result.get("passed_tasks", [])) == TASKS,
        "capabilities_called": "capabilities" in command_log,
        "context_called": command_log.count("context ") >= 3,
        "all_get_views_called": all(value in command_log for value in ("--view full", "--view raw", "--fields")),
        "usage_recorded": "record-use" in command_log,
        "patch_dry_and_apply_called": command_log.count("patch ") >= 3 and "--dry-run" in command_log,
        "create_called": "create --document-file" in command_log,
        "unverified_override_called": "--include-unverified" in command_log,
        "legacy_unchanged": legacy_path.exists() and hashlib.sha256(legacy_path.read_bytes()).hexdigest() == legacy_hash,
    }
    conn = sqlite3.connect(root / "data" / "wiki.db")
    try:
        version = conn.execute("SELECT version FROM articles_meta WHERE id = ?", (MAIN_ID,)).fetchone()
        usage_count = conn.execute("SELECT COUNT(*) FROM context_usage").fetchone()[0]
    finally:
        conn.close()
    checks["patch_applied_once"] = bool(version and version[0] == 2)
    checks["usage_persisted"] = usage_count >= 1

    draft_files = list((root / "articles").rglob("*.yaml"))
    pending = False
    for path in draft_files:
        data = yaml.safe_load(path.read_text(encoding="utf-8"))
        if data.get("title") == "Live Pending Draft":
            pending = (
                data.get("metadata", {}).get("confidence", 1) <= 0.5 and
                data.get("extensions", {}).get("system_metadata", {}).get("verification_status") == "pending"
            )
    checks["pending_draft_created"] = pending
    return {"passed": all(checks.values()), "checks": checks, "result": result}


def run_agent(agent: str, base: Path, timeout: int) -> dict:
    executable = shutil.which("ai-wiki")
    if not executable:
        return {"agent": agent, "passed": False, "error": "ai-wiki executable not found"}
    root = (base / agent).resolve()
    if root.exists():
        if base.resolve() not in root.parents:
            raise RuntimeError("refusing to clear eval directory outside base")
        shutil.rmtree(root)
    root.mkdir(parents=True)
    legacy_path, legacy_hash = _seed(root)
    _write_inputs(root)
    bin_dir = _write_wrapper(root, executable)
    schema_path = root / "result-schema.json"
    schema_path.write_text(json.dumps(_schema()), encoding="utf-8")
    final_file = root / "final.json"
    env = os.environ.copy()
    env["AI_WIKI_ROOT"] = str(root)
    env["PYTHONPATH"] = str(SRC_DIR) + os.pathsep + env.get("PYTHONPATH", "")
    env["PATH"] = str(bin_dir) + os.pathsep + env["PATH"]
    process = subprocess.run(
        _command(agent, root, schema_path, final_file), cwd=root, env=env,
        capture_output=True, text=True, encoding="utf-8", errors="replace", timeout=timeout,
    )
    (root / "agent.stdout.log").write_text(process.stdout, encoding="utf-8")
    (root / "agent.stderr.log").write_text(process.stderr, encoding="utf-8")
    result = _extract_result(agent, process.stdout, final_file)
    grade = _grade(root, legacy_path, legacy_hash, result)
    grade.update({"agent": agent, "exit_code": process.returncode, "root": str(root)})
    grade["passed"] = grade["passed"] and process.returncode == 0
    return grade


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--agents", default="codex,gemini")
    parser.add_argument("--output", default=str(REPO_ROOT / "tmp" / "agent-eval-report.json"))
    parser.add_argument("--timeout", type=int, default=900)
    args = parser.parse_args()
    agents = [item.strip() for item in args.agents.split(",") if item.strip()]
    base = REPO_ROOT / "tmp" / "agent-eval"
    base.mkdir(parents=True, exist_ok=True)
    reports = []
    for agent in agents:
        try:
            reports.append(run_agent(agent, base, args.timeout))
        except Exception as exc:
            reports.append({"agent": agent, "passed": False, "error": str(exc)})
    passed = sum(12 for report in reports if report.get("passed"))
    report = {
        "status": "ok" if passed == len(agents) * 12 else "error",
        "passed_tasks": passed,
        "total_tasks": len(agents) * 12,
        "agents": reports,
    }
    output = Path(args.output)
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps(report, ensure_ascii=False, indent=2))
    return 0 if report["status"] == "ok" else 1


if __name__ == "__main__":
    raise SystemExit(main())
