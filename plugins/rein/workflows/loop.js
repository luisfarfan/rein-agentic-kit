export const meta = {
  name: 'rein-loop',
  description: 'Rein change loop -- PHASE 0 STUB: resolves config + stack only, implements nothing',
  whenToUse:
    'Phase 0 plumbing probe. Verifies that a plugin-shipped workflow resolves, that flow.config.json is read, and that the stack + commands resolve correctly. The real bounded implement/review loop lands in phase 1.',
  phases: [{ title: 'Resolve' }],
}

// ─────────────────────────────────────────────────────────────────────────────
// PHASE 0 STUB -- deliberately does NOT implement anything.
//
// What it proves (acceptance criteria A2 and A4):
//   A2  a workflow shipped inside a plugin is resolvable and runs
//   A4  the run resolves flow.config.json + stack + commands from the CONSUMING
//       project, not from this repo
//
// Design constraint that shapes everything here: workflow scripts run in a
// sandbox with NO filesystem and NO Node APIs. Every filesystem fact has to come
// back through an agent. That is why resolution is a `rein detect` call made by
// one cheap agent instead of `require('fs')`.
//
// And why `rein detect` exists at all rather than letting the agent poke around:
// the bill of this whole system is context re-reads (~90% cache_read, measured).
// An agent that greps to rediscover "pnpm or npm, vitest or jest" spends turns on
// something deterministic. One bash round-trip replaces the exploration.
// ─────────────────────────────────────────────────────────────────────────────

// args can arrive as an object or as a JSON string depending on the caller --
// accepting both keeps a run from dying at startup.
let ARGS = args
if (typeof ARGS === 'string') {
  try {
    ARGS = JSON.parse(ARGS)
  } catch {
    ARGS = { change: ARGS }
  }
}
ARGS = ARGS || {}

// Model routing, per-agent (lever #2). On a subscription this does not lower the
// bill -- it frees the scarce Opus quota. Mechanical work does not need Opus.
const MODEL_AUX = ARGS.modelAux || 'haiku'

const RESOLVE_SCHEMA = {
  type: 'object',
  properties: {
    ok: { type: 'boolean' },
    reinOnPath: { type: 'boolean' },
    invokedAs: { type: 'string' }, // exact command that worked -- answers A3
    cwd: { type: 'string' },
    configFound: { type: 'boolean' },
    stack: { type: 'string' },
    subtypes: { type: 'array', items: { type: 'string' } },
    taskRunner: { type: 'string' },
    commands: { type: 'array', items: { type: 'string' } }, // "slot=cmd [source]"
    missingCommands: { type: 'array', items: { type: 'string' } },
    planSource: { type: 'string' },
    tracker: { type: 'string' },
    capabilities: { type: 'array', items: { type: 'string' } },
    notes: { type: 'string' },
  },
  required: [
    'ok',
    'reinOnPath',
    'invokedAs',
    'cwd',
    'configFound',
    'stack',
    'subtypes',
    'taskRunner',
    'commands',
    'missingCommands',
    'planSource',
    'tracker',
    'capabilities',
    'notes',
  ],
  additionalProperties: false,
}

phase('Resolve')

const resolved = await agent(
  `You are a PROBE for the "rein" plugin, phase 0. Implement NOTHING, edit NOTHING, commit NOTHING.\n` +
    `Your entire job is ONE bash round-trip plus reporting. Do not explore the repo.\n\n` +
    `1. Print the working directory: 'pwd'.\n` +
    `2. Resolve the project's stack and commands. Try these IN ORDER and stop at the first that works:\n` +
    `   a) 'rein detect'                      (tells us the plugin's bin/ is on PATH)\n` +
    `   b) '"$CLAUDE_PLUGIN_ROOT"/bin/rein detect'\n` +
    `   c) 'python3 "$CLAUDE_PLUGIN_ROOT"/lib/detect.py'\n` +
    `   Report in 'invokedAs' the EXACT command that worked, and set reinOnPath=true only if (a) worked.\n` +
    `   The output is JSON -- read it, do not re-derive any of it yourself.\n` +
    `3. Report its fields back: configFound, stack, subtypes, taskRunner, planSource ('plan.source'),\n` +
    `   tracker ('tracker.kind'), capabilities, missingCommands. For 'commands', emit one string per\n` +
    `   slot in the form "slot=command [source]" using the matching entry of 'commandSources'.\n` +
    `4. In 'notes': anything that looks wrong -- a stack that does not match what you can see, a\n` +
    `   command that would obviously fail, or all three invocations failing (then ok=false and say why).\n\n` +
    `Do not run the resolved test/lint/build commands. This is a probe, not a build.`,
  {
    schema: RESOLVE_SCHEMA,
    label: 'resolve-config',
    phase: 'Resolve',
    agentType: 'general-purpose',
    effort: 'low',
    model: MODEL_AUX,
  }
)

if (!resolved) {
  log('probe agent died -- phase 0 A4 not verified')
  return { phase: 0, ok: false, reason: 'probe agent died' }
}

log(`stack: ${resolved.stack}${resolved.subtypes.length ? ` (${resolved.subtypes.join(', ')})` : ''}`)
log(`invoked as: ${resolved.invokedAs}  |  rein on PATH: ${resolved.reinOnPath}`)
log(`commands: ${resolved.commands.join(' · ') || '(none resolved)'}`)
if (resolved.missingCommands.length) log(`missing: ${resolved.missingCommands.join(', ')}`)

return {
  phase: 0,
  stub: true,
  ...resolved,
  // What phase 1 replaces this with.
  next: 'phase 1: bounded fresh-agent implement loop + review rounds, driven by these commands',
}
