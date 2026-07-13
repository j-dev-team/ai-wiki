# AI Wiki Missions

AI Wiki Missions is an execution ledger, not an automation runtime. Codex and
Gemini perform work; AI Wiki preserves what was approved, claimed, executed,
verified, and handed off.

## Core Flow

```text
ResearchReport -> WorkPlan revision -> approval -> WorkRun
-> Task lease -> external execution -> Evidence -> independent review
-> KnowledgeCandidate -> privacy/routing/duplicate checks -> writeback
```

Research does not automatically start implementation. A run can start only
from an approved plan revision, and that revision remains pinned for the life
of the run. An agent may submit a task to review but cannot complete it.

## Commands

```bash
ai-wiki capabilities
ai-wiki schema-json --contract mission
ai-wiki mission create --document-file research.json --principal agent
ai-wiki mission create --document-file plan.json --principal agent
ai-wiki plan approve <plan-id> --if-revision 1 --principal reviewer
ai-wiki run start <plan-id> --principal agent
ai-wiki task ready <run-id> --principal agent
ai-wiki task claim <run-id> <task-id> --if-revision 1 --principal agent
ai-wiki task submit <run-id> <task-id> --evidence-file evidence.json \
  --result "implemented and tested" --if-revision 2 --principal agent
ai-wiki task verify <run-id> <task-id> --decision completed \
  --reason "criteria and evidence match" --if-revision 3 --principal reviewer
```

Use `run pause`, `run resume`, and the run's `handoff` payload when work moves
between sessions or agents. A lease prevents concurrent ownership of a task;
resource locks prevent two tasks from changing the same declared resource.

Evidence types are `file_change`, `command`, `test_result`, `commit`,
`document`, `screenshot`, `external_source`, and `human_decision`. Each item
records a locator, capture time, actor, result, optional content hash, source
IDs, and the exact acceptance criteria it proves in `criterion_ids`.

Completed tasks require evidence for every criterion. WorkRun history and
execution events are append-only. Expired leases make a task claimable again,
but never trigger automatic external execution.

## Local Operation Boundary

Missions are designed for one local operator and locally running agents. They
do not require accounts, login, shared hosting, or network exposure. Keep the
web UI bound to the local machine and protect the wiki directory through the
operating system account and filesystem permissions.
