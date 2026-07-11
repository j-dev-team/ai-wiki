---
name: ai-wiki
description: General-purpose fallback knowledge wiki skill. Use for reusable research, explanations, technology, science, history, economics, and cross-domain reference material when no dedicated wiki applies.
user-invocable: true
argument-hint: "[search|get|create|update|vsearch|doctor|todo|maintain] [query or options]"
---

# AI Wiki Skill

AI Wiki is a local structured knowledge base for reusable knowledge. It stores articles as YAML and exposes keyword search, vector search, quality checks, and maintenance commands through the `ai-wiki` CLI.

## When To Use

Use this skill for factual or background knowledge, reusable research, explanations, and retrieving or storing long-lived knowledge. When an installed dedicated wiki has matching manifest triggers, use that wiki and keep private records within their originating package.

## Required First Step

Before answering a reusable knowledge question, search the wiki:

```bash
ai-wiki search "query"
ai-wiki vsearch "semantic query"
```

Use found documents as context and mention document IDs when they materially support the answer.

## Common Commands

```bash
ai-wiki doctor
ai-wiki search "query"
ai-wiki vsearch "semantic query"
ai-wiki get <document-id>
ai-wiki template technology --output content.yaml
ai-wiki create --title "..." --category "technology/..." --source "https://..." --content-file content.yaml
ai-wiki update <document-id> --content-file content.yaml --source "https://..."
ai-wiki quality <document-id>
ai-wiki todo
ai-wiki maintain
ai-wiki vindex
```

## Storage Rules

- Use structured YAML, not prose-only notes.
- Include at least one source for factual claims whenever possible.
- Mark uncertainty with lower `confidence`, `limitations`, or verification metadata.
- Prefer stable categories such as `technology/python`, `history/korea`, `law/contracts`, or `science/ai`.
- Run `ai-wiki quality <id>` after significant edits.

If search or vector search is stale, run `ai-wiki doctor`, `ai-wiki reindex`, and `ai-wiki vindex`.
