# Changelog

## 1.1.2 - 2026-07-13

- Add entity-first temporal authoring: canonical entity attributes, explicit event-to-event links, and source-backed event participants precede narrative prose.
- Add the opt-in `timeline_contract: entity_first` validator, which rejects timeline rows not bound to a known event and that event's participant IDs.
- Render bound timeline entities from their canonical graph names, so a concise narrative label cannot silently split one person into several people.
- Update AI Wiki skills with the required entity-first write workflow while keeping legacy temporal timelines readable.

## 1.1.1 - 2026-07-13

- Add compact Mission execution reads for progress, next-task, task-context, and criterion-evidence retrieval while preserving the full immutable ledger.
- Add entity-fidelity guidance to general and purpose-specific wiki skills so public, authorized, and user-provided entity attributes remain usable across records.
- Warn when anonymous or placeholder entity labels lack an explicit `identity_handling` record describing the scope and preserved attributes.

## 1.1.0 - 2026-07-13

- Add a shared `ko`/`en` authoring-language resolver across CLI, web, capabilities, and purpose-specific wiki runtimes.
- Add Mission source-language and revision-bound localization contracts while preserving legacy records and technical source text.
- Require readable, language-matched ResearchReport and WorkPlan prose and make Windows JSON stdin explicitly UTF-8 safe.
- Render ResearchReport scope, findings, recommendations, uncertainties, and bidirectional evidence links in Mission Control.
- Localize Mission list, detail, and API narratives with explicit source and fallback state.
- Present each WorkPlan and its WorkRun records as one logical Mission in the default overview while retaining immutable plan/run records and explicit run filters.
- Project the representative run's task, criterion, evidence, and handoff state onto the plan card and detail view.
- Add language, legacy, localization, Mission aggregation, accessibility, responsive, performance, package, and browser regression coverage.

## 1.0.0 - 2026-07-12

- Add schema-v3 temporal entities, claims, events, evidence, transitions, lazy v2 views, and time-aware queries.
- Add a Mission ledger with revision-pinned plans, independent approval, evidence review, transactional leases, resource locks, pause/resume, and knowledge candidates.
- Add independently labeled calibration runs with holdout gates, scoped promotion, cooldown, and rollback.
- Add principal, role, namespace, secret-reference, redaction, and authorization-audit policy enforcement.
- Add read-only Git, HTTP, Google Drive, Notion, and Slack connectors with immutable permission-bearing snapshots.
- Add plugin backend discovery and explicit degraded-state reporting.
- Add Mission Control, temporal, and calibration review pages plus optional Argon2id/AES-GCM team mode.
- Add protocol 1.5 contracts, a shared `AIWikiClient`, a Codex/Gemini Missions skill, and a release privacy gate.
- Preserve v1 and v2 documents without bulk migration; only temporal writes produce schema v3.

## 0.6.0 - 2026-07-12

- Replace one-vector-per-document retrieval with deterministic YAML-path chunks and exact chunk citations.
- Add Korean trigram chunk FTS, multilingual E5 embeddings, document-diverse hybrid fusion, and verified-evidence context packing.
- Add batched and incremental embedding, atomic full rebuilds, model/dimension migration, and revision-bound score calibration.
- Expose degraded vector retrieval in context metadata and add `--require-vector`, `vindex --incremental`, and `vcalibrate`.
- Add a 48-query Korean semantic quality gate covering long-document tails, Recall@5, citations, budgets, and latency.

## 0.5.0 - 2026-07-12

- Make compact context citations resolve only to values actually included in the AI payload.
- Add query-aware compact fields so nonstandard evidence such as architecture is retrievable and citable.
- Promote explicitly related documents into the final context candidates even when hybrid search ranks them below the limit.
- Require vector synchronization for document writes, roll back YAML and SQLite on failure, and recover interrupted vector writes from durable markers.
- Load custom document types from the active wiki root and expose type and HTTP source constraints through capabilities JSON Schema.
- Synchronize Gemini skills across Antigravity and Gemini CLI discovery paths and verify matching skill contents.
- Keep CLI JSON output parseable while package and installed skill versions temporarily differ during upgrades.
- Add isolated 1,000-document retrieval gates, vector failure injection tests, and Codex/Gemini 24-task live acceptance evidence.
- Add a detailed schema-v2 self-reference seed document and improve the human review article layout.

## 0.4.0 - 2026-07-12

- Add a stable AI JSON protocol with compact, full, and raw document views.
- Add token-budgeted hybrid context assembly with path-level citations and usage tracking.
- Add optimistic, validated RFC 6902 patching and autonomous JSON document creation.
- Exclude pending unverified drafts from normal context while preserving them for verification.
- Replace agent skill workflows with context-first retrieval and evidence-linked writeback.
- Add deterministic protocol benchmarks and a 24-task live Codex and Gemini acceptance gate.
- Keep Claude skill and protocol compatibility without requiring a paid live account for release.
- Use Antigravity CLI for Gemini evaluation and install Gemini skills under its global config path.

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
