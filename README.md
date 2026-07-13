# AI Wiki — Structured Knowledge Encyclopedia

A CLI-based knowledge wiki system that stores, searches, and manages knowledge learned by AI assistants as **structured YAML** data.

[한국어 README](https://github.com/j-dev-team/ai-wiki/blob/master/README.ko.md)

[Korean User Guide](https://github.com/j-dev-team/ai-wiki/blob/master/docs/USER_GUIDE.ko.md) | [Purpose-Specific Wiki Guide](https://github.com/j-dev-team/ai-wiki/blob/master/docs/VARIANTS.md)

> **Local-only notice**: AI Wiki is designed for one local user and must not be exposed to a network. It needs no account, login, or server configuration. Protect the local OS account and the wiki directory; full-repository encryption is the operating system's responsibility.

---

## Why AI Wiki?

AI coding agents (Claude Code, Gemini via Antigravity CLI, GPT Codex) have no built-in way to retain and reuse knowledge across sessions or projects — every conversation starts from scratch.
**AI Wiki is a structured knowledge store that agents read and write directly.** Save a piece of knowledge once, and every agent in every project can access it automatically — no copy-paste, no repeated research.

---

## Features

- **AI-First Protocol** — Stable JSON envelopes for deterministic agent reading and writing
- **Budgeted Context** — Hybrid evidence packages constrained to an agent token budget
- **Evidence Citations** — Document ID + JSON Pointer citations linked to source verification
- **Safe Partial Writes** — RFC 6902 patching with optimistic version checks and quality gates
- **Autonomous Writeback** — Validated creation and pending drafts without full-document replacement
- **Structured YAML Storage** — Accumulate knowledge as key-value structured data, not markdown prose
- **FTS5 Full-Text Search** — SQLite FTS5-based keyword search for Korean and English
- **Vector Semantic Search** — `sentence-transformers` + `sqlite-vec` embedding search
- **Hybrid Search (RRF Fusion)** — Combines FTS5 + vector search via RRF (Reciprocal Rank Fusion): `score = Σ 1/(k+rank_i)`
- **Quality Gates** — 5-level automatic evaluation of confidence, sources, and verification
- **Auto Cross-Reference** — Automatic detection and bidirectional linking of related documents
- **Git Auto-Commit** — Automatic Git history recording on every document change
- **AI Agent Skill Integration** — Auto-generates skill files for Claude Code (`~/.claude/skills/`), Gemini via Antigravity CLI (`~/.gemini/config/skills/`), and GPT Codex (`~/.codex/skills/`)
- **Wiki Name Customization** — Name entered during init is displayed in the web UI header
- **Token Optimization** — Efficient operation with large document sets via DB metadata queries
- **Purpose-Specific Variants** — Install isolated law, labor, tax, business, research, or custom wikis backed by one shared engine
- **Safe Lifecycle** — Backup, restore, migration, upgrade rollback, uninstall, isolation audit, and skill routing audit
- **Temporal Evidence Ledger** — Reconstruct current, historical, and previously known claims without overwriting history
- **AI Wiki Missions** — Revision-pinned plans, task leases, evidence review, pause/resume, and Codex/Gemini handoff
- **Compact Mission Command Center** — One small next-task read carries the pinned plan, dependencies, criteria, evidence summary, lease, blockers, and handoff; the full ledger remains available for audit
- **Deep Research Skill** — Evidence-led, claim-led research with explicit scope and stop conditions; it is read-only unless durable research registration is requested
- **Retrieval Trust Loop** — Independently labeled calibration candidates with holdout gates and rollback
- **Read-Only Connectors** — Git, web, Google Drive, Notion, and Slack snapshots with provenance and permissions

---

## Installation

### User Installation (pip)

> **Requires Python 3.11+**

```bash
pip install ai-wiki
ai-wiki init ~/my-wiki
```

Upgrade an existing installation:

```bash
python -m pip install --upgrade ai-wiki
ai-wiki upgrade-skill
ai-wiki reindex
ai-wiki vindex
ai-wiki doctor
```

Version 1.2.0 upgrades the primary, Mission, and Deep Research skill set together.
The installer retains the full Mission audit ledger while making normal agent reads
compact and action-oriented.

## AI Agent Workflow

Agents use `context` instead of manually chaining keyword and vector search.
Every primary command returns a stable JSON envelope.

```bash
ai-wiki capabilities
ai-wiki context "How does optimistic concurrency protect wiki updates?" --max-tokens 4000 --require-vector
ai-wiki get <document-id> --fields id,title,content.facts,sources
ai-wiki record-use <context-id> --citation "doc:<id>#/content/data/facts/0" --outcome answered
ai-wiki temporal as-of <document-id> --at 2026-01-01T00:00:00Z
```

Reusable knowledge can be written without replacing the whole document:

```bash
ai-wiki patch <document-id> --operations-file patch.json --if-version 3 --dry-run
ai-wiki patch <document-id> --operations-file patch.json --if-version 3
ai-wiki create --document-file document.json --dry-run
ai-wiki create --document-file document.json
```

Source-free knowledge is stored as a low-confidence pending draft and excluded
from normal context retrieval. Existing v1 and v2 files are not rewritten by
reads. Modified legacy documents become v2; only documents receiving temporal
data are lazily saved as v3.

Mission work uses the separate `ai-wiki-missions` skill. AI Wiki stores the
approved plan, run, lease, evidence, review, and handoff; Codex or Gemini does
the actual file, command, browser, and research work. See
[`docs/MISSIONS.md`](docs/MISSIONS.md).

For deep research, use the installed `ai-wiki-deep-research` skill. It begins
with an evidence plan and claim ledger, treats external verification as
time-bounded, and does not create durable wiki records unless the user asks for
a ResearchReport or authorizes writeback.

### Developer Installation (source)

```bash
git clone https://github.com/j-dev-team/ai-wiki.git
cd ai-wiki
python -m venv .venv
source .venv/bin/activate   # Linux/macOS
# .venv\Scripts\activate    # Windows
pip install -e ".[test]"
```

> **Windows Note**: Use `python` instead of `python3`.

### Purpose-Specific Wiki

```powershell
ai-wiki variant install legal-team-wiki --preset law --output-dir D:\dev --agent codex --lang ko
```

See [Purpose-Specific Wikis](https://github.com/j-dev-team/ai-wiki/blob/master/docs/VARIANTS.md) for lifecycle and audit commands.

### Interactive Init Flow

`ai-wiki init` runs interactively:

1. **Language Selection** — `English` / `한국어`
2. **Wiki Name** — Displayed in the web UI header (saved in `.ai-wiki.yaml`)
3. **Agent Selection** — Choose which AI agents you use (default: Claude Code)
4. **Preset Selection** — Category structure matching your wiki's purpose
5. **Self-Reference Seed** — Creates a detailed schema-v2 document with the wiki architecture, AI workflow, sources, verification paths, safety policy, and current engine version

**Agent Selection UI:**

```
Which AI agents do you use? (comma-separated numbers)
  1. Claude Code
  2. Gemini via Antigravity CLI
  3. GPT Codex
Select [1,2,3] (default: 1):
```

- Default: `1` (Claude Code only)
- Multiple: `1,2` → Claude Code + Gemini via Antigravity CLI
- All: `1,2,3`
- Skills are installed only to the selected agent paths (saved in `.ai-wiki.yaml`)

Gemini uses Antigravity CLI (`agy`). Install it on Windows with
`irm https://antigravity.google/cli/install.ps1 | iex`, then run `agy` once and
authenticate with the Google account that owns the Gemini subscription.

| Preset | Description |
|--------|-------------|
| `general` | General knowledge (default) |
| `tech` | Technology & development focused |
| `business` | Business & management focused |
| `research` | Research & academic focused |

---

## Quick Start

```bash
# Search documents (hybrid: FTS5 + vector auto-combined)
ai-wiki search "python"

# List documents (recent first, default 50)
ai-wiki list

# Filter by category + sort by title
ai-wiki list --category technology/python --sort title

# Pagination
ai-wiki list --limit 20 --offset 40

# Get a document
ai-wiki get <document-id>

# Create a document
ai-wiki create \
  --title "Python Basics" \
  --category "technology/programming" \
  --tags "python,programming" \
  --confidence 0.9 \
  --source "https://docs.python.org" \
  --content-stdin << EOF
type: knowledge
what: Python is a general-purpose interpreted programming language
creator: Guido van Rossum
release_year: 1991
paradigm:
  - object-oriented
  - functional
  - procedural
use_cases:
  - web development
  - data science
  - automation
  - AI/ML
EOF
```

---

## How It Works

```
1. You ask your AI agent a question
2. The agent automatically searches the wiki via skill trigger
3. If found   -> agent uses the knowledge to answer
4. If not found -> agent researches, then saves new knowledge to the wiki
5. Next time any agent asks the same question -> instant answer from wiki
```

```
Your Question
     |
     v
+-----------+   skill   +---------+   search   +------------------+
| AI Agent  | --------> | ai-wiki | ---------> | AI Wiki          |
|           |           |  (CLI)  |            | (YAML + DB)      |
+-----------+           +---------+            +------------------+
     ^                      |                        |
     |         hit          |<-- found --------------+
     |                      |
     |         miss         v
     |              +--------------+
     +-- answer <-- |   Research   |
          + save    | (web / docs) |
                    +--------------+
```

All projects and all agents share the same wiki — knowledge accumulates automatically over time.

---

## Web UI

```bash
# Start web UI
ai-wiki-web

# Default port: 5000 → http://127.0.0.1:5000
# Wiki name is read from the `name` field in .ai-wiki.yaml
```

> See the security notice at the top of this document.

---

## CLI Command Reference

### Basic CRUD

| Command | Description |
|---------|-------------|
| `ai-wiki init [path]` | Initialize a new wiki (directory structure + config) |
| `ai-wiki create` | Create a new document |
| `ai-wiki get <ID>` | Get a document by ID |
| `ai-wiki update <ID>` | Update an existing document |
| `ai-wiki delete <ID> --confirm` | Delete a document |
| `ai-wiki list` | List all documents (`--sort`, `--category`, `--limit`, `--offset`) |
| `ai-wiki destroy [path]` | Destroy a wiki (remove skill files, env vars, directory) |
| `ai-wiki upgrade-skill` | Upgrade skill files to the latest version |

### Search

| Command | Description |
|---------|-------------|
| `ai-wiki search "query"` | Hybrid search (FTS5 + vector, auto-combined via RRF) |
| `ai-wiki vsearch "query"` | Semantic vector search |
| `ai-wiki similar <ID>` | Find documents similar to a given document |
| `ai-wiki tag <tag>` | List documents with a specific tag |
| `ai-wiki tags` | List all tags and document counts |

### Quality Management

| Command | Description |
|---------|-------------|
| `ai-wiki quality <ID>` | Single document quality report |
| `ai-wiki quality-all` | Batch quality check for all documents |
| `ai-wiki verify <ID>` | Update document verification date |
| `ai-wiki verify <ID> --human` | Mark as human-verified |
| `ai-wiki verify-queue` | List documents needing verification |
| `ai-wiki review <ID>` | Generate verification checklist |

### Wiki Maintenance

| Command | Description |
|---------|-------------|
| `ai-wiki reindex` | Rebuild search index |
| `ai-wiki vindex` | Rebuild vector index |
| `ai-wiki lint` | Wiki health check |
| `ai-wiki lint --fix` | Health check + auto-fix |
| `ai-wiki maintain` | Run lint + quality + todo together |
| `ai-wiki backlinks <ID>` | List documents referencing this document |
| `ai-wiki sync-backlinks` | Bulk sync all bidirectional backlinks |

### Analysis & Exploration

| Command | Description |
|---------|-------------|
| `ai-wiki stats` | Access statistics (Top N views/searches) |
| `ai-wiki gaps` | Gap analysis by category |
| `ai-wiki todo` | Auto-collected task list for the wiki |
| `ai-wiki stale` | List outdated documents (default: 90 days) |
| `ai-wiki discover` | Find isolated, low-quality, or stale documents |
| `ai-wiki path <ID1> <ID2>` | Shortest path between two documents (BFS) |
| `ai-wiki cluster` | Document topic clustering |
| `ai-wiki history <ID>` | Git change history for a document |

### Export / Import

| Command | Description |
|---------|-------------|
| `ai-wiki export <ID>` | Export as Markdown or YAML |
| `ai-wiki export-all` | Batch export all documents |
| `ai-wiki ingest <file>` | Ingest a source file (auto-creates stub document) |

---

## Hybrid Search

`ai-wiki search` **automatically combines** FTS5 keyword search and vector semantic search.

- **RRF (Reciprocal Rank Fusion)** algorithm merges both result sets
- Returns a unified `hybrid_score` field
- Vector search (`sentence-transformers`, `sqlite-vec`) is included in the default installation — no extra setup needed

```bash
# Hybrid search (default)
ai-wiki search "machine learning python"

# Pure vector search only
ai-wiki vsearch "machine learning python"
```

---

## Document Structure (YAML Schema)

```yaml
schema_version: 2
id: tech-python-abc123
title: Python Basics
category: technology/programming
tags: [python, programming]
metadata:
  confidence: 0.9
  document_version: 1
  created_at: 2026-01-01T00:00:00Z
  modified_at: 2026-01-01T00:00:00Z
  verified_at: 2026-01-01T00:00:00Z
  author: ai-agent
  maturity: mature
  completeness: 0.85
sources:
  - id: src-1
    url: https://docs.python.org/3/
    title: Python documentation
    retrieved_at: 2026-01-01T00:00:00Z
relations:
  - target_id: tech-django-xyz789
    type: related_to
    direction: outgoing
    weight: 0.8
    source_ids: [src-1]
content:
  type: technology
  data:
    what: Python is a general-purpose interpreted programming language
    facts:
      - Python was created by Guido van Rossum.
      - Python was first released in 1991.
    use_cases: [web development, data science]
verification:
  - path: /content/data/facts/1
    level: verified
    source_ids: [src-1]
    verified_at: 2026-01-01T00:00:00Z
history:
  - at: 2026-01-01T00:00:00Z
    action: created
    fields: [what, facts]
    note: Document created
extensions: {}
```

Schema v2 validates field types, date/time zones, HTTP(S) source URLs, content
types, required content fields, relation weights, and source references. Unknown
top-level fields are rejected. Custom content types must be registered in
`.ai-wiki.yaml` before use.

Existing v1 documents remain readable. Migrate them explicitly after upgrading:

```bash
ai-wiki migrate-schema            # dry-run and validation report
ai-wiki migrate-schema --apply    # backup, atomic conversion, index rebuild
ai-wiki schema-json --legacy > schema.json # bare integration JSON Schema
```

### File Storage Path

```
articles/<category>/<subcategory>/<slug>.yaml
```

Example: `articles/technology/programming/python-abc123.yaml`

---

## Quality System

### Verification Levels

Schema v2 stores verification records outside user content and targets claims with JSON Pointer paths.

| Level | Weight | Description |
|-------|--------|-------------|
| `unverified` | 0.0 | Not verified |
| `sourced` | 0.3 | Has a source |
| `verified` | 0.8 | Verified |
| `corroborated` | 0.8 | Cross-verified |
| `disputed` | 0.2 | Disputed |
| `human_verified` | 1.0 | Verified by a human |

### Maturity Stages

| Stage | Description |
|-------|-------------|
| `stub` | Minimal info, needs enrichment |
| `draft` | Basic info present |
| `review` | Under review |
| `mature` | Complete document |

### Quality Score Calculation

```
score = structure keys (15%) + word count (20%) + sources (10%) +
        tags (5%) + related docs (10%) + confidence (10%) + verification rate (30%)
```

---

## Directory Structure

### Wiki Directory (after `ai-wiki init`)

```
my-wiki/
+-- articles/           # Document YAML files (subdirectories by category)
+-- data/
|   +-- wiki.db         # Search index (SQLite FTS5)
|   +-- vectors.db      # Vector embedding index
+-- sources/            # Ingested source files
+-- logs/               # Operation logs
+-- .ai-wiki.yaml       # Wiki configuration
```

### Source Code (for developers)

```
src/ai_wiki/
+-- cli.py              # CLI entry point
+-- models.py           # Article data model
+-- storage.py          # File/DB storage layer
+-- index.py            # SQLite search index
+-- vector.py           # Vector search engine
+-- quality.py          # Quality validation engine
+-- schemas.py          # Type-specific schemas
+-- catalog.py          # Catalog builder
+-- wikilog.py          # Operation logger
+-- web.py              # Web UI (Flask)
```

---

## Wiki Operations

### Single Wiki (Default)

Install one wiki and all AI agent projects on the system can automatically access it.

```bash
ai-wiki init ~/my-wiki
```

→ See [AI Agent Skill Integration](#ai-agent-skill-integration) for details.

```
~/.claude/skills/my-wiki/   <- Claude Code
~/.gemini/config/skills/my-wiki/   <- Gemini via Antigravity CLI
~/.codex/skills/my-wiki/    <- GPT Codex

Project A --+
Project B --+-- Share a single wiki
Project C --+
```

### Multi Wiki

Run multiple independent wikis on a single system.

#### Creating Wiki Instances

```bash
# Work wiki
ai-wiki init ~/work-wiki
# -> Wiki name: "Work Wiki"
# -> Preset: business

# Personal knowledge wiki
ai-wiki init ~/knowledge-wiki
# -> Wiki name: "My Knowledge"
# -> Preset: general
```

#### Switching Wikis

Switch the target wiki using the `AI_WIKI_ROOT` environment variable.

```bash
# Search work wiki
AI_WIKI_ROOT=~/work-wiki ai-wiki search "client"

# Search personal wiki
AI_WIKI_ROOT=~/knowledge-wiki ai-wiki search "python"
```

#### Agent Skill Integration

`ai-wiki init` auto-generates skill files for each selected agent, one per wiki.

```
~/.claude/skills/
  +-- work-wiki/SKILL.md        (AI_WIKI_ROOT=~/work-wiki)
  +-- knowledge-wiki/SKILL.md   (AI_WIKI_ROOT=~/knowledge-wiki)

~/.gemini/config/skills/  ...  ~/.codex/skills/  (same pattern)

Project A --+
Project B --+-- Access both wiki skills
Project C --+
```

→ See [AI Agent Skill Integration](#ai-agent-skill-integration) for full details.

- **Data**: Fully isolated per wiki (separate articles/, data/)
- **Program**: Single shared ai-wiki CLI
- **Skills**: Independent skill files per wiki, accessible from all projects

---

## Environment Variables

| Variable | Description |
|----------|-------------|
| `AI_WIKI_ROOT` | Wiki root directory path (required) |

Automatically registered during `ai-wiki init`.

```bash
# Manual setup (Windows)
setx AI_WIKI_ROOT "C:\Users\you\my-wiki"

# Manual setup (Linux/macOS)
export AI_WIKI_ROOT="$HOME/my-wiki"
```

---

## Development / Testing

```bash
# Activate virtual environment
source .venv/bin/activate   # Linux/macOS
.venv\Scripts\activate      # Windows

# Run tests
pytest

# Reinstall package (after source changes)
pip install -e .
```

Release maintainers should follow the [Korean release workflow](https://github.com/j-dev-team/ai-wiki/blob/master/docs/RELEASE.ko.md). The checked PowerShell workflow is available as `scripts/release.ps1`.

---

## Dependencies

| Package | Purpose |
|---------|---------|
| `click` | CLI framework |
| `pyyaml` | YAML parsing/serialization |
| `flask` | Web UI server |
| `sqlite-vec` | Vector embedding search |
| `sentence-transformers` | Multilingual embedding model |
| `pecab` *(optional)* | Korean morphological analysis |

All dependencies including vector search are installed automatically via `pip install ai-wiki`.

---

## AI Agent Skill Integration

Skill files are **auto-generated** during `ai-wiki init`. When an agent receives a knowledge-related question, the skill auto-triggers and uses the `ai-wiki` CLI to search, query, and create documents.

| Agent | Skill Path |
|-------|------------|
| Claude Code | `~/.claude/skills/<wiki-name>/` |
| Gemini via Antigravity CLI | `~/.gemini/config/skills/<wiki-name>/` |
| GPT Codex | `~/.codex/skills/<wiki-name>/` |

**Skills are installed only to the agents you select during init.**

- **`ai-wiki init`** — Creates skill files in the selected agent paths
- **`ai-wiki upgrade-skill`** — Updates skill files to the latest version (reads `agents` from `.ai-wiki.yaml`)
- **`ai-wiki destroy`** — Removes skill files from selected agent paths
- If `agents` field is missing, defaults to `["claude"]` (backward compatible)

```bash
# Upgrade skill files to latest version
ai-wiki upgrade-skill
```

---

## License

MIT License. See [LICENSE](LICENSE) for details.
