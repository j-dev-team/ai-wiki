---
name: ai-wiki
version: 0.5.0
description: AI-first reusable knowledge encyclopedia. Retrieve evidence-linked context before answering, cite document paths, and autonomously write back reusable knowledge through validated create or patch operations.
user-invocable: true
argument-hint: "[capabilities|context|get|record-use|patch|create] [query or options]"
---

# AI Wiki Skill

AI Wiki is an encyclopedia operated primarily by AI agents. YAML is the durable
source of truth; agents use the stable CLI JSON protocol to retrieve, cite, and
write knowledge. Use a dedicated variant when its manifest triggers match.

## Mandatory Workflow

1. Discover the protocol when needed: `ai-wiki capabilities`.
2. Before answering reusable knowledge questions, run:

```bash
ai-wiki context "question" --max-tokens 4000
```

3. Answer from `data.documents` and cite keys from `data.citations`.
4. Record actual use:

```bash
ai-wiki record-use <context-id> --citation "doc:<id>#<path>" --outcome answered
```

5. If context is insufficient, record `--outcome insufficient`, research with
authoritative sources, then patch an existing document or create a new one.
6. Run context again after writeback and answer from the updated evidence.

## Safe Writeback

Read the current version before patching:

```bash
ai-wiki get <id>
ai-wiki patch <id> --operations-file patch.json --if-version <version> --dry-run
ai-wiki patch <id> --operations-file patch.json --if-version <version>
```

Create a document from JSON without temporary YAML:

```bash
ai-wiki create --document-file document.json --dry-run
ai-wiki create --document-file document.json
```

- Never retry a `version_conflict` without retrieving the current document.
- Never delete autonomously. Deletion requires an explicit user request.
- Prefer patching a matching document; `duplicate_conflict` returns candidates.
- Source-free reusable knowledge may be saved only as a pending, unverified draft.
- Do not use legacy full-document update in the normal agent workflow.

## Read Views

```bash
ai-wiki get <id>                         # compact JSON
ai-wiki get <id> --fields id,title,content.facts,sources
ai-wiki get <id> --view full             # canonical v2 document
ai-wiki get <id> --view raw              # exact YAML inside JSON
```

Use `search`, `vsearch`, `quality`, `doctor`, `reindex`, and `vindex` only for
diagnostics or maintenance. `context` is the normal retrieval entrypoint.
