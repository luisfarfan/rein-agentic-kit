export const meta = {
  name: 'rein-loop',
  description: "Execute a planned change: bounded fresh-agent implementation steps, then an independent review gate, driven by the project's flow.config.json",
  whenToUse:
    'To execute an already-planned change. Every task is implemented as a bounded loop of short, FRESH agents handing off a compact ledger; then a reviewer that wrote none of it audits the change as a whole, and its findings return to the implementer until approved. Pass {change} for openspec plans; tasks.md needs no argument.',
  phases: [
    { title: 'Prepare' },
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
    'verifyWarnings',
    'maxTaskSteps', 'maxReviewRounds', 'modelAux', 'modelImpl', 'modelReview', 'tasks', 'problem',
  ],
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
    findings: { type: 'array', items: { type: 'string' } },
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
    `3. Report it back, mapping fields exactly:\n` +
    `   · cmdTest/cmdTestOne/cmdLint/cmdTypecheck  <- config.commands.{test,testOne,lint,typecheck}\n` +
    `     (empty string when a slot is absent — say so rather than inventing a command)\n` +
    `   · verifyPolicy <- config.verifyPolicy, serve <- config.serve when present, else {command:"",url:""}\n` +
    `     (non-frontend projects have none). Copy both LITERALLY, do not re-derive them.\n` +
    `   · verifyWarnings <- config.verifyWarnings, or [] when the key is absent. These say the policy\n` +
    `     cannot be satisfied as detected (no browser tool reachable, a guessed URL); the run surfaces\n` +
    `     them so a wrong instruction is visible instead of silently followed.\n` +
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

// ── Shared prompt fragments ─────────────────────────────────────────────────

const hasGraph = (ctx.capabilities || []).includes('graphify-index')
const RETRIEVAL =
  `RETRIEVAL — do not burn context. The real cost is cache_read: every turn re-reads everything ` +
  `accumulated so far, so cost ≈ context-size × turns.\n` +
  (hasGraph
    ? `  · Orient with the graph BEFORE grepping or opening whole files: 'graphify query "<what you need>"' ` +
      `(a bounded subgraph, far smaller than a raw grep), 'graphify path "<A>" "<B>"', 'graphify explain "<concept>"'.\n`
    : `  · Locate with bounded search (grep with a concrete path and pattern) before opening files.\n`) +
  `  · Read ONLY the symbols/regions you will touch (Read with offset/limit), NEVER whole files "just in case" — ` +
  `every large file you pull in is RE-READ on every later turn.\n` +
  `  · Keep command output small: filter and scope it (head, -q, concrete paths) instead of dumping everything.\n` +
  `  · Aim to finish in FEW turns with precise reads, not to explore incrementally.`

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

function boundedVerification(task) {
  if (task.verification) return `'${task.verification}'`
  if (ctx.cmdTestOne) return `the narrowest form of '${ctx.cmdTestOne}' (substitute {target} with the one test file or id covering your change)`
  if (ctx.cmdTest) return `'${ctx.cmdTest}'`
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
      `RENDERED VERIFICATION REQUIRED (mode: rendered): ${VERIFY_POLICY.requires.join('; ')}. ` +
      `A green ${ctx.stack} test suite does NOT make this task done by itself. Serve it — ` +
      `${SERVE.command || '(no serve command is configured; say so rather than inventing one)'}` +
      `${SERVE.url ? ` at ${SERVE.url}` : ''} — and render it` +
      `${VERIFY_POLICY.tools.length ? ` (available: ${VERIFY_POLICY.tools.join(', ')})` : ''}. Record what you ` +
      `OBSERVED — not just that the test suite passed — in 'verification'.\n`
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
      `This project's policy is 'rendered': the mechanical gate is INCOMPLETE without observed render evidence — ` +
      `a green test suite with no actual page render is NOT grounds for approval. Confirm the implementer recorded ` +
      `what was served${SERVE.url ? ` (${SERVE.url})` : ''} and rendered, not just that tests passed; if it did not, ` +
      `that alone is a CHANGES_REQUESTED finding.\n`
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

const closeCmd =
  ctx.tracker === 'beads'
    ? (id) => `'bd close ${id} --reason "<what landed>"' and '${REIN} close ${id} --root ${WD}'`
    : (id) => `'${REIN} close ${id} --root ${WD}' (ticks the checkbox deterministically — do not hand-edit the plan)`

// ── Phase 1.5: ISOLATE — one worktree + branch per run ──────────────────────
// Running loops in parallel is then safe with no per-run decision, and an
// unapproved run leaves an ugly branch rather than a broken base branch.
if (WORKTREE_MODE) {
  phase('Isolate')
  const setup = await agentRetry(
    `You work in ${ctx.root} (the ${BASE} tree). Prepare ISOLATION for this run. Implement NOTHING.\n` +
      `1. If ${ctx.root} has UNCOMMITTED work ('git status --porcelain'), mention it in the summary ` +
      `   (another loop may be active) — but continue: the worktree is cut from the committed HEAD.\n` +
      `2. Create it: 'git -C ${ctx.root} worktree add ${WD} -b ${BRANCH} 2>/dev/null || ` +
      `   git -C ${ctx.root} worktree add ${WD} ${BRANCH}' (reuse the branch if it exists). If ${WD} already ` +
      `   exists and belongs to this change, reuse it — do not fail.\n` +
      `3. Verify: 'git -C ${WD} rev-parse --abbrev-ref HEAD' reports ${BRANCH}. Set done=true only then.`,
    { schema: TASK_SCHEMA, label: 'isolate', phase: 'Isolate', agentType: 'general-purpose', effort: 'low', model: MODEL_AUX }
  )
  if (!setup || setup.blocked || !setup.done) {
    const why = setup ? setup.blockedReason || setup.summary : 'the agent died'
    log(`⛔ could not create worktree ${WD}: ${why} — aborting`)
    return { ok: false, phase: 'Isolate', problem: why, worktree: WD, branch: BRANCH }
  }
  log(`🌿 isolated in ${WD} (branch ${BRANCH})`)
}

// ── Phase 1.7: MAP — one cheap scout, so N implementers do not each explore ──
// A HINT, never a contract: if the scout dies the map is empty and implementers
// explore exactly as they would have.
const codeMapById = {}
if (hasGraph) {
  phase('Map')
  try {
    const scout = await agent(
      `You work in ${ctx.root}. You are the SCOUT: implement NOTHING, commit NOTHING. Build a small CODE MAP ` +
        `per task so implementers do not explore from zero — the loop's real cost is re-read context, so less ` +
        `exploration = fewer turns.\n` +
        (artifactList || `Read ${ctx.planPath} ONCE.\n`) +
        `For EACH of [${tasks.map((t) => t.id).join(', ')}] use THE GRAPH, not grep or whole-file reads: ` +
        `'graphify query "<what the task touches>"', 'graphify path "<A>" "<B>"', 'graphify explain "<concept>"'.\n` +
        `Return per task: 'touchpoints' (a SHORT list of "path:symbol" — a hint, not a dump) and 'orientation' ` +
        `(one line: where to start). If unsure about a task, return empty touchpoints and say so — it is a ` +
        `STARTING POINT, not a contract.`,
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
    `NARROWEST verification that covers your change — ${boundedVerification(task)} — NEVER the full suite, ` +
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

let gateContradiction = ''
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
          `2. JUDGEMENT over the full diff ('git -C ${WD} diff ${BASE}...${BRANCH}' or the run's commits), on five ` +
          `axes: correctness, readability, architecture, security, performance. Look at the change AS A WHOLE — ` +
          `coherence defects BETWEEN tasks are the ones nobody else will ever see.\n` +
          `3. COVERAGE: verify each task in ${ctx.planPath} genuinely meets its acceptance criteria, and that no ` +
          `checkbox was ticked without them being met.\n\n` +
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

    lastFindings = review.findings || []
    lastVerdict = review.verdict || ''
    gateOutput = review.gateOutput || gateOutput

    if (review.needsHumanDecision) {
      needsHumanDecision = true
      humanDecisionReason = review.humanDecisionReason || 'a supervised task needs your verdict'
      lastVerdict = `escalated to the owner: ${humanDecisionReason}`
      log(`✋ the reviewer ESCALATES (the implementer cannot resolve it) — ${humanDecisionReason}`)
      break
    }

    if (review.approved && review.gateGreen) {
      approved = true
      log(`✅ APPROVED in round ${round}`)
      break
    }
    if (review.approved && !review.gateGreen) {
      // Approving with a red gate is exactly the false green this loop exists to
      // prevent, so the loop overrides the reviewer rather than trusting it.
      log(`⚠️ reviewer said APPROVED with a RED gate — overriding to CHANGES_REQUESTED`)
      lastVerdict = 'CHANGES_REQUESTED (loop override: approved with a red gate)'
      lastFindings = ['the mechanical gate is red; it must be green before approval', ...lastFindings]
    }

    log(`↻ round ${round}: CHANGES_REQUESTED with ${lastFindings.length} finding(s) -> back to the implementer`)
    if (round === ROUNDS) break

    let fix
    try {
      fix = await agent(
        `${CTX}\n\nYou are the IMPLEMENTER. The reviewer did NOT approve the change (round ${round}).\n` +
          `Tasks implemented in this run: ${implemented.map((r) => r.id).join(', ')}\n` +
          `Fix THESE findings, with tests, leaving the affected tasks' verification green` +
          (gateCmds.length ? ` (plus ${gateCmds.join(' and ')})` : '') + `, and commit:\n` +
          lastFindings.map((f, i) => `${i + 1}. ${f}`).join('\n') +
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
  openFindings: approved ? [] : lastFindings,
  // Measure the run: turns/agent, ctx_max/turn and Opus share are what predict cost.
  measure: `${REIN} token-report`,
}
