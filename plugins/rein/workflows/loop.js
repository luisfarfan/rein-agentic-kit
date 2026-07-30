export const meta = {
  name: 'rein-loop',
  description: "Execute a planned change: bounded fresh-agent implementation steps, then an independent review gate, driven by the project's flow.config.json",
  whenToUse:
    'To execute an already-planned change. Every task is implemented as a bounded loop of short, FRESH agents handing off a compact ledger; then a reviewer that wrote none of it audits the change as a whole, and its findings return to the implementer until approved. Pass {change} for openspec plans; tasks.md needs no argument.',
  phases: [
    { title: 'Prepare' },
    { title: 'PlanCheck' },
    { title: 'Isolate' },
    { title: 'Map' },
    { title: 'Implement' },
    { title: 'Verify' },
    { title: 'Review' },
    { title: 'Integrate' },
  ],
}

// ─────────────────────────────────────────────────────────────────────────────
// WHY THIS SHAPE (measured, not preferred)
//
// ~90% of an agent run's token spend is cache_read: every turn re-reads the whole
// accumulated context, so cost ≈ turns × context-size. Output is ~0.3%, which is
// why "make the model write less" optimizations do nothing. Claude Code has no
// native eviction of stale tool results — only /compact, which is lossy and breaks
// the cache — so a long agent's context only grows.
//
// One agent that ran 241 turns re-reading ~234k of context each turn was most of a
// 112M-token run. Prompting it to be frugal did not help: the cause is not initial
// exploration, it is ACCUMULATION over a long agent.
//
// Three levers, all structural:
//   1. Every task is a bounded loop of FRESH agents (maxTaskSteps) handing off a
//      compact ledger. Context RESETS at every boundary, so spend stops growing
//      without a ceiling. Cutting and handing off is cheap; running 200 turns is not.
//   2. Per-agent model routing. On a subscription this does not lower the bill —
//      it frees the scarce Opus quota. Mechanical work → haiku, code → sonnet,
//      the review gate → opus.
//   3. Retrieval discipline in the shared prompt + bounded verification (ONE test,
//      never the suite) inside implementation steps.
//
// Measured effect: 241 turns → 26, Opus 100% → 0%, ~7× less context per turn.
//
// PORTABILITY: this file knows nothing about any specific project. Every command,
// path, model and limit arrives from `rein context` (flow.config.json > task runner
// > autodetect). Workflow scripts have no filesystem access, so that resolution
// happens in ONE bash round-trip made by the Prepare agent.
// ─────────────────────────────────────────────────────────────────────────────

let ARGS = args
if (typeof ARGS === 'string') {
  try {
    ARGS = JSON.parse(ARGS)
  } catch {
    ARGS = { change: ARGS }
  }
}
ARGS = ARGS || {}

const CHANGE = ARGS.change || ''
const ROOT = ARGS.root || '.'
const MAX_TASK_STEPS = ARGS.maxTaskSteps || 0 // 0 = take it from config
const MAX_REVIEW_ROUNDS = ARGS.maxReviewRounds || 0
const AUTO_HUMAN = !!ARGS.autoHumanReview
const WORKTREE_MODE = ARGS.worktree !== false
const ONLY = (ARGS.taskIds || []).map((t) => String(t).toUpperCase())
const DRY_RUN = !!ARGS.dryRun

// ── Schemas ──────────────────────────────────────────────────────────────────

const CONTEXT_SCHEMA = {
  type: 'object',
  properties: {
    ok: { type: 'boolean' },
    reinPath: { type: 'string' }, // the CLI invocation that worked; reused by every later agent
    root: { type: 'string' },
    stack: { type: 'string' },
    subtypes: { type: 'array', items: { type: 'string' } },
    verifyWarnings: { type: 'array', items: { type: 'string' } },
    verifyPolicy: {
      type: 'object',
      properties: {
        mode: { type: 'string' },
        requires: { type: 'array', items: { type: 'string' } },
        forbids: { type: 'array', items: { type: 'string' } },
        tools: { type: 'array', items: { type: 'string' } },
      },
      required: ['mode', 'requires', 'forbids', 'tools'],
      additionalProperties: false,
    },
    serve: {
      type: 'object',
      properties: {
        command: { type: 'string' },
        url: { type: 'string' },
      },
      required: ['command', 'url'],
      additionalProperties: false,
    },
    cmdTest: { type: 'string' },
    cmdTestOne: { type: 'string' }, // contains {target}
    cmdLint: { type: 'string' },
    cmdTypecheck: { type: 'string' },
    // T003: `rein verify` ACTUALLY RAN each configured command (D2) before any
    // implementer is paid. 'configured' distinguishes "no command to check" from
    // "checked and it is not invocable" -- an unconfigured slot is not a stop.
    // Reported literally from verify's own JSON, never re-derived (same style
    // as cmdTest/verifyPolicy above).
    verifyGate: {
      type: 'object',
      properties: {
        test: {
          type: 'object',
          properties: {
            configured: { type: 'boolean' },
            invocable: { type: 'boolean' },
            outcome: { type: 'string' },
          },
          required: ['configured', 'invocable', 'outcome'],
          additionalProperties: false,
        },
        lint: {
          type: 'object',
          properties: {
            configured: { type: 'boolean' },
            invocable: { type: 'boolean' },
            outcome: { type: 'string' },
          },
          required: ['configured', 'invocable', 'outcome'],
          additionalProperties: false,
        },
        typecheck: {
          type: 'object',
          properties: {
            configured: { type: 'boolean' },
            invocable: { type: 'boolean' },
            outcome: { type: 'string' },
          },
          required: ['configured', 'invocable', 'outcome'],
          additionalProperties: false,
        },
      },
      required: ['test', 'lint', 'typecheck'],
      additionalProperties: false,
    },
    // Round-2 finding 6: for the exact repo that motivated this change (a
    // monorepo with no `subproject` chosen), detect resolves ZERO commands,
    // so every verifyGate slot above is "unconfigured" and the ordinary
    // precheck default ("unconfigured is never a stop") would run a full
    // change with no mechanical gate whatsoever. This carries the one fact
    // that distinguishes that case from an honest "no linter here".
    monorepoUnconfigured: { type: 'boolean' },
    planPath: { type: 'string' },
    planSource: { type: 'string' },
    why: { type: 'string' },
    scopeOut: { type: 'array', items: { type: 'string' } },
    decisions: { type: 'array', items: { type: 'string' } }, // "D1 — title"

    artifacts: { type: 'array', items: { type: 'string' } },
    capabilities: { type: 'array', items: { type: 'string' } },
    tracker: { type: 'string' },
    baseBranch: { type: 'string' },
    worktreePrefix: { type: 'string' },
    maxTaskSteps: { type: 'number' },
    maxReviewRounds: { type: 'number' },
    modelAux: { type: 'string' },
    modelImpl: { type: 'string' },
    modelReview: { type: 'string' },
    tasks: {
      type: 'array',
      items: {
        type: 'object',
        properties: {
          id: { type: 'string' },
          title: { type: 'string' },
          dependsOn: { type: 'array', items: { type: 'string' } },
          type: { type: 'string' },
          verification: { type: 'string' },
          humanReview: { type: 'boolean' },
          acceptance: { type: 'array', items: { type: 'string' } },
        },
        required: ['id', 'title', 'dependsOn', 'type', 'verification', 'humanReview', 'acceptance'],
        additionalProperties: false,
      },
    },
    problem: { type: 'string' },
  },
  required: [
    'ok', 'reinPath', 'root', 'stack', 'subtypes', 'verifyPolicy', 'serve', 'cmdTest', 'cmdTestOne', 'cmdLint', 'cmdTypecheck',
    'planPath', 'planSource', 'why', 'scopeOut', 'decisions', 'artifacts', 'capabilities', 'tracker', 'baseBranch', 'worktreePrefix',
    'verifyWarnings', 'verifyGate', 'monorepoUnconfigured',
    'maxTaskSteps', 'maxReviewRounds', 'modelAux', 'modelImpl', 'modelReview', 'tasks', 'problem',
  ],
  additionalProperties: false,
}

const ISOLATE_SCHEMA = {
  type: 'object',
  properties: {
    done: { type: 'boolean' },
    summary: { type: 'string' },
    // Progress lives in the WORKTREE and only reaches the base branch on merge,
    // because unapproved work is never merged. So on a RESUMED run the base
    // branch still shows every task open while the worktree knows better.
    // Reading it here costs nothing: this agent is already in the worktree.
    pendingIds: { type: 'array', items: { type: 'string' } },
    commits: { type: 'array', items: { type: 'string' } },
    // Literal facts about the graph index build in the worktree (D2/D4): report
    // it, do not re-derive it. graphIndexed=false covers every failure mode —
    // missing binary, non-zero exit, timeout — uniformly, so a run never stops
    // over it; graphOutcome is the free-text reason, for the log only.
    graphIndexed: { type: 'boolean' },
    graphOutcome: { type: 'string' },
    blocked: { type: 'boolean' },
    blockedReason: { type: 'string' },
  },
  required: ['done', 'summary', 'pendingIds', 'commits', 'graphIndexed', 'graphOutcome', 'blocked', 'blockedReason'],
  additionalProperties: false,
}

const TASK_SCHEMA = {
  type: 'object',
  properties: {
    done: { type: 'boolean' },
    summary: { type: 'string' },
    commits: { type: 'array', items: { type: 'string' } },
    blocked: { type: 'boolean' },
    blockedReason: { type: 'string' },
  },
  required: ['done', 'summary', 'commits', 'blocked', 'blockedReason'],
  additionalProperties: false,
}

// Result of ONE bounded step. The LEDGER is what makes the hand-off cheap: a few
// hundred tokens instead of a 234k transcript. The next FRESH agent resumes from
// `remaining` without re-exploring or inheriting the previous step's context.
const STEP_SCHEMA = {
  type: 'object',
  properties: {
    done: { type: 'boolean' }, // the WHOLE task: every criterion + verification green
    progress: { type: 'string' }, // what THIS step did (compact)
    remaining: { type: 'string' }, // what is left (compact; empty when done)
    filesTouched: { type: 'array', items: { type: 'string' } },
    verification: { type: 'string' }, // result of the BOUNDED verification actually run
    committed: { type: 'boolean' },
    commits: { type: 'array', items: { type: 'string' } },
    blocked: { type: 'boolean' },
    blockedReason: { type: 'string' },
  },
  required: ['done', 'progress', 'remaining', 'filesTouched', 'verification', 'committed', 'commits', 'blocked', 'blockedReason'],
  additionalProperties: false,
}

const REVIEW_SCHEMA = {
  type: 'object',
  properties: {
    approved: { type: 'boolean' },
    verdict: { type: 'string' }, // APPROVED | CHANGES_REQUESTED
    findings: {
      type: 'array',
      items: {
        type: 'object',
        properties: {
          severity: { type: 'string', enum: ['BLOCKING', 'IMPORTANT', 'SUGGESTION'] },
          text: { type: 'string' },
        },
        required: ['severity', 'text'],
        additionalProperties: false,
      },
    },
    gateOutput: { type: 'string' }, // literal output of the mechanical gate — objective evidence
    gateGreen: { type: 'boolean' },
    // The only thing that legitimately blocks is a judgement solely the human can
    // give. Burning another implementer round on that is wasted time.
    needsHumanDecision: { type: 'boolean' },
    humanDecisionReason: { type: 'string' },
  },
  required: ['approved', 'verdict', 'findings', 'gateOutput', 'gateGreen', 'needsHumanDecision', 'humanDecisionReason'],
  additionalProperties: false,
}

