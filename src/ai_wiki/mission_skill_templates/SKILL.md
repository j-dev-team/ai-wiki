---
name: ai-wiki-missions
version: 1.0.0
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
2. Read the latest plan and run. Never rely on a plan copied into a prompt.
3. Confirm that the exact plan revision is approved before starting a run.
4. Select only a ready task, then claim its lease with the current run revision.
5. Perform the task outside AI Wiki and retain file, command, test, source, or
   human-decision evidence for every acceptance criterion.
6. Submit evidence and move the task to `in_review`.
7. A reviewer or owner, not the submitting agent, decides completion.
8. On interruption, write a handoff containing current state, changed files,
   remaining work, evidence, and blocking reasons. A later Codex or Gemini
   session resumes from the pinned run and lease state.

## Modes

- `research`: create a ResearchReport only; do not begin implementation.
- `plan`: create or revise a WorkPlan and request independent approval.
- `execute`: start or continue an approved revision, claim one ready task, and submit evidence.
- `resume`: inspect handoff, expired leases, pinned revision, and existing evidence before acting.
- `status`: report plan, run, ready, blocked, failed, and review state without mutating it.
- `review`: match evidence to each criterion and complete, fail, or reopen the task.

Never delete autonomously, self-approve a plan, mark your own task completed,
reuse a stale plan revision, or move knowledge between isolated wiki roots.
