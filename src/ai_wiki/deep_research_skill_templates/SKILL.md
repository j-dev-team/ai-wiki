---
name: __SKILL_NAME__-deep-research
version: 1.2.0
description: Run evidence-led deep research for __DISPLAY_NAME__. Use whenever a user asks for a deep investigation, comprehensive research, source comparison, conflicting evidence analysis, current-fact verification, or a ResearchReport before planning or implementation.
user-invocable: true
argument-hint: "[read-only|report] <research question>"
---

# __DISPLAY_NAME__ Deep Research

Use `__COMMAND_NAME__` and `__ROOT_ENV_NAME__` for this wiki only. Never read
or write another wiki root to complete a research request.

## Research contract

1. Turn the request into a brief: decision to support, scope, exclusions,
   time boundary, required source types, and stopping condition.
2. Retrieve the local evidence package first with `__COMMAND_NAME__ context`.
   Record use of every cited local document. Do not re-research facts that the
   local evidence already answers.
3. If the brief needs current or missing facts, research authoritative external
   sources. Preserve URL, publisher, retrieved time, claim scope, and whether a
   source is primary, official, attributed reporting, or commentary.
4. Build a claim ledger before prose. Every material claim is `supported`,
   `contested`, `insufficient`, or `out_of_scope`; link it to source IDs and a
   known/observed time. Do not convert an allegation, interpretation, or stale
   source into a verified fact.
5. Compare conflicting sources explicitly. State the conflict, why it remains,
   and what evidence would resolve it. Absence of a source is not confirmation.
6. Stop when the brief's evidence threshold is met, new sources add no material
   information, the budget is reached, or a required source is unavailable.
   Report the stop reason and open questions.

## Write boundary

- `read-only` is the default: return a cited research answer and do not create
  a Mission or wiki document.
- Create a ResearchReport only when the user requests Mission registration or
  explicitly authorizes durable writeback. Use the wiki authoring language.
- A ResearchReport records scope, exclusions, findings, recommendations,
  uncertainties, source/evidence IDs, and sufficiency. It never starts
  implementation; a separately approved WorkPlan is required.
- When web access is unavailable, say that current external facts could not be
  verified. Limit conclusions to local or user-provided evidence.

## Quality gate

Before answering or recording: verify source diversity is proportionate to the
claim, distinguish time-of-event from time-of-report, preserve known entities,
and list every unresolved contradiction. Technical paths, hashes, commands, and
source URLs remain verbatim even when prose is localized.