const CODEMAP_SCHEMA = {
  type: 'object',
  properties: {
    maps: {
      type: 'array',
      items: {
        type: 'object',
        properties: {
          id: { type: 'string' },
          touchpoints: { type: 'array', items: { type: 'string' } },
          orientation: { type: 'string' },
        },
        required: ['id', 'touchpoints', 'orientation'],
        additionalProperties: false,
      },
    },
  },
  required: ['maps'],
  additionalProperties: false,
}

// Findings are per-task-id, same severity vocabulary as REVIEW_SCHEMA (D1: one
// vocabulary, one consequence). 'blocked'/'blockedReason' are for the agent
// itself failing its job (e.g. the plan file could not be read) — distinct
// from findings, which are the agent's judgement about the plan's content.
const PLANCHECK_SCHEMA = {
  type: 'object',
  properties: {
    findings: {
      type: 'array',
      items: {
        type: 'object',
        properties: {
          taskId: { type: 'string' },
          severity: { type: 'string', enum: ['BLOCKING', 'IMPORTANT', 'SUGGESTION'] },
          text: { type: 'string' },
        },
        required: ['taskId', 'severity', 'text'],
        additionalProperties: false,
      },
    },
    blocked: { type: 'boolean' },
    blockedReason: { type: 'string' },
  },
  required: ['findings', 'blocked', 'blockedReason'],
  additionalProperties: false,
}

// A transient API failure ("connection closed mid-response") must not kill a run:
// it killed one at step 1 before this existed. Only used for agents whose work is
// safe to repeat — reads, or steps whose committed work is already in git and
// whose instructions are unchanged.
async function agentRetry(prompt, opts, attempts = 2) {
  for (let i = 1; i <= attempts; i++) {
    try {
      const r = await agent(prompt, opts)
      if (r) return r
    } catch (e) {
      if (i === attempts) throw e
      log(`  ⟳ ${opts.label} threw (${e && e.message ? e.message : e}) — attempt ${i + 1}/${attempts}`)
      continue
    }
    if (i < attempts) log(`  ⟳ ${opts.label} died — attempt ${i + 1}/${attempts}`)
  }
  return null
}

// ── Phase 1: PREPARE — one bash round-trip resolves config AND plan ──────────
// `rein context` returns both. Parsing a task list is a parse, not a judgement:
// an agent doing it by hand spends turns re-deriving what a regex settles.
phase('Prepare')

const changeArg = CHANGE ? ` --change ${CHANGE}` : ''
const ctx = await agentRetry(
  `You are the PREPARE agent for the "rein" loop. Implement NOTHING, edit NOTHING, commit NOTHING.\n` +
    `Your job is ONE bash round-trip plus faithful reporting. Do NOT explore the repository.\n\n` +
    `1. Resolve the rein CLI. Try in order, stop at the first that works:\n` +
    `   a) 'rein'\n` +
    `   b) 'R=$(ls -d ~/.claude/plugins/cache/*/rein/*/bin/rein 2>/dev/null | tail -1); echo "$R"'\n` +
    `   Report the working invocation in 'reinPath' (an absolute path, or the bare word 'rein').\n` +
    `2. Run: '<reinPath> context ${ROOT}${changeArg}'. It prints JSON with 'config' and 'plan'.\n` +
    `   READ that JSON. Do NOT re-derive any of it, do NOT run the commands it reports.\n` +
    `2b. Also run (same round-trip, chain with '&&'): '<reinPath> verify ${ROOT} --only test,lint,typecheck ` +
    `--json'. It ACTUALLY RUNS just those three resolved slots (never 'build' or anything else — the\n` +
    `    gate precheck below only reads test/lint/typecheck, and running more in the operator's MAIN\n` +
    `    checkout before Isolate would burn time and risk writes for nothing this run consumes) and\n` +
    `    prints JSON with 'results' keyed by slot, each carrying 'invocable' (boolean) and 'outcome' (a\n` +
    `    string). It may exit non-zero when something is not invocable — that is expected, still read\n` +
    `    its JSON stdout. READ that JSON too. Do NOT re-run anything yourself, do NOT judge pass/fail —\n` +
    `    just report what it found, literally.\n` +
    `3. Report it back, mapping fields exactly:\n` +
    `   · cmdTest/cmdTestOne/cmdLint/cmdTypecheck  <- config.commands.{test,testOne,lint,typecheck}\n` +
    `     (empty string when a slot is absent — say so rather than inventing a command)\n` +
    `   · verifyPolicy <- config.verifyPolicy, serve <- config.serve when present, else {command:"",url:""}\n` +
    `     (non-frontend projects have none). Copy both LITERALLY, do not re-derive them.\n` +
    `   · verifyWarnings <- config.verifyWarnings, or [] when the key is absent. These say the policy\n` +
    `     cannot be satisfied as detected (no browser tool reachable, a guessed URL); the run surfaces\n` +
    `     them so a wrong instruction is visible instead of silently followed.\n` +
    `   · verifyGate.{test,lint,typecheck} <- for each slot: 'configured' is true iff the matching\n` +
    `     cmd{Test,Lint,Typecheck} above is non-empty. When configured, 'invocable'/'outcome' come\n` +
    `     LITERALLY from the verify JSON's results[slot].invocable/outcome. When NOT configured,\n` +
    `     there is nothing to check — report invocable=true, outcome="" (absence is not a broken gate).\n` +
    `   · monorepoUnconfigured <- true iff config.stack === "monorepo" AND config.missingCommands\n` +
    `     contains an entry starting with "choose a sub-project" (no sub-project has been named yet);\n` +
    `     else false. This is a monorepo root the kit can see into but that has not been pointed at a\n` +
    `     sub-project, which is a different fact than an ordinary project with no linter configured.\n` +
    `   · baseBranch <- config.worktree.baseBranch, worktreePrefix <- config.worktree.prefix\n` +
    `   · maxTaskSteps/maxReviewRounds <- config.limits, model* <- config.models\n` +
    `   · tasks <- plan.pending, ALREADY dependency-ordered. Keep that order. Copy each field\n` +
    `     LITERALLY, especially 'verification' — it is executed verbatim later.\n` +
    `   · planPath/planSource/artifacts <- plan.path, plan.source, plan.artifacts\n` +
    `   · why <- plan.why (verbatim, may be empty). scopeOut <- plan.scopeOut (each entry a\n` +
    `     string). decisions <- plan.decisions as "D1 — title" strings, TITLE ONLY, never the\n` +
    `     rationale: the titles travel in every agent's prompt and length is paid per turn.\n` +
    `4. Set ok=false and explain in 'problem' if: the CLI could not be resolved, the plan does not\n` +
    `   exist, or plan.unresolvableDeps is non-empty (name the ids — a dependency cycle means the\n` +
    `   order is only best-effort).`,
  { schema: CONTEXT_SCHEMA, label: 'prepare', phase: 'Prepare', agentType: 'general-purpose', effort: 'low', model: ARGS.modelAux || 'haiku' }
)

if (!ctx || !ctx.ok) {
  const why = ctx ? ctx.problem : 'the prepare agent died'
  log(`⛔ cannot start: ${why}`)
  return { ok: false, phase: 'Prepare', problem: why }
}

const REIN = ctx.reinPath || 'rein'
const STEPS = MAX_TASK_STEPS || ctx.maxTaskSteps || 8
const ROUNDS = MAX_REVIEW_ROUNDS || ctx.maxReviewRounds || 3
const MODEL_AUX = ARGS.modelAux || ctx.modelAux || 'haiku'
const MODEL_IMPL = ARGS.modelImpl || ctx.modelImpl || 'sonnet'
const MODEL_REVIEW = ARGS.modelReview || ctx.modelReview || 'opus'
const BASE = ctx.baseBranch || 'main'
const LABEL = CHANGE || 'change'
// Reported literally by Prepare, never re-derived here. Defaulted only so a
// prepare agent talking to an older CLI (no verifyPolicy/serve yet) degrades
// to 'unit' — today's behaviour — instead of throwing.
const VERIFY_POLICY = ctx.verifyPolicy || { mode: 'unit', requires: [], forbids: [], tools: [] }
const SERVE = ctx.serve || { command: '', url: '' }
const PREFIX = ctx.worktreePrefix || 'rein-wt'
const BRANCH = WORKTREE_MODE ? `${PREFIX}/${LABEL}` : BASE
// A worktree must live beside the repo: git refuses one inside the main tree.
const PARENT = ctx.root.slice(0, ctx.root.lastIndexOf('/'))
const WD = WORKTREE_MODE ? `${PARENT}/${PREFIX}-${LABEL}` : ctx.root

let tasks = ctx.tasks || []
if (ONLY.length) tasks = tasks.filter((t) => ONLY.includes(t.id.toUpperCase()))

// Before the no-tasks return: these describe project CONFIGURATION, not task
// state, and are the only in-run signal that the policy cannot be satisfied.
for (const w of ctx.verifyWarnings || []) log(`⚠️ ${w}`)

// T003/D3: the gate is checked (not re-derived) HERE, before any implementer
// is paid — a red gate found before Isolate costs nothing, the same one found
// at Review costs a whole run. Reported literally by Prepare; defaulted only
// so a prepare agent talking to an older CLI (no verifyGate yet) degrades to
// "nothing configured" instead of throwing.
const VERIFY_GATE = ctx.verifyGate || {
  test: { configured: false, invocable: true, outcome: '' },
  lint: { configured: false, invocable: true, outcome: '' },
  typecheck: { configured: false, invocable: true, outcome: '' },
}
const gatePrecheck = decideGatePrecheck(VERIFY_GATE, !!ctx.monorepoUnconfigured)
for (const w of gatePrecheck.warnings) log(`⚠️ ${w}`)
if (gatePrecheck.decision === 'stop') {
  log(`⛔ gate precheck: ${gatePrecheck.reason}`)
  return { ok: false, phase: 'Prepare', problem: gatePrecheck.reason, verifyGate: VERIFY_GATE }
}

if (!tasks.length) {
  log('nothing to do: no pending tasks in the plan')
  return { ok: true, change: CHANGE, planPath: ctx.planPath, implemented: [], verdict: 'no pending tasks' }
}
log(`${tasks.length} pending task(s): ${tasks.map((t) => t.id).join(', ')}`)
log(`stack ${ctx.stack}${ctx.subtypes.length ? ` (${ctx.subtypes.join(', ')})` : ''} · models ${MODEL_AUX}/${MODEL_IMPL}/${MODEL_REVIEW} · ≤${STEPS} steps/task · ≤${ROUNDS} review rounds`)

if (DRY_RUN) {
  return {
    ok: true,
    dryRun: true,
    change: CHANGE,
    tasks,
    resolved: { steps: STEPS, rounds: ROUNDS, wd: WD, branch: BRANCH, base: BASE, rein: REIN,
      commands: { test: ctx.cmdTest, testOne: ctx.cmdTestOne, lint: ctx.cmdLint, typecheck: ctx.cmdTypecheck } },
  }
}

// Shared prompt fragments (hasGraph/RETRIEVAL/artifactList/CTX) are built AFTER
// Isolate — see below — because hasGraph depends on what Isolate reports about
// the WORKTREE it just built (D2), not on ctx.capabilities alone.

