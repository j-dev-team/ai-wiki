# AI Wiki Operations

## Primary Agent Commands

```bash
ai-wiki capabilities
ai-wiki context "question" --max-tokens 4000
ai-wiki get <id>
ai-wiki record-use <context-id> --citation "doc:<id>#<path>" --outcome answered
ai-wiki patch <id> --operations-file patch.json --if-version <version> --dry-run
ai-wiki create --document-file document.json --dry-run
```

## Maintenance

```bash
ai-wiki doctor
ai-wiki quality <id>
ai-wiki reindex
ai-wiki vindex
ai-wiki maintain
```

Maintenance databases and vectors are derived data. YAML remains the source of
truth. Do not run `migrate-schema --apply` unless the user explicitly requests
a bulk document migration.
