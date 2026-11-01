# Loops and Harness²: viability decision

Research-only decisions, 2026-11-01. No external optimizer was installed or run.

## Reuse before adding a loop

| Mechanism | REIN evidence / decision |
|---|---|
| Codex Goals | Native durable objective with a success condition; use only when explicitly requested and supported by the session. REIN should supply acceptance evidence, not build another scheduler. |
| Ralph Loop | Official plugin repeats a prompt through a Stop hook; its documented default iteration count is unlimited. Do not enable it by default. If explicitly used, require an iteration cap and objective checks. |
| Harness engineering | Improve repository instructions, useful feedback and deterministic constraints. The small AGENTS.md, shared skills and existing gates follow this approach; no extra service is implied. |
| Spec-driven development | Keep selective planning and the existing plan-check contract. October's permissions failure shows that a plausible written decision can itself be wrong. Inspect access requirements independently. |
| Test-driven development | For a confirmed defect, reproduce it, fix it, then run the affected regression. A red test can be an environment or fixture problem; do not alter test setup merely to get green. |
| Verification-driven / generator–evaluator | Existing `gate.py`, state-bound reviews and independent auditor sessions already provide the pieces. Independent evaluation matters on high-risk changes; not every localized edit needs another agent. |
| Gas Town | Current upstream still includes a larger execution platform with Dolt/Beads and persistent agent roles. That duplicates Orca and violates the minimal-infrastructure objective here. |
| Agetor | Current upstream presents a kanban/worktree control plane for real agent CLIs. Useful as a comparison; execution management already belongs to Orca. Do not import its runtime. |

Before any bounded native iteration, identify the command/artifact proving success,
maximum attempts and elapsed time, and the available provider budget. On failure,
classify code/environment/flaky/quota/unknown. Repeat an unchanged failure only
with new evidence; otherwise stop and report what intervention is needed. Existing
Ship already has retry/audit/time caps, saved state and failure signatures. This
change adds no loop engine and does not claim those caps constrain arbitrary
native sessions automatically.

Sources actually consulted:
[Codex Goals](https://learn.chatgpt.com/use-cases/follow-goals),
[Ralph Loop README](https://raw.githubusercontent.com/anthropics/claude-plugins-official/main/plugins/ralph-loop/README.md),
[Harness engineering](https://openai.com/index/harness-engineering/),
[Gas Town](https://github.com/gastownhall/gastown),
[Agetor](https://github.com/alamops/agetor).
The detailed October source audits remain historical evidence, not a fresh audit
of every current upstream function.

## Harness²

[Harness² upstream](https://github.com/google-research/harness2) adds task-level
Propose–Probe–Compose and a persistent improvement harness. Unlike REIN's fixed
A/B pilot, it generates candidate harness edits and composes lessons for future
tasks. That is a real laboratory capability, but no REIN evidence yet shows it
outperforms a simpler manually edited skill on unseen tasks.

The repository documents a credential-free deterministic demo and separate
model-backed benchmarks with API costs. It supports a Codex substrate, but that
does not establish compatibility with the user's subscription quota. Its documented
executor network access and disabled Codex sandbox need a separate isolation design
before any model-backed experiment. Do not connect it to production worktrees.

Decision: **no runtime integration and no PoC yet**. First stabilize an uncontaminated
fixture set and obtain a bounded budget. Optimizing against the contaminated October
pilot risks teaching the harness to exploit test setup rather than improve code.
An external optimizer adds probe and composition executions before any useful task
result; savings or gains must be measured after accounting for those executions.

Proposed isolated PoC, not enabled:

- Hypothesis: one constrained skill edit reduces missed money/permission impacts
  on held-out synthetic repositories without increasing escaped defects.
- Freeze prompts, base commits, model versions, tools, acceptance tests and rubrics.
  Split development and held-out tasks by capability, not just by wording.
- Keep guardrails, test files and hidden rubrics outside editable components.
  Permit only discovery guidance edits. No automatic mutation of main.
- Begin with the deterministic demo in a disposable Linux environment. A later
  model probe needs explicit monetary or subscription budget, one candidate,
  one composition and one retry maximum per cell; stop at the first guard breach,
  quota brake, evaluator contamination or budget exhaustion.
- Record total probe/composition/evaluation consumption, not just the final run.
  Reject a complex strategy without demonstrated advantage over the simpler arm.
- No secrets, customer data, global dependency installation, scheduler or recurring
  quota consumption. A recurring lab requires separate explicit approval.