const INTEGRITY =
  `INTEGRITY (non-negotiable): real logic and real tests. NEVER weaken or skip a verification, NEVER fake ` +
  `success, NEVER stub a real step to force green, NEVER commit failing tests, NEVER let a failure pass ` +
  `silently. If you genuinely cannot solve something, report it as blocked with the reason: an honest ` +
  `blocker is worth more than a false green.\n` +
  `Tests with fakes have passed while the real output was broken. If a task has a verification against ` +
  `reality, perform it — do not substitute a mock.`

// Gate commands, from config only. A slot the project did not define is not invented.
const gateCmds = [
  ctx.cmdTest && `'${ctx.cmdTest}'`,
  ctx.cmdLint && `'${ctx.cmdLint}'`,
  ctx.cmdTypecheck && `'${ctx.cmdTypecheck}'`,
].filter(Boolean)

// Pure (takes cmdTestOne/cmdTest as arguments rather than reading the outer
// `ctx` closure) so it can be extracted straight out of the shipped source and
// executed by a test, the same way decideRound/decideGatePrecheck are.
//
// Round-2 finding 2: `detect.resolve()` prefixes a chosen sub-project's
// `testOne` with `cd <subproject> &&` (same construction as test/lint/
// typecheck), which silently changes what `{target}` is relative to. An
// implementer working from the worktree ROOT naturally substitutes a
// root-relative path (`apps/web/src/foo.test.ts`), producing
// `cd apps/web && npx vitest run apps/web/src/foo.test.ts` — nothing found,
// a burned step whose red signal is not a code problem. `rein verify` cannot
// catch this either: it substitutes an ABSOLUTE temp path for {target}, which
// works from any cwd, so verify-green + step-red is exactly the setup-vs-code
// misdirection T002 exists to remove. Stating the base explicitly here is the
// chosen fix (over NOT prefixing testOne): the prefix is required for the
// command to be invocable AT ALL from the worktree root, so the base must be
// named instead of removed.
function boundedVerification(task, cmdTestOne, cmdTest) {
  if (task.verification) return `'${task.verification}'`
  if (cmdTestOne) {
    const cdMatch = /^cd\s+('[^']*'|\S+)\s+&&\s+/.exec(cmdTestOne)
    const dir = cdMatch ? cdMatch[1].replace(/^'|'$/g, '') : ''
    const targetNote = dir
      ? `substitute {target} with the one test file or id covering your change, given RELATIVE TO '${dir}' — ` +
        `this command already 'cd's there first, so {target} is NOT relative to the repo root`
      : `substitute {target} with the one test file or id covering your change`
    return `the narrowest form of '${cmdTestOne}' (${targetNote})`
  }
  if (cmdTest) return `'${cmdTest}'`
  return `(no verification command is configured — say so in 'verification' rather than pretending you ran one)`
}

// Additive per-mode guidance, from verifyPolicy only — nothing here names a
// stack, a framework or a port; those come from ctx/SERVE. Empty string in
// 'unit' mode keeps every prompt byte-identical to before this policy existed,
// so library and CLI projects see no change.
function implementerPolicyBlock() {
  if (VERIFY_POLICY.mode === 'rendered') {
    return (
      // The requirement text comes from VERIFY_POLICY.requires, not a second
      // English restatement of it here: two copies of one rule drift apart with
      // nothing failing, and a future mode would silently get no requirement.
      // D1: the render happens in Verify, by an agent that implemented nothing
      // — NOT here. Telling the implementer to "serve and render" is the exact
      // unsatisfiable instruction this policy exists to remove: only ask for
      // what the implementer can honestly state (does it boot cleanly with the
      // serve command), never for an observation only the render agent makes.
      `RENDERED VERIFICATION (mode: rendered): ${VERIFY_POLICY.requires.join('; ')}. A green ${ctx.stack} test ` +
      `suite does NOT make this task done by itself, but rendering it is NOT your job — a separate agent that ` +
      `implemented nothing renders and observes it independently in the Verify phase after this run (D1), using ` +
      `${SERVE.command || '(no serve command is configured; say so rather than inventing one)'}` +
      `${SERVE.url ? ` at ${SERVE.url}` : ''}` +
      `${VERIFY_POLICY.tools.length ? ` and ${VERIFY_POLICY.tools.join(', ')}` : ''}. Leave the app able to boot ` +
      `cleanly with that command — that you CAN honestly verify, so do it — and do NOT serve it, render it, or ` +
      `write a render claim into 'verification' yourself; that claim would be unfalsifiable coming from you.\n`
    )
  }
  if (VERIFY_POLICY.mode === 'plan-only' && VERIFY_POLICY.forbids.length) {
    return (
      `HARD PROHIBITION (mode: plan-only): never run ${VERIFY_POLICY.forbids.join(', ')} against real infrastructure ` +
      `as verification for this task — a plan-only task is verified by inspection, never by mutating anything real.\n`
    )
  }
  return ''
}

function reviewerPolicyBlock() {
  if (VERIFY_POLICY.mode === 'rendered') {
    return (
      // D1: the implementer never rendered anything to self-report, so this
      // must not send the reviewer looking for a note the implementer had no
      // honest way to write. The render outcome comes from the loop's own
      // Verify phase (the RENDER EVIDENCE block appended below), not from the
      // implementer's 'verification' text.
      `This project's policy is 'rendered': the mechanical gate is INCOMPLETE without observed render evidence — ` +
      `a green test suite with a FAILED or ABSENT render is NOT grounds for APPROVED. The render was gathered ` +
      `independently this Verify phase against ${SERVE.url || 'the served app'} (D1) — judge the RENDER EVIDENCE ` +
      `block below, NOT any claim in the implementer's own notes about serving or rendering. If it says FAILED, ` +
      `that alone is a CHANGES_REQUESTED finding. If it says 'rendered-unverified' (no browser tool reachable), ` +
      `that is a DIFFERENT fact — 'we could not look' — state it plainly but it does not by itself block APPROVED.\n`
    )
  }
  if (VERIFY_POLICY.mode === 'plan-only' && VERIFY_POLICY.forbids.length) {
    return (
      `HARD PROHIBITION (mode: plan-only): ${VERIFY_POLICY.forbids.join(', ')} must never have run against real ` +
      `infrastructure as part of this change's verification — if they did, the gate is not actually green.\n`
    )
  }
  return ''
}

// D1/D2 as executable policy, not just prose in the prompt. Pure so it can be
// extracted and run by tests the same way implementerPolicyBlock/reviewerPolicyBlock
// already are: review result (+ round bookkeeping) in, one decision out.
//   approve   — gate green and zero BLOCKING findings, whatever the reviewer's verdict said
//   fix       — a round is spent; the fix agent gets BLOCKING+IMPORTANT only (D1: SUGGESTION never costs a round)
//   escalate  — the reviewer flagged a judgement only the human can make
//   reject    — not approvable and no rounds remain
function decideRound(review, round, maxRounds, render) {
  const RECOGNIZED_SEVERITIES = ['BLOCKING', 'IMPORTANT', 'SUGGESTION']
  const rawFindings = review.findings || []
  const findings = rawFindings.map((f) =>
    typeof f === 'string' ? { severity: 'IMPORTANT', text: f } : f
  )
  const sev = (f) => String(f.severity || '').toUpperCase()
  // A finding "arrived untagged" if it was a plain string, or an object whose
  // severity is missing or not one of the three recognized words. Both get
  // normalized to IMPORTANT above for display, but that normalization must
  // not silently unlock D2's override below — the reviewer never spoke the
  // vocabulary D2 is judging against.
  // Zero findings with a CHANGES_REQUESTED verdict is the same violation as
  // untagged ones, only worse: the reviewer spoke the vocabulary LESS. The
  // gate CLI refuses to record that episode; the loop must not quietly merge
  // on it either.
  const spokeNoVocabulary =
    rawFindings.length === 0 ||
    rawFindings.some((f) => typeof f === 'string' || !RECOGNIZED_SEVERITIES.includes(sev(f)))
  const hasUntagged = spokeNoVocabulary
  const blocking = findings.filter((f) => sev(f) === 'BLOCKING')
  const fixWorthy = findings.filter((f) => sev(f) === 'BLOCKING' || sev(f) === 'IMPORTANT')
  const mechanicalGateGreen = !!review.gateGreen
  // T003 (symmetric to the red-gate override): mode 'rendered' with a FAILED
  // render is exactly as disqualifying as a red mechanical gate — a green test
  // suite never substitutes for a render nobody watched succeed. D4:
  // 'rendered-unverified' is a DIFFERENT fact (no tool was reachable, not that
  // the render broke) and must never force a round by itself — it is only
  // carried through on the returned decision so it can reach the operator.
  const renderStatus = (render && render.status) || ''
  const renderFailed = renderStatus === 'failed'
  const renderUnverified = renderStatus === 'rendered-unverified'
  const gateGreen = mechanicalGateGreen && !renderFailed

  if (review.needsHumanDecision) {
    return {
      decision: 'escalate',
      findings,
      humanDecisionReason: review.humanDecisionReason || 'a supervised task needs your verdict',
      renderUnverified,
      renderFailed,
    }
  }

  if (gateGreen && blocking.length === 0) {
    if (hasUntagged && !review.approved) {
      // The gate.record_review CLI refuses this exact input (CHANGES_REQUESTED
      // with no BLOCKING-tagged finding). Silently overriding it to approve
      // here would be quieter than that refusal and would merge a branch the
      // reviewer asked to change — the precise false green this loop exists
      // to prevent. Spend a round instead of trusting a vocabulary the
      // reviewer never actually spoke.
      // ...but never past the cap: on the final round a fix agent's commits
      // can no longer be re-reviewed, so dispatching one is pure unreviewed
      // spend — the exact cost this change exists to remove (round-5 finding).
      if (round >= maxRounds) {
        return {
          decision: 'reject',
          findings: fixWorthy,
          reason: 'round cap reached with a vocabulary-less CHANGES_REQUESTED — not approved, not worth an unreviewable fix round',
          renderUnverified,
          renderFailed,
        }
      }
      return {
        decision: 'fix',
        findings: fixWorthy,
        reason:
          "the reviewer requested changes with untagged findings — D2's override cannot be applied to a vocabulary the reviewer did not speak",
        renderUnverified,
        renderFailed,
      }
    }
    // Symmetric to the red-gate override below: D2 says CHANGES_REQUESTED
    // requires a BLOCKING finding, so a reviewer that said CHANGES_REQUESTED
    // anyway with none is overridden to approve, not trusted at face value.
    const overridden = !review.approved
    return {
      decision: 'approve',
      findings,
      overridden,
      overrideReason: overridden
        ? 'gate green and no BLOCKING findings, but the reviewer said CHANGES_REQUESTED — D2 requires a BLOCKING finding for that verdict'
        : '',
      renderUnverified,
      renderFailed,
    }
  }

  // T003: a render failure is reported distinctly from a red mechanical gate
  // (even though both flow through the same `gateGreen` AND above) so the fix
  // agent and the operator are told WHICH thing broke, not just that "something"
  // did.
  const reason = !mechanicalGateGreen
    ? 'the mechanical gate is red; it must be green before approval'
    : renderFailed
    ? `mode 'rendered': the render failed${render && render.reason ? ` (${render.reason})` : ''} — a green test suite does not substitute for it`
    : `${blocking.length} BLOCKING finding(s)`

  if (round >= maxRounds) {
    return { decision: 'reject', findings, reason, renderUnverified, renderFailed }
  }
  return { decision: 'fix', findings: fixWorthy, reason, renderUnverified, renderFailed }
}

