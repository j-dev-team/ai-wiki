# Changelog

## 0.4.0 - 2026-07-12

- Add a stable AI JSON protocol with compact, full, and raw document views.
- Add token-budgeted hybrid context assembly with path-level citations and usage tracking.
- Add optimistic, validated RFC 6902 patching and autonomous JSON document creation.
- Exclude pending unverified drafts from normal context while preserving them for verification.
- Replace agent skill workflows with context-first retrieval and evidence-linked writeback.
- Add deterministic protocol benchmarks and live Codex, Claude, and Gemini acceptance evaluation.

## 0.3.0 - 2026-07-11

- Add canonical `schema_version: 2` documents with strict Pydantic validation and JSON Schema output.
- Normalize sources, relations, verification, history, and system metadata outside user content.
- Add duplicate-key and resource-bounded YAML loading.
- Add idempotent `migrate-schema` dry-run/apply workflow with backups and reports.
- Replace direct YAML writes with same-directory temporary files, `fsync`, and `os.replace`.
- Keep v1 documents and legacy API metadata readable during the compatibility window.

## 0.2.1 - 2026-07-11

- Added manifest-driven purpose-specific wiki presets and thin shared-engine packages.
- Added one-command install, lifecycle backup/restore/upgrade, isolation audits, and skill routing checks.
- Added a Korean end-to-end user guide for upgrades, vector search, variants, skills, backup, restore, and troubleshooting.
- Replaced instance-specific skill routing with manifest-driven routing for every dedicated wiki.
- Added a documented, guarded PowerShell workflow for repeatable GitHub and PyPI releases.
