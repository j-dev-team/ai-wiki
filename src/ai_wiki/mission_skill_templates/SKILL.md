---
name: ai-wiki-missions
version: 1.1.4
description: Resume and execute approved AI Wiki Missions through revision-pinned plans, task leases, evidence submission, independent review, and handoff.
user-invocable: true
argument-hint: "[research|plan|execute|resume|status|review]"
---

# AI Wiki Missions

AI Wiki stores the plan, approval, lease, evidence, and handoff ledger. Codex or
Gemini performs file, command, browser, and research work in its own runtime.
AI Wiki never executes those operations itself.

## Mandatory Workflow

1. Run `ai-wiki capabilities` and inspect the Mission contract.
2. Read `data.language.authoring_language` and its warning. Use that language
   for all human-facing Mission prose and record it as `metadata.source_language`.
3. Before starting a run, read the exact plan revision from Missions. Never rely
   on a plan copied into a prompt.
4. Confirm that the exact plan revision is approved before starting a run.
5. For an existing run, use `ai-wiki run next <run-id>` as the normal execution
   read. Select only its ready task, then claim the lease with `run_revision`.
6. Perform the task outside AI Wiki and retain file, command, test, source, or
   human-decision evidence for every acceptance criterion.
7. Submit evidence and move the task to `in_review`.
8. A reviewer or owner, not the submitting agent, decides completion.
9. On interruption, write a handoff containing current state, changed files,
   remaining work, evidence, and blocking reasons. A later Codex or Gemini
   session resumes from the pinned run and lease state.

## Modes

- `research`: create a ResearchReport only; do not begin implementation.
- `plan`: create or revise a WorkPlan and request independent approval.
- `execute`: start or continue an approved revision, claim one ready task, and submit evidence.
- `resume`: inspect handoff, expired leases, pinned revision, and existing evidence before acting.
- `status`: report plan, run, ready, blocked, failed, and review state without mutating it.
- `review`: match evidence to each criterion and complete, fail, or reopen the task.

## Compact execution reads

Use one read that matches the current action. Do not call every compact command
in sequence when `run next` already contains enough context.

- Normal execution: `ai-wiki run next <run-id>` returns the next task together
  with the pinned plan and approval, dependency results, criteria, existing
  evidence summaries, lease, blockers, and handoff.
- Resume one known task: `ai-wiki task context <run-id> <task-id>`.
- Check progress: `ai-wiki run summary <run-id>` or the default compact
  `ai-wiki run status <run-id>`.
- Review one criterion: `ai-wiki run evidence <run-id> --criterion <criterion-id>`.
- Audit, incident investigation, or recovery only: `ai-wiki run status <run-id> --full`.

Every compact result includes `run_revision` and the exact pinned plan revision.
Re-read after a mutation or revision conflict. The compact views omit complete
history and evidence bodies; the immutable full ledger remains available with
`--full`.

Never delete autonomously, self-approve a plan, mark your own task completed,
reuse a stale plan revision, or move knowledge between isolated wiki roots.

## Language and readability

- A Korean wiki authors objectives, findings, recommendations, task titles,
  instructions, completion criteria, results, and handoffs in Korean. An English
  wiki authors those fields in English.
- Keep commands, file paths, code identifiers, hashes, evidence payloads, and
  original errors unchanged. They are technical source material, not prose to
  translate.
- A ResearchReport must contain a readable scope summary, substantive findings
  with explanations, and recommendations. Screenshots and identifiers support
  the prose; they never replace it.
- A WorkPlan must contain a readable objective and actionable task instructions,
  completion criteria, and verification methods.
- Preserve legacy `source_language: und` documents and exact approved revisions.
  Do not rewrite audit history merely to add a translation.