// A red gate can produce a 'fix' decision with zero fixWorthy findings (an
// APPROVED verdict carries no BLOCKING findings per D2), which would render
// the fix prompt with an empty list and no statement that the gate is red.
// This prepends the gate's own reason as a synthetic BLOCKING finding so the
// fix agent is always told what actually forces the round.
function buildFixFindings(review, decision) {
  const base = decision.findings || []
  // A fix round needs at least one concrete instruction. Three ways to arrive
  // with none: a red MECHANICAL gate (the failure IS the finding), a FAILED
  // render (decision.renderFailed — the mechanical gate can be green while the
  // render is not, per T003's decideRound, and that fact must not be silently
  // dropped just because the reviewer also returned unrelated findings), or a
  // reviewer that requested changes while speaking no vocabulary at all — empty
  // findings. In all three, the synthesized reason becomes the brief; an empty
  // numbered list would send an agent to fix nothing, and a findings list with
  // no mention of the render would send it to fix the WRONG thing.
  if (!review.gateGreen || decision.renderFailed || base.length === 0) {
    return [{ severity: 'BLOCKING', text: decision.reason || 'the review gate did not pass' }, ...base]
  }
  return base
}

// Pure and self-contained (only `planPath` in scope) so it can be extracted the
// same way decideRound is: a regex pull of the source plus `new Function`,
// proving the actual shipped prompt rather than a restatement of it.
// Exactly four lenses (no more, no fewer) — each one a failure mode this repo
// hit for real, not a generic "review the plan" ask.
function buildPlanCheckPrompt(planPath, taskIds) {
  const scope = (taskIds || []).length
    ? `Judge ONLY these tasks — the ones THIS run will execute: [${taskIds.join(', ')}]. Completed ` +
      `tasks and tasks excluded from this run are not yours to judge: a defect there cannot waste ` +
      `this run's implementers, and stopping for it would be a false stop.\n\n`
    : ''
  return (
    scope +
    `You are the PLAN-CHECK agent. Your ONLY job is to critique the plan's own text before any ` +
    `implementer is paid to build it. Read ONLY ${planPath}. Do NOT read, grep, open, or explore ` +
    `any other file in the repository — the codebase does not exist yet as far as you are concerned; ` +
    `judging it against code is out of scope for this agent.\n\n` +
    `Apply exactly these four lenses to every task in the plan:\n` +
    `  1. Criteria that cannot be checked as written — vague or unfalsifiable acceptance text a ` +
    `verifier could not mechanically confirm or deny.\n` +
    `  2. Verifications that are unbounded — a command that runs the whole suite where one test or ` +
    `one file would prove the criterion, burning far more context than the task needs.\n` +
    `  3. Criteria satisfiable in letter by a test whose fixture avoids the case it claims to cover — ` +
    `the failure this repo produced five times: a test that passes without ever exercising the real path.\n` +
    `  4. Tasks that contradict the plan's own Scope or dependency order — touching something the plan ` +
    `marks out of scope, or depending on a task that has not run yet.\n\n` +
    `For every issue found, report one finding with the offending task's id, a severity of BLOCKING, ` +
    `IMPORTANT, or SUGGESTION (BLOCKING only for something that would waste an implementer's run), and ` +
    `a one-sentence 'text'. Report zero findings if the plan holds up under all four lenses.\n` +
    `Set blocked=true only if you could not read or parse the plan file itself; that is a fault in the ` +
    `check, not a judgement about its content.`
  )
}

// Pure: findings in, one decision out. D4 — a BLOCKING plan finding stops the
// run before any implementer is paid; anything else is carried in the return
// value without stopping anything.
// Pure so tests can execute it rather than grep for its call site.
function shouldRunPlanCheck(args) {
  return !args || args.planCheck !== false
}

function decidePlanCheck(findings, runIds) {
  const list = (findings || []).map((f) =>
    typeof f === 'string' ? { taskId: '', severity: 'IMPORTANT', text: f } : f
  )
  const sev = (f) => String(f.severity || '').toUpperCase()
  const inRun = (f) => {
    // A finding with no taskId is plan-LEVEL (a Scope contradiction, a broken
    // dependency order) — it concerns the whole run, so it may stop it. A
    // finding pinned to a task this run will not execute cannot waste this
    // run's implementers, and stopping for it would be the false stop the
    // scoping exists to prevent.
    if (!f.taskId) return true
    if (!runIds || !runIds.length) return true
    return runIds.includes(String(f.taskId).toUpperCase())
  }
  const blocking = list.filter((f) => sev(f) === 'BLOCKING' && inRun(f))
  if (blocking.length) {
    return {
      decision: 'stop',
      findings: list,
      reason: `${blocking.length} BLOCKING plan finding(s) — stopping before Isolate (D4)`,
    }
  }
  return { decision: 'continue', findings: list }
}

// D3 as executable policy: verification happens where it is cheap — a red
// gate found HERE (before Isolate) costs nothing, the same one found at
// Review costs a whole run. Pure so it can be extracted and run by tests the
// same way decidePlanCheck/decideRound already are.
//   test not invocable           -> stop: no implementer is paid toward a gate
//                                   that cannot pass
//   test invocable but failing   -> continue: the ordinary state of a repo
//                                   mid-change; stopping for it would make the
//                                   loop unusable
//   lint/typecheck not invocable -> warning only, carried to the reviewer;
//                                   neither is required for a verdict
//   an unconfigured slot is never a stop or a warning — there is nothing to
//   check, which is a different fact than "checked and broken"
//   EXCEPT: a monorepo root with no sub-project chosen (round-2 finding 6).
//   `detect` there resolves ZERO commands, so every slot above reads
//   "unconfigured" — but that is not "no linter exists", it is "the kit can
//   see this is a monorepo and knows exactly what is missing" (the same fact
//   already carried in `missingCommands`). Paying an implementer toward a
//   gate that cannot report anything at all inverts D3, so this is its own
//   stop, checked BEFORE the ordinary "unconfigured is never a stop" default.
function decideGatePrecheck(verifyGate, monorepoUnconfigured) {
  const vg = verifyGate || {}
  const slot = (name) => vg[name] || { configured: false, invocable: true, outcome: '' }
  const warnings = []
  for (const name of ['lint', 'typecheck']) {
    const s = slot(name)
    if (s.configured && !s.invocable) {
      warnings.push(`${name} is not invocable (${s.outcome || 'unknown'}) — carried to the reviewer, not required for a verdict`)
    }
  }
  const test = slot('test')
  if (test.configured && !test.invocable) {
    return {
      decision: 'stop',
      reason: `the test command is not invocable (${test.outcome || 'unknown'}) — no implementer is paid to work toward a gate that cannot pass`,
      warnings,
    }
  }
  if (monorepoUnconfigured) {
    return {
      decision: 'stop',
      reason: 'this is a monorepo root with no sub-project chosen — set "subproject" in flow.config.json to one ' +
        'of subprojects[].path before the loop can resolve a mechanical gate at all',
      warnings,
    }
  }
  return { decision: 'continue', reason: '', warnings }
}

// D2: a capability is only claimed where its tools will actually RUN. Every
// graph command (`graphify query/path/explain`) executes inside the WORKTREE
// the Isolate step just built — a DIFFERENT directory than the base repo
// `ctx.capabilities` was detected in. A base-repo `graphify-index` capability
// says nothing about whether THIS worktree has an index: that mismatch is
// exactly what made every graph command in every past run answer "graph file
// not found". Availability comes ONLY from what Isolate reports building in
// the worktree — never from the base-repo capability list — so a stale or
// mismatched capability can never claim a graph that is not actually there.
// When there is no worktree at all (WORKTREE_MODE=false) there is no base/work
// split to guard against: work happens directly where capabilities were
// detected, so that list is trusted as-is.
// Pure so it is executed by a test (T001 acceptance), not asserted by comment.
function decideGraphAvailable(worktreeMode, capabilities, isolate) {
  if (!worktreeMode) return (capabilities || []).includes('graphify-index')
  return !!(isolate && isolate.graphIndexed)
}

// D4: indexing failure never stops the run — building the prompt is separate
// from deciding availability (decideGraphAvailable reads what this prompt's
// agent reports, never re-derives it). Pure so the actual instructions the
// agent receives are executed by a test, not asserted by comment.
function buildIsolatePrompt(root, base, wd, branch, rein) {
  return (
    `You work in ${root} (the ${base} tree). Prepare ISOLATION for this run. Implement NOTHING.\n` +
    `1. If ${root} has UNCOMMITTED work ('git status --porcelain'), mention it in the summary ` +
    `   (another loop may be active) — but continue: the worktree is cut from the committed HEAD.\n` +
    `2. Create it: 'git -C ${root} worktree add ${wd} -b ${branch} 2>/dev/null || ` +
    `   git -C ${root} worktree add ${wd} ${branch}' (reuse the branch if it exists). If ${wd} already ` +
    `   exists and belongs to this change, reuse it — do not fail.\n` +
    `3. Verify: 'git -C ${wd} rev-parse --abbrev-ref HEAD' reports ${branch}.\n` +
    `4. Report the plan's state INSIDE the worktree: run '${rein} tasks ${wd}' and put in\n` +
    `   'pendingIds' the ids of tasks whose checkbox is still unticked THERE. On a resumed run\n` +
    `   the worktree knows what already landed and the base branch does not. Copy the ids\n` +
    `   literally; do not judge whether the work looks done.\n` +
    `5. Build the code graph index IN THE WORKTREE (no LLM, ~1-2s). First make its output path ` +
    `worktree-locally excluded so it can never end up staged from here, even in a repo whose own ` +
    `.gitignore lacks the entry: run 'f="$(git -C ${wd} rev-parse --git-path info/exclude)"; ` +
    `grep -qxF "graphify-out/" "$f" 2>/dev/null || printf "graphify-out/\\n" >> "$f"' — info/exclude is ` +
    `per-worktree state that is itself never committed, so this needs no change to ${root}'s tracked ` +
    `files. This step is non-blocking too (D4): if it fails, continue anyway. Then run ` +
    `'cd ${wd} && graphify update . --no-cluster' ` +
    `(cwd MUST be ${wd} itself — 'graphify update ${wd}' from a different cwd splits the index, writing ` +
    `manifest.json into the WRONG directory and corrupting that directory's own incrementality). ` +
    `This is a HINT for later steps, never a gate — if the 'graphify' binary is missing, the command errors, ` +
    `or it hangs past your tool's own timeout, that is fine: do NOT retry it and do NOT let it fail this step ` +
    `(D4, the run continues with the graph off). Set graphIndexed=true ONLY if the command exited 0 AND ` +
    `${wd}/graphify-out/graph.json now exists; otherwise graphIndexed=false. Set graphOutcome to what ` +
    `literally happened (the command's own message, or "graphify: command not found" if it is not on PATH) ` +
    `— report it, do not judge it.\n` +
    `Set done=true when steps 2-4 all succeeded (step 5 never blocks done, per D4).`
  )
}

