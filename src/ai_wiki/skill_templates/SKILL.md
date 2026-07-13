---
name: ai-wiki
version: 1.1.2
description: AI-first reusable knowledge encyclopedia. Retrieve evidence-linked context before answering, cite document paths, and autonomously write back reusable knowledge through validated create or patch operations.
user-invocable: true
argument-hint: "[capabilities|context|get|record-use|record-feedback|patch|create|temporal] [query or options]"
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

3. Answer from each document's `evidence` chunks and cite only keys from
   `data.citations`. Treat `meta.retrieval.vector_status != "ready"` as degraded
   retrieval; use `doctor`, `reindex`, and `vindex` before high-stakes reuse.
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

## Entity Fidelity and Privacy Scope

Preserve the identifiers and attributes that make records reusable across
documents. Names, organizations, roles, nationalities, relationships, account
owners, and other entity keys are operational data when they were supplied by
the user, lawfully obtained in the authorized workspace, or published by a
reliable public source.

- Do not replace known entities with `A`, `B`, `Person 1`, or generic anonymous
  labels merely because the record concerns a third party.
- If a source withholds a name, preserve that uncertainty while retaining every
  other sourced attribute that distinguishes the entity. Never invent a name.
- Distinguish verified facts, attributed reports, allegations, and unresolved
  identity claims instead of deleting useful context.
- Protect the user's private data, credentials, secrets, and access-controlled
  fields. Enforce privacy primarily through correct wiki routing, authorization,
  and policy redaction rather than irreversible information loss.
- When anonymization is requested or required, add `content.identity_handling`
  with the reason, scope, source disclosure status, and preserved attributes so
  another agent can understand what was intentionally withheld.

## Entity-First Event Authoring

For a temporal (schema v3) matter, build the canonical graph before prose:

1. Create one `entities[]` record per real-world participant. Put sourced
   distinguishing attributes in `attributes`; keep a withheld name unknown,
   rather than creating a second generic person.
2. Create `events[]` using only `participant_ids`. If events form one sequence,
   connect them with `event_links` (`continues`, `escalates`, or `same_subject`).
3. Set `content.data.timeline_contract` to `entity_first`. Every
   `content.data.timeline[]` row must contain the canonical `event_id`
   and non-empty `entity_ids`. Those IDs must be participants of that event.
4. Write the narrative only after the graph exists. Use the canonical entity
   name or a faithful derived label; never introduce a new person label in a
   timeline row.

The engine rejects schema-v3 timeline rows that are not bound to a known event
and its participant IDs. This is deliberate: a readable sentence is not an
adequate substitute for an identity-preserving graph.

## Read Views

```bash
ai-wiki get <id>                         # compact JSON
ai-wiki get <id> --fields id,title,content.facts,sources
ai-wiki get <id> --view full             # canonical v2 document
ai-wiki get <id> --view raw              # exact YAML inside JSON
```

Use `search`, `vsearch`, `quality`, `doctor`, `reindex`, and `vindex` only for
diagnostics or maintenance. `context` is the normal retrieval entrypoint.

## Temporal Knowledge

Use `temporal current`, `as-of`, `known-as-of`, `timeline`, `why-changed`, and
`disputed` when the answer depends on when a fact was true or known. Never
resolve proposed transitions by silently replacing a current claim.
