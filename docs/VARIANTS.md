# Purpose-Specific Wikis

Purpose-specific wikis isolate data, commands, configuration, vector databases, web ports, and AI agent skills while sharing the `ai-wiki` engine.

For a complete Korean walkthrough, see the [AI Wiki 1.2.0 User Guide](USER_GUIDE.ko.md).
For canonical skill names, historical alias cleanup, and isolated Mission roots,
see the Korean [Skill Identity Contract](SKILL_IDENTITY_CONTRACT.ko.md).

## Install

List and inspect bundled presets:

```powershell
ai-wiki variant presets
ai-wiki variant show-preset law
```

Create a ready-to-use law wiki:

```powershell
ai-wiki variant install law-wiki `
  --preset law `
  --output-dir D:\dev `
  --agent codex `
  --lang ko
```

The command generates and installs the thin package, initializes its isolated root, installs the selected skill, builds the vector index, and runs `doctor`.
Initialization also creates a detailed schema-v2 self-reference document customized with the variant display name and command. The upstream AI Wiki source URLs remain unchanged, while metadata, sources, verification paths, and history are preserved.

```powershell
law-wiki doctor
law-wiki search "contract termination"
law-wiki vsearch "how to end a contract"
law-wiki-web
```

## Custom Manifest

Create a manifest from a bundled preset:

```powershell
ai-wiki variant init-manifest patent-wiki `
  --preset general `
  --display-name "Patent Wiki" `
  --domain patent `
  --command patent-wiki `
  --description "Patent research and filing knowledge" `
  --trigger "patent" `
  --trigger "filing" `
  --output D:\dev\patent-wiki.yaml
```

Review the YAML, then install it:

```powershell
ai-wiki variant install `
  --manifest D:\dev\patent-wiki.yaml `
  --output-dir D:\dev `
  --agent codex `
  --lang en
```

## Skills

Install or refresh a variant skill for each agent:

```powershell
ai-wiki variant install-skills D:\dev\law-wiki --agent claude
ai-wiki variant install-skills D:\dev\law-wiki --agent gemini
ai-wiki variant install-skills D:\dev\law-wiki --agent codex
```

| Agent | Skill directory |
| --- | --- |
| Claude Code | `~/.claude/skills/<wiki-name>/` |
| Gemini via Antigravity CLI | `~/.gemini/config/skills/<wiki-name>/` |
| GPT Codex | `~/.codex/skills/<wiki-name>/` |

## Backup and Restore

Stop the variant web server before backup or restore.

```powershell
ai-wiki variant backup D:\dev\law-wiki
ai-wiki variant backup D:\dev\law-wiki --output D:\backup\law-wiki.zip
ai-wiki variant restore D:\backup\law-wiki.zip D:\dev\law-wiki
law-wiki doctor
```

Restore replaces the target package with the verified archive contents. Check both paths before running it.

## Upgrade

Upgrade the shared engine, then refresh each thin package:

```powershell
python -m pip install --upgrade ai-wiki
ai-wiki variant upgrade D:\dev\law-wiki
law-wiki doctor
```

`variant upgrade` creates a backup first and rolls back if validation fails.

## Legacy Migration

Convert an older package containing a copied `ai_wiki` engine to the shared-engine layout:

```powershell
ai-wiki variant migrate D:\dev\legacy-wiki
```

The migration is backup-based. Verify article counts, vector counts, and representative search results afterward.

## Isolation and Routing Audits

```powershell
ai-wiki variant audit-isolation `
  D:\dev\law-wiki `
  D:\dev\labor-wiki `
  D:\dev\tax-wiki

ai-wiki variant audit-skills `
  D:\dev\law-wiki `
  D:\dev\labor-wiki `
  D:\dev\tax-wiki
```

The isolation audit checks roots, config names, environment prefixes, commands, ports, databases, and execution from an unrelated working directory. The skill audit checks installed skill files and routing collision cases.

## Uninstall

Uninstall commands and skills while preserving the package data:

```powershell
ai-wiki variant uninstall D:\dev\law-wiki
```

Delete the package root after creating a backup:

```powershell
ai-wiki variant uninstall D:\dev\law-wiki --purge --yes
```

`--purge --yes` is destructive. Confirm the generated backup path before removing any additional files.