// D4: no reachable browser tool is an explicit, carried outcome — never a
// silent pass, never a hard stop. Pure so it is executed by tests instead of
// asserted by comment; also what the Verify phase itself calls, so the
// dispatch decision under test IS the one that runs.
// `serve` is checked too (finding 4): SERVE defaults to {command:'',url:''}
// (a REACHABLE state — detect.py ships an empty serve command rather than an
// absent one, and flow.config.json can set mode:'rendered' with no `_serve()`
// at all) and an empty command/url renders as a bare, unusable serve-probe
// invocation. That is "we could not look", not "we looked and it broke" —
// D4 says it must degrade to unverified, never dispatch into a guaranteed
// CLI usage failure the fix agent cannot resolve by writing code.
function decideRenderDispatch(verifyPolicy, serve) {
  const vp = verifyPolicy || { mode: '', tools: [] }
  if (vp.mode !== 'rendered') return { dispatch: false, unverified: false, reason: '' }
  if (!(vp.tools || []).length) return { dispatch: false, unverified: true, reason: 'no browser tool reachable' }
  const sv = serve || { command: '', url: '' }
  if (!sv.command || !sv.url) {
    return { dispatch: false, unverified: true, reason: 'no serve command/url is configured' }
  }
  return { dispatch: true, unverified: false, reason: '' }
}

// D3 as executable policy: 'rendered: true' with no facts alongside it is a
// failed render, whatever the agent claims. Pure so it is executed by tests.
function decideRenderOutcome(render) {
  const r = render || {}
  const status = r.httpStatus
  const statusOk = typeof status === 'number' && status >= 200 && status < 300
  const evidence = Array.isArray(r.evidence) ? r.evidence : []
  if (!r.rendered) return { failed: true, reason: 'rendered=false' }
  if (!statusOk) {
    return {
      failed: true,
      reason: `httpStatus ${status === undefined || status === null ? 'is absent' : `${status} is not 2xx`}`,
    }
  }
  if (evidence.length === 0) return { failed: true, reason: 'rendered=true but evidence is empty' }
  return { failed: false, reason: '' }
}

// The ONE additional Verify-phase agent for mode:'rendered' (D1: it implements
// nothing, only observes). Built ONLY from its own arguments so it can never
// name a tool the caller did not pass — the caller passes ONLY
// verifyPolicy.tools, already filtered to what is actually reachable.
//
// D2 as it actually applies to a render: '${rein} serve-probe' is the ONE
// deterministic CLI that owns the whole server lifecycle, but a render needs
// the server held up WHILE a separate browser tool navigates — the reachable
// tools (claude-in-chrome, browser-testing, ...) only navigate, they cannot
// start a dev server themselves. So the single-shot `--command/--url` form
// (start, poll, tear down, return) cannot host a render: by the time it
// returns, the server is already gone. `--start --pidfile` keeps the SAME
// CLI owning the process group but leaves it running past the call, and
// `--stop --pidfile` is the matching teardown — two invocations of one
// deterministic CLI, not two different mechanisms.
//
// `rein` is ALWAYS the caller's resolved REIN (never the bare literal 'rein'
// — finding 2): the binary may not be on PATH, or a stale 'rein' already on
// PATH may resolve to an older installed plugin copy with no `serve-probe`
// subcommand at all, in which case a hardcoded 'rein serve-probe' fails in a
// way no fix agent can repair by writing code.
// `wd` is ALWAYS the tree containing the change under review (finding 3): in
// WORKTREE_MODE that is a sibling directory of the loop's own cwd, so both
// invocations get an explicit `--cwd`/`cd` into it and an ABSOLUTE pidfile —
// a relative pidfile would be written in one bash round-trip's cwd and read
// in another's, silently orphaning the server across a cwd difference.
function buildRenderPrompt(rein, command, url, tools, wd, pidfile) {
  const toolList = (tools || []).join(', ')
  return (
    `You are the RENDER agent for the Verify phase. You implement NOTHING, edit NOTHING, commit NOTHING — ` +
    `you only observe and report facts. Run EVERYTHING from ${wd} — that is the tree containing the change; ` +
    `never render against any other checkout.\n` +
    `1. Start the app and keep it running for the render: run ` +
    `'cd ${wd} && ${rein} serve-probe --command "${command}" --url ${url} --cwd ${wd} --start --pidfile ${pidfile}'. ` +
    `It starts the process group, polls ${url} for a real TCP accept, and — because of --start — leaves the group ` +
    `running (already its own session) instead of tearing it down immediately, so step 2 has a live server to ` +
    `render against; do NOT background the server yourself (no '&', no nohup, no manual kill) at any point (D2) — ` +
    `this CLI owns the whole lifecycle, start through stop. If its JSON says ready=false, stop here: there is ` +
    `nothing to render — report rendered=false with the reported error in 'notes' and skip step 3 (a failed ` +
    `--start already tore itself down, nothing is left running).\n` +
    `2. Render ${url} using ${toolList || '(no browser tool is reachable — do not invent one)'}. OBSERVE the HTTP ` +
    `status of the initial load, the page <title>, and any uncaught console errors.\n` +
    `3. Tear the app down: run 'cd ${wd} && ${rein} serve-probe --stop --pidfile ${pidfile}'. Do this even if ` +
    `step 2 failed — an orphaned server must never outlive this agent.\n` +
    `4. Report exactly these fields: rendered (boolean — did a page genuinely load), httpStatus (number, 0 if ` +
    `unknown), title (string), consoleErrors (array of strings, [] if none), evidence (array of concrete facts ` +
    `you observed, e.g. ["HTTP 200", "title: Dashboard", "0 console errors"] — NEVER a summary sentence), and ` +
    `notes (free text for anything else).\n` +
    `'rendered: true' with an empty 'evidence' array is not a passed render (D3) — it is a claim with nothing ` +
    `behind it.`
  )
}

const closeCmd =
  ctx.tracker === 'beads'
    ? (id) => `'bd close ${id} --reason "<what landed>"' and '${REIN} close ${id} --root ${WD}'`
    : (id) => `'${REIN} close ${id} --root ${WD}' (ticks the checkbox deterministically — do not hand-edit the plan)`

// ── Phase 1.3: PLAN CHECK — catch plan defects before paying implementers ───
// D4: a BLOCKING plan finding stops the run before any implementer is paid.
// One agent, no retries beyond agentRetry's standard, no second opinion, no
// per-task fan-out — the check must cost less than the mistake it prevents.
// A dead checker degrades to a logged warning: a run must never be lost to
// the thing meant to protect it.
let planFindings = []
if (shouldRunPlanCheck(ARGS)) {
  phase('PlanCheck')
  const planCheck = await agentRetry(
    buildPlanCheckPrompt(ctx.planPath, tasks.map((t) => t.id)),
    { schema: PLANCHECK_SCHEMA, label: 'plan-check', phase: 'PlanCheck', agentType: 'general-purpose', effort: 'low', model: MODEL_IMPL }
  )
  if (!planCheck || planCheck.blocked) {
    const why = planCheck ? planCheck.blockedReason || 'could not read the plan' : 'the plan-check agent died'
    log(`⚠️ plan check unavailable (${why}) — continuing without it`)
  } else {
    const verdict = decidePlanCheck(planCheck.findings, tasks.map((t) => t.id))
    for (const f of verdict.findings) {
      const marker = f.severity === 'BLOCKING' ? '⛔' : f.severity === 'IMPORTANT' ? '⚠️' : 'ℹ️'
      log(`  ${marker} plan-check [${f.taskId || '?'}] ${f.severity}: ${f.text}`)
    }
    if (verdict.decision === 'stop') {
      log(`⛔ plan check: ${verdict.reason}`)
      return { ok: false, phase: 'PlanCheck', problem: verdict.reason, findings: verdict.findings }
    }
    // Non-blocking plan-check findings are carried, not dropped — symmetric to
    // how review's non-blocking observations survive into openFindings below.
    planFindings = verdict.findings
  }
} else {
  log('plan check skipped (planCheck: false)')
}

// ── Phase 1.5: ISOLATE — one worktree + branch per run ──────────────────────
// Running loops in parallel is then safe with no per-run decision, and an
// unapproved run leaves an ugly branch rather than a broken base branch.
let setup = null
if (WORKTREE_MODE) {
  phase('Isolate')
  setup = await agentRetry(
    buildIsolatePrompt(ctx.root, BASE, WD, BRANCH, REIN),
    { schema: ISOLATE_SCHEMA, label: 'isolate', phase: 'Isolate', agentType: 'general-purpose', effort: 'low', model: MODEL_AUX }
  )
  if (!setup || setup.blocked || !setup.done) {
    const why = setup ? setup.blockedReason || setup.summary : 'the agent died'
    log(`⛔ could not create worktree ${WD}: ${why} — aborting`)
    return { ok: false, phase: 'Isolate', problem: why, worktree: WD, branch: BRANCH }
  }
  log(`🌿 isolated in ${WD} (branch ${BRANCH})`)
  // D4: indexing failure never stops the run — only logged, never a gate.
  log(setup.graphIndexed ? `📈 graph indexed in ${WD}` : `📉 graph not indexed (${setup.graphOutcome || 'no report'}) — continuing with the graph off`)

  // Drop tasks the worktree already closed. Without this a resumed run re-does
  // finished work: the plan is read from the base branch, where nothing is ticked
  // until the merge.
  const stillOpen = setup.pendingIds || []
  if (stillOpen.length) {
    const before = tasks.length
    tasks = tasks.filter((t) => stillOpen.includes(t.id))
    if (tasks.length !== before) {
      log(`↩︎ ${before - tasks.length} task(s) already closed in the worktree — skipping them`)
    }
  }
  if (!tasks.length) {
    log('every task is already closed in the worktree — nothing to implement')
  }
}

// ── Shared prompt fragments ─────────────────────────────────────────────────
// Built HERE, after Isolate: hasGraph must reflect the WORKTREE the agents
// actually run graph commands in, never the base repo ctx.capabilities was
// detected in (D2) — see decideGraphAvailable. Evaluated by a test the same
// way decideRound/decideGatePrecheck are (T001 acceptance).
const hasGraph = decideGraphAvailable(WORKTREE_MODE, ctx.capabilities, setup)
// Measured on this repo: get_symbols_overview maps a 697-line file in 178 tokens
// where reading it costs 7,097 — 40x — and find_symbol returns one function body
// in the single turn a grep-then-read pair would have taken two. Orientation is
// where the money is: the median agent spends 41 turns before its first edit, and
// every tool result it accumulates is re-sent on every later turn.
const hasSerena = (ctx.capabilities || []).includes('serena-project')
// Pure (hasSerena/hasGraph as args, not read from the outer closure) so all four
// on/off combinations are executed by a test the same way decideGraphAvailable
// is (T002 acceptance) — including proving the no-tool and serena-only branches
// stay byte-identical to before the graph teaching below existed.
// D3: teaches 'graphify explain'/'graphify path', each with a one-line statement
// of what it returns — NEVER 'graphify query'. This repo's extracted graph is
// structure-only (contains/calls edges, no data/string-literal edges), so a
// natural-language query answers with a confident-looking subgraph an agent
// cannot tell apart from a correct one, and pays for on every later turn (D3).
function buildRetrievalBlock(hasSerena, hasGraph) {
  return (
    `RETRIEVAL — do not burn context. The real cost is cache_read: every turn re-reads everything ` +
    `accumulated so far, so cost ≈ context-size × turns.\n` +
    (hasSerena
      ? `  · SYMBOL-LEVEL FIRST — this project has serena (language-server backed). Before any whole-file read:\n` +
        `      serena get_symbols_overview <file>   what is in a file, without reading it\n` +
        `      serena find_symbol <name>            the definition, with include_body for its source\n` +
        `      serena find_referencing_symbols      every caller, instead of grepping for one\n` +
        `      serena get_diagnostics_for_file      type errors WITHOUT running a build\n` +
        `    Read with offset/limit only for what these cannot answer.\n`
      : '') +
    (hasGraph
      ? `  · Orient with the graph before grepping or opening whole files: 'graphify explain "<symbol>"' returns ` +
        `its definition plus its direct callers/callees; 'graphify path "<A>" "<B>"' returns the call/reference ` +
        `chain between two symbols, if one exists.\n`
      : '') +
    (!hasSerena && !hasGraph
      ? `  · Locate with bounded search (grep with a concrete path and pattern) before opening files.\n`
      : '') +
    `  · Read ONLY the symbols/regions you will touch (Read with offset/limit), NEVER whole files "just in case" — ` +
    `every large file you pull in is RE-READ on every later turn.\n` +
    `  · Keep command output small: filter and scope it (head, -q, concrete paths) instead of dumping everything.\n` +
    `  · Aim to finish in FEW turns with precise reads, not to explore incrementally.`
  )
}
const RETRIEVAL = buildRetrievalBlock(hasSerena, hasGraph)

const artifactList = (ctx.artifacts || []).length
  ? `Read these first — they are the source of truth for intent:\n` + ctx.artifacts.map((a) => `  - ${a}\n`).join('')
  : ''

const CTX =
  `You work in ${WD}` +
  (WORKTREE_MODE
    ? ` (a git worktree on branch ${BRANCH}, already created). 'cd ${WD}' first and always use paths inside ` +
      `${WD}; do NOT touch ${ctx.root} directly. Commit to ${BRANCH}, NOT ${BASE}: the loop merges only if the ` +
      `change is approved.\n`
    : ` (branch ${BASE}).\n`) +
  `Plan: ${ctx.planPath}\n` +
  // Deliberately NOT the "why": that is judgement context the reviewer needs and
  // an implementer does not, and CTX is re-read on every turn of every agent.
  // Only what an implementer could actually violate travels here.
  ((ctx.scopeOut || []).length
    ? `OUT OF SCOPE for this change — do not touch, even if it looks broken:\n` +
      ctx.scopeOut.map((s) => `  - ${s}\n`).join('')
    : '') +
  ((ctx.decisions || []).length
    ? `DECISIONS already made — respect them, do NOT re-open what was decided:\n` +
      ctx.decisions.map((d) => `  - ${d}\n`).join('')
    : '') +
  artifactList +
  `Conventional Commits.\n` +
  RETRIEVAL

// ── Phase 1.7: MAP — one cheap scout, so N implementers do not each explore ──
// A HINT, never a contract: if the scout dies the map is empty and implementers
// explore exactly as they would have.
// Pure (root/planPath/artifactList/taskIds as args) so it is executed by the
// same test as buildRetrievalBlock (T002 acceptance) — this is the single
// heaviest graph consumer in the loop (every task, every run), so its D3
// discipline (teach 'explain'/'path', never 'query') matters most here.
function buildScoutPrompt(wd, planPath, artifactList, taskIds) {
  return (
    `You work in ${wd}. You are the SCOUT: implement NOTHING, commit NOTHING. Build a small CODE MAP ` +
    `per task so implementers do not explore from zero — the loop's real cost is re-read context, so less ` +
    `exploration = fewer turns.\n` +
    (artifactList || `Read ${planPath} ONCE.\n`) +
    `For EACH of [${taskIds.join(', ')}] use THE GRAPH, not grep or whole-file reads: ` +
    `'graphify explain "<symbol>"' returns its definition plus its direct callers/callees; ` +
    `'graphify path "<A>" "<B>"' returns the call/reference chain between two symbols, when a task spans more ` +
    `than one.\n` +
    `Return per task: 'touchpoints' (a SHORT list of "path:symbol" — a hint, not a dump) and 'orientation' ` +
    `(one line: where to start). If unsure about a task, return empty touchpoints and say so — it is a ` +
    `STARTING POINT, not a contract.`
  )
}

const codeMapById = {}
if (hasGraph) {
  phase('Map')
  try {
    const scout = await agent(
      buildScoutPrompt(WD, ctx.planPath, artifactList, tasks.map((t) => t.id)),
      { schema: CODEMAP_SCHEMA, label: 'scout', phase: 'Map', agentType: 'general-purpose', effort: 'low', model: MODEL_IMPL }
    )
    for (const m of (scout && scout.maps) || []) codeMapById[m.id] = m
    log(`🗺️ code map: ${Object.keys(codeMapById).length}/${tasks.length} tasks`)
  } catch (e) {
    log(`⚠️ scout failed (${e && e.message ? e.message : e}) — implementers explore as usual`)
  }
}

function mapHintFor(id) {
  const m = codeMapById[id]
  if (!m || (!(m.touchpoints || []).length && !m.orientation)) return ''
  const tp = (m.touchpoints || []).length ? `you probably touch: ${m.touchpoints.join(', ')}. ` : ''
  return `\nMAP (scout's starting point, NOT exhaustive — start here and verify; explore if it is not enough): ${tp}${m.orientation || ''}\n`
}

// ── One bounded step ────────────────────────────────────────────────────────

function stepPrompt(task, proxied, ledger, step) {
  const cont = ledger
    ? `CONTINUATION (step ${step}): a previous FRESH agent already advanced THIS SAME task. Do NOT re-explore ` +
      `or redo its work — continue from what is left.\n` +
      `  · done so far: ${ledger.progress}\n` +
      `  · files touched: ${(ledger.files || []).join(', ') || '(none declared)'}\n` +
      `  · last verification: ${ledger.verification || '(none)'}\n` +
      `  · REMAINING (start here): ${ledger.remaining}\n`
    : `First step of this task.\n`

  const criteria = (task.acceptance || []).length
    ? `Acceptance criteria (all of them must hold):\n` + task.acceptance.map((a, i) => `  ${i + 1}. ${a}\n`).join('')
    : `Acceptance criteria are in ${ctx.planPath} under ${task.id} — read that entry, nothing else.\n`

  return (
    `${CTX}\n\nYou are the IMPLEMENTER of task ${task.id} ("${task.title}").\n` +
    criteria +
    mapHintFor(task.id) +
    cont +
    `BOUNDED-STEP CONTRACT (this is the point — an agent that runs 200 turns re-reads its bloated context ` +
    `every single turn and costs a fortune): do ONE FOCUSED STRETCH, not the whole task at once. Run the ` +
    `NARROWEST verification that covers your change — ${boundedVerification(task, ctx.cmdTestOne, ctx.cmdTest)} — NEVER the full suite, ` +
    `and do not dump its whole output. Commit whatever is green.\n` +
    implementerPolicyBlock() +
    `If the task is NOT 100% complete after that, STOP and return done=false with a COMPACT ledger ` +
    `(progress/remaining/filesTouched/verification, a few lines) so another FRESH agent continues. Do NOT ` +
    `keep accumulating context to "just finish it": cutting and handing off is CHEAP, running 200 turns is ` +
    `what is expensive.\n` +
    `Return done=true ONLY when everything holds: all acceptance criteria + the task's verification green + ` +
    (gateCmds.length ? `${gateCmds.join(' and ')} green + ` : '') +
    `committed + the task closed with ${closeCmd(task.id)}.\n` +
    (proxied
      ? `This task is SUPERVISED and the owner delegated its real verification: when you reach done, do what ` +
        `they would do — exercise the REAL artifact and inspect the actual output — and judge against that ` +
        `evidence, not against fakes. Put the evidence paths in 'progress'.\n`
      : '') +
    `\n${INTEGRITY}`
  )
}

// One task = a bounded loop of fresh agents. Context resets at every boundary;
// only the compact ledger travels between them.
async function implementTaskBounded(task, proxied) {
  let ledger = null
  const allCommits = []
  const touched = new Set()
  for (let step = 1; step <= STEPS; step++) {
    let res
    try {
      // Retried: a step that dies transiently would otherwise block the task
      // permanently. Repeating it is safe — whatever it already committed is in
      // git, and the replacement agent is fresh with the same instructions.
      res = await agentRetry(stepPrompt(task, proxied, ledger, step), {
        schema: STEP_SCHEMA,
        label: `impl:${task.id}#${step}`,
        phase: 'Implement',
        agentType: 'general-purpose',
        model: MODEL_IMPL,
      })
    } catch (e) {
      return { id: task.id, status: 'error', detail: `step ${step}: ${e && e.message ? e.message : e}`, commits: allCommits }
    }
    if (!res) return { id: task.id, status: 'blocked', detail: `the agent of step ${step} died twice`, commits: allCommits }

    for (const c of res.commits || []) allCommits.push(c)
    for (const f of res.filesTouched || []) touched.add(f)

    if (res.blocked) return { id: task.id, status: 'blocked', detail: res.blockedReason || `blocked at step ${step}`, commits: allCommits }
    if (res.done) {
      return { id: task.id, status: proxied ? 'implemented-proxy' : 'implemented', detail: res.progress || 'implemented', commits: allCommits, steps: step }
    }

    ledger = {
      progress: res.progress || '',
      remaining: res.remaining || '',
      files: Array.from(touched),
      verification: res.verification || '',
    }
    log(`  ↻ ${task.id} step ${step}/${STEPS} — remaining: ${(res.remaining || '').slice(0, 90)}`)
  }
  return {
    id: task.id,
    status: 'blocked',
    detail: `did not finish in ${STEPS} bounded steps (anti-runaway cap); remaining: ${ledger ? ledger.remaining : ''}`,
    commits: allCommits,
  }
}

// ── Phase 2: IMPLEMENT ───────────────────────────────────────────────────────
// Sequential on purpose: every task writes to the same tree. Parallelism belongs
// BETWEEN runs (each with its own worktree), where it does not cause git locks
// and flaky tests.
phase('Implement')

const results = []
const failedIds = new Set()

for (const task of tasks) {
  const blockingDep = (task.dependsOn || []).find((d) => failedIds.has(d))
  if (blockingDep) {
    log(`⏸ ${task.id} skipped: depends on ${blockingDep}, which did not land`)
    results.push({ id: task.id, status: 'waiting', detail: `depends on ${blockingDep}` })
    failedIds.add(task.id)
    continue
  }

  if (task.humanReview && !AUTO_HUMAN) {
    log(`✋ ${task.id} is supervised (Human review: true) — left for you`)
    results.push({ id: task.id, status: 'needs-human', detail: 'requires the owner' })
    failedIds.add(task.id)
    continue
  }
  const proxied = task.humanReview && AUTO_HUMAN
  if (proxied) log(`🤖 ${task.id} supervised, but delegated (autoHumanReview): the agent does the real verification`)

  const r = await implementTaskBounded(task, proxied)
  results.push({ id: task.id, status: r.status, detail: r.detail, commits: r.commits || [] })
  if (r.status === 'implemented' || r.status === 'implemented-proxy') {
    log(`✔ ${task.id} implemented in ${r.steps} bounded step(s)${proxied ? ' (real verification by proxy)' : ''}`)
  } else {
    log(`⛔ ${task.id} ${r.status}: ${r.detail}`)
    failedIds.add(task.id)
  }
}

// ── Phase 2.5: GATE — verify the claim instead of believing it ──────────────
// Until here, "this task is done" is a boolean the implementing agent set on
// ITSELF. That is not a gate, it is a suggestion. One cheap agent asks the plan
// what is actually still open, and a contradiction stops the run before a
// reviewer is paid to audit work that was never finished.
//
// Deliberately ONE check per pass, not per task: inside the sandbox any
// verification costs a whole agent, and the per-task claim is already bounded by
// maxTaskSteps. This catches the failure that matters — a pass reported complete
// while the plan says otherwise.
const GATE_SCHEMA = {
  type: 'object',
  properties: {
    ready: { type: 'boolean' },
    reason: { type: 'string' },
    remaining: { type: 'number' },
    stillOpen: { type: 'array', items: { type: 'string' } },
    raw: { type: 'string' },
  },
  required: ['ready', 'reason', 'remaining', 'stillOpen', 'raw'],
  additionalProperties: false,
}

const RENDER_SCHEMA = {
  type: 'object',
  properties: {
    rendered: { type: 'boolean' },
    httpStatus: { type: 'number' },
    title: { type: 'string' },
    consoleErrors: { type: 'array', items: { type: 'string' } },
    evidence: { type: 'array', items: { type: 'string' } },
    notes: { type: 'string' },
  },
  required: ['rendered', 'httpStatus', 'title', 'consoleErrors', 'evidence', 'notes'],
  additionalProperties: false,
}

// Finding 5: teardown is the LOOP's job, never left to an agent's memory. A
// render agent that dies mid-step, or simply stops after starting the server,
// leaves a process group holding the port for the rest of this run and every
// later one — the exact "agents cannot be trusted with background server
// lifecycles" reasoning D2 is built on. `stop()` is idempotent (a missing
// pidfile reports stopped=false, never raises), so calling it unconditionally
// after the render agent returns — success, failure, or death — is always
// safe, including on the paths where the agent already stopped it itself.
// One derivation, two callers. The path was a magic string computed
// independently in buildRenderPrompt and runRender; they agreed only by
// coincidence, and a divergence would make teardown target a pidfile the agent
// never wrote — stopped=false, nothing alarming logged, server orphaned.
// Outside the worktree on purpose: a pidfile left inside the tree under review
// is an untracked file a later fix agent's `git add -A` would commit.
function renderPidfile(wd, tmpdir) {
  // A short hash of the FULL path, not just the sanitized name: sanitizing
  // every non-alphanumeric to '-' makes `wt-x` and `wt_x` collide, and two
  // concurrent loops on sibling worktrees would tear down each other's server.
  const path = String(wd)
  let h = 0
  for (let i = 0; i < path.length; i++) h = (h * 31 + path.charCodeAt(i)) | 0
  const tag = Math.abs(h).toString(36)
  return `${tmpdir || '/tmp'}/rein-render-${path.replace(/[^A-Za-z0-9]/g, '-').slice(-40)}-${tag}.pid`
}

async function stopRenderServer(pidfile) {
  try {
    await agentRetry(
      `Run EXACTLY this one command and report its JSON output; do NOT interpret, fix, retry, or run anything ` +
        `else: 'cd ${WD} && ${REIN} serve-probe --stop --pidfile ${pidfile}'. It is idempotent — if nothing is ` +
        `running it reports stopped=false, which is a normal, expected result, not a failure to fix.`,
      { schema: TASK_SCHEMA, label: 'render-stop', phase: 'Verify', agentType: 'general-purpose', effort: 'low', model: MODEL_AUX }
    )
  } catch (e) {
    log(`⚠️ render teardown agent failed (${e && e.message ? e.message : e}) — a server may still be holding the port`)
  }
}

// Finding 1: extracted so the SAME render step can be re-run after EVERY fix
// round, not only once in the Verify phase before the review loop starts. A
// render observed to fail is exactly as disqualifying as a red mechanical
// gate (T003), but unlike the gate — which the reviewer re-observes every
// round — a renderEvidence computed once and handed unchanged to every
// decideRound call would outlive its own round: a round-1 failure the fix
// agent genuinely repairs could never be approved, no matter how many rounds
// remained. Calling this again after each fix commits is what keeps
// renderEvidence bound to the round it describes.
async function runRender() {
  if (VERIFY_POLICY.mode !== 'rendered') return null
  const dispatch = decideRenderDispatch(VERIFY_POLICY, SERVE)
  const empty = { rendered: false, httpStatus: 0, title: '', consoleErrors: [], evidence: [], notes: '' }
  if (!dispatch.dispatch) {
    log(`⚠️ rendered-unverified: ${dispatch.reason} — Verify continues, nothing stops`)
    return { status: 'rendered-unverified', reason: dispatch.reason, ...empty }
  }
  const pidfile = renderPidfile(WD, ARGS.tmpdir)
  // agentRetry RETHROWS on its final attempt — it returns null only when the
  // agent dies without throwing. Without this try/finally a thrown render agent
  // skipped teardown entirely, orphaning a server that `serve-probe --start`
  // deliberately puts in its own session (immune to the CLI's own exit): the
  // port stays held for this run and every later one, which is precisely what
  // D2 exists to prevent. It also aborted the whole loop mid-Verify — no
  // review, no verdict, no Integrate — against D4's "never a hard stop".
  let render = null
  let threw = ''
  try {
    render = await agentRetry(
      buildRenderPrompt(REIN, SERVE.command, SERVE.url, VERIFY_POLICY.tools, WD, pidfile),
      { schema: RENDER_SCHEMA, label: 'render', phase: 'Verify', agentType: 'general-purpose', effort: 'low', model: MODEL_AUX }
    )
  } catch (e) {
    threw = e && e.message ? e.message : String(e)
  } finally {
    await stopRenderServer(pidfile)
  }
  if (threw) {
    log(`⚠️ rendered-unverified: the render agent threw (${threw})`)
    return { status: 'rendered-unverified', reason: `the render agent threw: ${threw}`, ...empty }
  }
  if (!render) {
    log(`⚠️ rendered-unverified: the render agent died`)
    return { status: 'rendered-unverified', reason: 'the render agent died', ...empty }
  }
  const outcome = decideRenderOutcome(render)
  if (outcome.failed) log(`⛔ render failed: ${outcome.reason}`)
  else log(`✔ render observed: HTTP ${render.httpStatus}, ${render.evidence.length} fact(s)`)
  return { status: outcome.failed ? 'failed' : 'passed', reason: outcome.reason, ...render }
}

let gateContradiction = ''
// D3: facts the loop can check, never a sentence. null in every mode other
// than 'rendered' (AC5: nothing changes for library, CLI or backend projects).
let renderEvidence = null
if (results.some((r) => r.status === 'implemented' || r.status === 'implemented-proxy')) {
  phase('Verify')
  const gate = await agentRetry(
    `You verify a claim. Implement NOTHING, edit NOTHING, commit NOTHING. ONE bash round-trip.\n` +
      `Run: 'cd ${WD} && ${REIN} next .' — it prints JSON and exits non-zero when nothing is claimable.\n` +
      `Report its fields verbatim: ready, reason, remaining. In 'stillOpen' put the ids of tasks the\n` +
      `plan still shows as PENDING (from '${REIN} tasks .'), and in 'raw' the literal JSON of next.\n` +
      `Do NOT interpret, do NOT fix anything, do NOT tick any checkbox. Report what the command said.`,
    { schema: GATE_SCHEMA, label: 'verify-gate', phase: 'Verify', agentType: 'general-purpose', effort: 'low', model: MODEL_AUX }
  )
  if (gate) {
    const claimed = new Set(results.filter((r) => r.status.startsWith('implemented')).map((r) => r.id))
    const lying = (gate.stillOpen || []).filter((id) => claimed.has(id))
    if (lying.length) {
      gateContradiction =
        `tasks reported implemented but still open in the plan: ${lying.join(', ')} ` +
        `(gate: ${gate.reason || 'ready=' + gate.ready})`
      log(`⛔ ${gateContradiction}`)
    } else {
      log(`✔ gate: ${gate.remaining} task(s) still pending, none of them claimed as done this run`)
    }
  } else {
    log(`⚠️ gate agent died — proceeding, but the pass-level claim is unverified`)
  }

  // ── Render (mode: 'rendered' only, additive) — the ONE extra agent D1 asks
  // for, dispatched with the SAME phase('Verify') as the gate above. Re-run
  // (not just read) inside the review round loop below after every fix round
  // — see runRender's own comment (finding 1).
  if (VERIFY_POLICY.mode === 'rendered') {
    renderEvidence = await runRender()
  }
}

// ── Phase 3: REVIEW — the whole change, by someone who wrote none of it ──────
phase('Review')

const implemented = results.filter((r) => r.status === 'implemented' || r.status === 'implemented-proxy')
const incomplete = results.filter((r) => r.status !== 'implemented' && r.status !== 'implemented-proxy')

let approved = false
let roundsUsed = 0
let lastFindings = []
let lastVerdict = ''
let gateOutput = ''
let needsHumanDecision = false
let humanDecisionReason = ''

if (gateContradiction) {
  // A reviewer auditing work the plan says was never finished is a wasted round,
  // and approving it would launder the false claim.
  lastVerdict = `review NOT run: ${gateContradiction}`
  log(`⏭ ${lastVerdict}`)
} else if (incomplete.length) {
  // Reviewing an incomplete change burns a round on a foregone CHANGES_REQUESTED.
  lastVerdict = `review NOT run: tasks still incomplete (${incomplete.map((r) => `${r.id}:${r.status}`).join(', ')})`
  log(`⏭ ${lastVerdict}`)
} else if (!implemented.length) {
  lastVerdict = 'review NOT run: nothing was implemented'
  log(`⏭ ${lastVerdict}`)
} else {
  let reviewerDeaths = 0
  let round = 1
  while (round <= ROUNDS) {
    roundsUsed = round
    let review
    try {
      review = await agent(
        `${CTX}\n\nYou are the REVIEWER and you implemented NONE of this. No agent approves its own work — ` +
          `that rule exists because a flow without an independent gate shipped defects to the user.\n` +
          `Audit the COMPLETE change (tasks ${implemented.map((r) => r.id).join(', ')}), not one task. Round ${round} of ${ROUNDS}.\n` +
          (ctx.why ? `\nWHY this change exists (judge the work against THIS, not only against the criteria): ${ctx.why}\n` : '\n') +
          (ctx.scopeOut && ctx.scopeOut.length
            ? `Explicitly OUT of scope: ${ctx.scopeOut.join('; ')}. Work that strays into it is a finding, ` +
              `even if it is good work.\n`
            : '') +
          `\n` +
          `1. MECHANICAL GATE FIRST. Run ${gateCmds.length ? gateCmds.join(', ') : 'the project verification commands (none are configured — say so)'} ` +
          `and put their LITERAL output (trimmed to what matters) in 'gateOutput', with gateGreen set honestly. ` +
          `If anything is red the verdict CANNOT be APPROVED, however good the code reads.\n` +
          reviewerPolicyBlock() +
          (renderEvidence && renderEvidence.status === 'failed'
            ? `RENDER EVIDENCE (already gathered this Verify phase, do not re-run): rendered=${renderEvidence.rendered}, ` +
              `httpStatus=${renderEvidence.httpStatus}, title=${JSON.stringify(renderEvidence.title || '')}, ` +
              `evidence=${JSON.stringify(renderEvidence.evidence)}, ` +
              `consoleErrors=${JSON.stringify(renderEvidence.consoleErrors || [])} — failed: ` +
              `${renderEvidence.reason}. Treat this as a finding: this change's render is FAILED, not passed.\n`
            : renderEvidence && renderEvidence.status === 'rendered-unverified'
            ? `RENDER EVIDENCE: no render tool was reachable this Verify phase (${renderEvidence.reason}). This is an ` +
              `INCOMPLETE gate, not a failed one — 'we could not look' is a different fact from 'we looked and it broke'. ` +
              `State it as 'rendered-unverified' rather than a defect; it does not by itself block APPROVED (D4).\n`
            : renderEvidence && renderEvidence.status === 'passed'
            ? `RENDER EVIDENCE (already gathered this Verify phase, do not re-run): rendered=true, ` +
              `httpStatus=${renderEvidence.httpStatus}, title=${JSON.stringify(renderEvidence.title || '')}, ` +
              `evidence=${JSON.stringify(renderEvidence.evidence || [])}, ` +
              `consoleErrors=${JSON.stringify(renderEvidence.consoleErrors || [])}. The render requirement is ` +
              `satisfied, but JUDGE THESE FACTS — a page can serve 200 with a title and still throw uncaught ` +
              `errors and paint blank. Non-empty consoleErrors is a finding even though the render passed.\n`
            : '') +
          `2. JUDGEMENT over the full diff ('git -C ${WD} diff ${BASE}...${BRANCH}' or the run's commits), on five ` +
          `axes: correctness, readability, architecture, security, performance. Look at the change AS A WHOLE — ` +
          `coherence defects BETWEEN tasks are the ones nobody else will ever see.\n` +
          `3. COVERAGE: verify each task in ${ctx.planPath} genuinely meets its acceptance criteria, and that no ` +
          `checkbox was ticked without them being met.\n\n` +
          `Tag every finding with a severity: BLOCKING (a real defect — repeats the round), IMPORTANT (worth fixing, ` +
          `travels to the fix agent only if a round happens anyway), or SUGGESTION (recorded and reported, never ` +
          `costs a round on its own). CHANGES_REQUESTED requires at least one BLOCKING finding; APPROVED tolerates ` +
          `none — do not return either verdict without matching findings.\n\n` +
          `ESCALATION: if the ONLY thing blocking approval is a judgement solely the owner can give (a supervised ` +
          `task whose acceptance is "the owner confirms", closed by proxy without their real verdict), do NOT ` +
          `return CHANGES_REQUESTED — the implementer cannot fix it, so another round is wasted time. Return ` +
          `needsHumanDecision=true naming WHICH task and WHAT they must judge. Code can be complete and green and ` +
          `still need that sign-off; that is not an implementer defect. (Defects the implementer CAN fix still go ` +
          `as normal findings.)\n\n` +
          `If you find fixable defects: verdict CHANGES_REQUESTED with PRECISE, actionable findings (file, what is ` +
          `wrong, what is missing) — they will be fixed without talking to you. Be demanding: approving something ` +
          `broken is worse than asking for another round.\n` +
          `Do NOT modify product code: doing so would make your own review stale.`,
        { schema: REVIEW_SCHEMA, label: `review#${round}`, phase: 'Review', agentType: 'agent-skills:code-reviewer', model: MODEL_REVIEW }
      )
    } catch (e) {
      review = null
      log(`reviewer threw: ${e && e.message ? e.message : e}`)
    }

    if (!review) {
      // A dead reviewer does not consume a round, but is not retried forever.
      reviewerDeaths++
      if (reviewerDeaths >= 2) {
        lastVerdict = 'the reviewer died twice — change not approved'
        log(`⛔ ${lastVerdict}`)
        break
      }
      log(`reviewer died in round ${round}, retrying without consuming a round`)
      continue
    }

    lastVerdict = review.verdict || ''
    gateOutput = review.gateOutput || gateOutput

    const decision = decideRound(review, round, ROUNDS, renderEvidence)
    lastFindings = decision.findings || []
    // D4: 'rendered-unverified' never overrides anything decideRound did above
    // — it is carried through untouched so the operator sees "we could not
    // look" and not a silent pass dressed up as a normal approval/fix.
    const renderNote = decision.renderUnverified
      ? ` [render: unverified — ${renderEvidence && renderEvidence.reason ? renderEvidence.reason : 'no browser tool reachable'}]`
      : ''

    if (decision.decision === 'escalate') {
      needsHumanDecision = true
      humanDecisionReason = decision.humanDecisionReason
      lastVerdict = `escalated to the owner: ${humanDecisionReason}${renderNote}`
      log(`✋ the reviewer ESCALATES (the implementer cannot resolve it) — ${humanDecisionReason}`)
      break
    }

    if (decision.decision === 'approve') {
      approved = true
      if (decision.overridden) {
        // Symmetric to the red-gate override below: D2 says CHANGES_REQUESTED
        // needs a BLOCKING finding, so a bare CHANGES_REQUESTED with none is
        // overridden rather than trusted at face value.
        lastVerdict = `APPROVED (loop override: gate green, zero BLOCKING findings)${renderNote}`
        log(`⚠️ reviewer said CHANGES_REQUESTED but gate is green with zero BLOCKING findings — overriding to APPROVED`)
      } else {
        lastVerdict = `${lastVerdict}${renderNote}`
        log(`✅ APPROVED in round ${round}`)
      }
      if (renderNote) log(`⚠️ rendered-unverified carried to the operator: approval stands, but the render gate was incomplete`)
      break
    }

    if (decision.decision === 'reject') {
      lastVerdict = `CHANGES_REQUESTED (${decision.reason}) — no review rounds remain${renderNote}`
      log(`⛔ round ${round}: ${decision.reason}, and no rounds remain`)
      break
    }

    // decision.decision === 'fix'
    if (review.approved && !review.gateGreen) {
      // Approving with a red gate is exactly the false green this loop exists to
      // prevent, so the loop overrides the reviewer rather than trusting it.
      log(`⚠️ reviewer said APPROVED with a RED gate — overriding to CHANGES_REQUESTED`)
      lastVerdict = 'CHANGES_REQUESTED (loop override: approved with a red gate)'
    } else if (review.approved && renderEvidence && renderEvidence.status === 'failed') {
      // T003: symmetric override — a green gate does not save an APPROVED
      // verdict from a render that was actually observed to fail.
      log(`⚠️ reviewer said APPROVED but the render failed — overriding to CHANGES_REQUESTED`)
      lastVerdict = 'CHANGES_REQUESTED (loop override: approved but the render failed)'
    }
    lastFindings = buildFixFindings(review, decision)
    log(`↻ round ${round}: CHANGES_REQUESTED with ${lastFindings.length} BLOCKING/IMPORTANT finding(s) (SUGGESTIONs excluded) -> back to the implementer`)

    let fix
    try {
      fix = await agent(
        `${CTX}\n\nYou are the IMPLEMENTER. The reviewer did NOT approve the change (round ${round}).\n` +
          `Tasks implemented in this run: ${implemented.map((r) => r.id).join(', ')}\n` +
          `Fix THESE findings, with tests, leaving the affected tasks' verification green` +
          (gateCmds.length ? ` (plus ${gateCmds.join(' and ')})` : '') + `, and commit:\n` +
          lastFindings.map((f, i) => `${i + 1}. [${f.severity}] ${f.text}`).join('\n') +
          `\nWork FOCUSED with BOUNDED verification (one test/file, NOT the whole suite, no full output dumps): ` +
          `the loop's cost is re-read context — do not inflate yours.\n` +
          `If a finding seems wrong, do NOT ignore it silently: fix the rest and explain in the summary why that ` +
          `one does not apply, with evidence — the reviewer will judge.\n` +
          `Report the new SHAs in "commits".\n\n${INTEGRITY}`,
        { schema: TASK_SCHEMA, label: `fix#${round}`, phase: 'Review', agentType: 'general-purpose', model: MODEL_IMPL }
      )
    } catch (e) {
      fix = null
      log(`the fix agent threw: ${e && e.message ? e.message : e}`)
    }
    if (!fix || fix.blocked) {
      log(`⛔ fix blocked: ${fix ? fix.blockedReason : 'the agent died'}`)
      break
    }
    // Finding 1: re-check the render against what the fix agent just
    // committed, BEFORE the next round's decideRound call — a renderEvidence
    // left over from an earlier round describes a tree that no longer
    // exists once the fix agent commits, and must never outlive it.
    if (VERIFY_POLICY.mode === 'rendered') {
      renderEvidence = await runRender()
    }
    round++
  }

  if (!approved && !needsHumanDecision) log(`⚠️ finished WITHOUT approval after ${roundsUsed} round(s)`)
}

// ── Phase 4: INTEGRATE — merge only approved work ───────────────────────────
let merged = false
if (WORKTREE_MODE) {
  if (approved) {
    phase('Integrate')
    const integ = await agent(
      `Integrate the APPROVED change into ${BASE}. You work from ${ctx.root}.\n` +
        `1. 'cd ${ctx.root}' and 'git checkout ${BASE}'.\n` +
        `2. 'git merge --no-ff ${BRANCH} -m "merge(${LABEL}): approved by the rein loop"'. On conflict, resolve in ` +
        `   favour of the branch for files this change owns; keep both for additive plan/spec files. Do NOT force ` +
        `   a resolution you do not understand — report it instead.\n` +
        (ctx.cmdTest ? `3. Verify green on ${BASE} after the merge: '${ctx.cmdTest}' (ONCE).\n` : '') +
        `4. Clean up: 'git -C ${ctx.root} worktree remove ${WD}' (or --force if dirty) and ` +
        `   'git -C ${ctx.root} branch -d ${BRANCH}' if it merged cleanly.\n` +
        `Report whether it merged cleanly and any conflict you touched.`,
      { schema: TASK_SCHEMA, label: 'integrate', phase: 'Integrate', agentType: 'general-purpose', model: MODEL_IMPL }
    )
    merged = !!(integ && !integ.blocked && integ.done)
    if (merged) log(`🔀 merged into ${BASE}; worktree ${WD} removed`)
    else log(`⚠️ merge did not complete: ${integ ? integ.blockedReason || integ.summary : 'agent died'} — ${WD} (${BRANCH}) kept for inspection`)
  } else if (needsHumanDecision) {
    log(`✋ waiting on YOUR verdict (${humanDecisionReason}): ${WD} (${BRANCH}) is ready to inspect — the code is complete, the judgement is yours`)
  } else {
    log(`🌿 not approved: ${WD} (${BRANCH}) left intact — unapproved work is never merged`)
  }
}

return {
  ok: true,
  change: CHANGE,
  planPath: ctx.planPath,
  stack: ctx.stack,
  approved,
  worktree: WORKTREE_MODE ? WD : null,
  branch: WORKTREE_MODE ? BRANCH : BASE,
  merged: WORKTREE_MODE ? merged : true,
  reviewRounds: roundsUsed,
  verdict: lastVerdict,
  needsHumanDecision,
  humanDecisionReason: needsHumanDecision ? humanDecisionReason : '',
  gateOutput,
  gateContradiction,
  implemented: implemented.map((r) => r.id),
  implementedByProxy: results.filter((r) => r.status === 'implemented-proxy').map((r) => r.id),
  needsHuman: results.filter((r) => r.status === 'needs-human').map((r) => r.id),
  problems: incomplete.filter((r) => r.status !== 'needs-human'),
  // Non-blocking observations survive an approval too — AC5, D1: SUGGESTION/IMPORTANT
  // findings on an approved run are still reported, never silently dropped.
  openFindings: lastFindings,
  // Non-blocking plan-check findings (D4's continue half): logged above and
  // carried here so nothing the plan-checker noticed is silently dropped.
  planFindings,
  // D3/AC4: facts the loop can check, carried whatever the verdict — null in
  // every mode other than 'rendered'.
  renderEvidence,
  // Measure the run: turns/agent, ctx_max/turn and Opus share are what predict cost.
  measure: `${REIN} token-report`,
}
