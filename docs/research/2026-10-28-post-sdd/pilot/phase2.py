#!/usr/bin/env python3
"""Phase 2 (FASE2.md): arms N1, N2, P2 and A on the screen's tasks, with the screen's sandbox,
environment and scoring (screen_run.py), none of which changes.

  phase2.py plan <cid> <rep>          the plan P2-rK and A-rK share (Opus, sandboxed, from the intent)
  phase2.py run <cid> <arm> <rep>     one run of n1, n2 or p2
  phase2.py runa <cid> <rep>          one run of A: `rein ship` through Orca, scripted owner
  phase2.py score <run-id>            final state, and the pre-audit state when a fix round changed it
  phase2.py launch <batch> [--dry]    a batch: 2 at a time (A alone), quota/budget/STOP checks, serial scoring
  phase2.py freeze | table
"""
import glob, json, os, random, re, shlex, shutil, subprocess, sys, threading, time, uuid, queue

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
import screen as S          # noqa: E402
import screen_run as SR     # noqa: E402
import harness as H         # noqa: E402

CFG = S.CFG
ROOT = S.ROOT
RUNS = os.path.join(os.path.dirname(ROOT), "phase2-runs")
SR.RUNS = RUNS              # run_paths, sandbox paths, agent_env, score and spent all read it
STOP = SR.STOP
HOME = SR.HOME
KIT = glob.glob(HOME + "/.local/share/uv/tools/rein-agentic-kit/lib/python3*/site-packages/rein_kit")[0]
SEED = 20261029
PLANNER, AUDITOR = "claude-opus-5-5", "gpt-6.1-sol"
HARD = ["mcp-ops-core", "client-errors", "cpe-admin", "pos-availability", "new-errors", "storefront-tolerance"]
EASY = ["widget", "sale-note-pos"]          # random.Random(20261029).sample(sorted(easy), 2)
BATCHES = {1: [(c, 1) for c in ("widget", "mcp-ops-core", "new-errors")]}
BATCHES[2] = [(c, r) for c in HARD + EASY for r in (1, 2) if (c, r) not in BATCHES[1]]
ARMS = ("n1", "n2", "p2", "a")
BUDGET = {1: 130.0}
WEEKLY_STOP, SESSION_PAUSE, CODEX_STOP = 72, 70, 60
PER_SESSION_USD, PER_PHASE_MIN = 8.0, 60
PLAN_USD, PLAN_MIN, A_MIN = 10.0, 30, 120
# REIN's own history: audits and runs of the very changes under test (FASE2 §5)
REIN_DENY = [HOME + "/.claude/rein/audit-reports", HOME + "/.claude/rein/runs.jsonl", HOME + "/.claude/rein/events.jsonl"]
CODEX_DENY = [HOME + "/.codex/sessions", HOME + "/.codex/archived_sessions", HOME + "/.codex/history.jsonl",
              HOME + "/.codex/session_index.jsonl"]

PLAN_PROMPT = """You are the planner for a change in this workspace. Plan it with Rein's planning procedure, which is the file below. Never implement anything.

The owner has confirmed in advance and will not answer questions: decide within the request, record each assumption as a Decision, skip the dry-run confirmation of step 7 and write the plan. Change name: `{change}`. Repos: {repos}. Write the plan to `{target}`, then run step 8's `rein tasks` check and fix what it reports. Do not run /ship.

--- Rein planning procedure (skills/rein-plan/SKILL.md) ---
{skill}

--- The request ---
{intent}"""


def rid(cid, arm, rep):
    return "%s-%s-r%d" % (cid, arm, rep)


def parse_rid(run_id):
    m = re.match(r"^(.+)-(n1|n2|p2|a|plan)-r(\d+)(-pre)?$", run_id)
    return m.group(1), m.group(2), int(m.group(3))


def ev(meta, kind, **kw):
    SR.ev(meta, kind, **kw)


def quotas():
    q = H.quota()
    out = {}
    for p in ("claude", "codex"):
        try:
            c = q["result"]["rateLimits"][p]
            out[p] = dict(session=c["session"]["usedPercent"], weekly=c["weekly"]["usedPercent"])
        except (KeyError, TypeError):
            out[p] = dict(session=None, weekly=None)
    return out


def forbidden_rx(run_id):
    cid, _, _ = parse_rid(run_id)
    change = re.escape(CFG["candidates"][cid]["change"])
    base = [x for x in H.FORBIDDEN if x != r"openspec/changes/archive"]
    extra = [r"rein-pilot/screen/", r"rein-pilot/screen-runs/", r"rein-pilot/phase2-runs/(?!%s)" % re.escape(run_id),
             r"/Developer/sandbox/rein-pilot/fixtures", r"\.claude/rein/(audit-reports|runs\.jsonl|events\.jsonl)",
             r"openspec/changes/archive\S*%s" % change]
    return re.compile("|".join(base + extra))


# ---------------------------------------------------------------- sandbox
def profile(meta, run_id, sid, codex=False):
    """The screen's profile (CRIBA §15), plus REIN's history denied. Codex's own home is allowed
    except its past sessions and history."""
    base, _ = SR.run_paths(run_id)
    q = lambda p: '"%s"' % p.replace("\\", "\\\\").replace('"', '\\"')
    rx = lambda p: '#"%s"' % p
    own = re.escape(SR.slug(base))
    deny = [d for d in SR.SANDBOX_DENY if not (codex and d == HOME + "/.codex")]
    lines = ["(version 1)", "(allow default)",
             "(deny file-read* file-write* %s)" % " ".join("(subpath %s)" % q(p) for p in deny),
             "(deny file-read* file-write* (regex %s))" % rx("^%s/-" % re.escape(SR.TMP_CLAUDE)),
             "(allow file-read* file-write* (subpath %s))" % q(base),
             "(deny file-write* (subpath %s))" % q(os.path.join(base, "meta")),
             "(allow file-read* (subpath %s))" % q(S.STUB),
             "(allow file-read* %s)" % " ".join("(literal %s)" % q(p) for p in SR.SANDBOX_HOOK_FILES),
             "(allow file-read* file-write* (regex %s) (regex %s))" % (
                 rx("^%s/\\.claude/projects/%s(-|/|$)" % (re.escape(HOME), own)),
                 rx("^%s/%s(-|/|$)" % (re.escape(SR.TMP_CLAUDE), own))),
             "(allow file-read* file-write* (regex %s) (regex %s))" % (
                 rx("^%s/\\.claude/file-history/%s(/|$)" % (re.escape(HOME), sid)),
                 rx("^%s/\\.claude/todos/%s" % (re.escape(HOME), sid))),
             "(allow file-write* (literal %s))" % q(HOME + "/.claude/history.jsonl"),
             "(allow file-read-metadata %s)" % " ".join(
                 "(literal %s)" % q(p) for p in (HOME + "/Developer", HOME + "/Developer/sandbox",
                                                 os.path.dirname(ROOT), RUNS, HOME + "/.claude/projects", SR.TMP_CLAUDE)),
             "(deny file-read* %s)" % " ".join("(subpath %s)" % q(p) for p in REIN_DENY)]
    if codex:  # read nothing Codex kept from earlier sessions; it may still write its own
        lines.append("(deny file-read* %s)" % " ".join("(subpath %s)" % q(p) for p in CODEX_DENY))
        # stat only, so it can create its own rollout under sessions/YYYY/MM/DD; listing and reading stay denied
        lines.append("(allow file-read-metadata (subpath %s))" % q(HOME + "/.codex/sessions"))
    p = os.path.join(meta, "codex.sb" if codex else "sandbox.sb")
    open(p, "w").write("\n".join(lines) + "\n")
    return p


# ---------------------------------------------------------------- prepare
def prepare(run_id, cid, plan_root=False):
    """The screen's prepare (fixture clone, setup, branch pilot/work), with a per-run copy of the
    engineering stub (a git repo: the plan root of a multi-repo plan) and, per repo, a link with a
    run-unique name under .pilot/ so `rein gate`'s per-worktree test DB never collides."""
    base, meta = SR.run_paths(run_id)
    if os.path.exists(base):
        raise SystemExit("exists: %s" % base)
    os.makedirs(meta)
    c = CFG["candidates"][cid]
    repos = {}
    for repo, spec in c["repos"].items():
        dst = os.path.join(base, repo)
        S.sh(["git", "clone", "-q", "--no-hardlinks", os.path.join(ROOT, cid, "fixture", repo), dst], check=True)
        S.sh(["git", "remote", "remove", "origin"], cwd=dst, check=True)
        fx = json.load(open(os.path.join(ROOT, cid, "fixture", repo + ".json")))
        head = S.sh(["git", "rev-parse", "HEAD"], cwd=dst, check=True).stdout.strip()
        assert head == fx["base"], (head, fx["base"])
        assert not S.sh(["git", "rev-list", "--all", "--not", head], cwd=dst).stdout.split(), "future commits"
        with open(os.path.join(dst, ".git", "info", "exclude"), "a") as fh:
            fh.write("\n.rein/\nnode_modules/\n.venv/\n")
        st, env = S.stack_env(spec, run_id)
        for step in st["setup"]:
            r = S.sh(step, cwd=dst, env=env, timeout=1800)
            if r.returncode:
                raise SystemExit("setup failed %s: %s" % (repo, (r.stdout + r.stderr)[-1500:]))
        S.sh(["git", "checkout", "-q", "-b", "pilot/work"], cwd=dst, check=True)
        repos[repo] = dict(path=dst, base=head)
    eng = os.path.join(base, "proxima-engineering")
    shutil.copytree(S.STUB, eng, symlinks=True)
    S.sh("git init -q && git add -A && git -c user.name=pilot -c user.email=pilot@local commit -qm stub", cwd=eng, check=True)
    pilot = os.path.join(base, ".pilot")
    os.makedirs(pilot)
    for repo in repos:
        os.symlink(os.path.join("..", repo), os.path.join(pilot, "%s-%s" % (run_id, repo)))
        repos[repo]["link"] = os.path.join(pilot, "%s-%s" % (run_id, repo))
    cwd = next(iter(repos.values()))["path"] if len(repos) == 1 else base
    root = next(iter(repos.values()))["path"] if len(repos) == 1 else eng
    json.dump(dict(run_id=run_id, cid=cid, repos=repos, cwd=cwd, plan_root=root, prepared=time.time()),
              open(os.path.join(meta, "run.json"), "w"), indent=1)
    return json.load(open(os.path.join(meta, "run.json")))


def gate_hook(meta, r):
    """N1's Stop hook (PLAN-PILOTO §1): `rein gate --tier task` in every repo; red blocks the stop.
    The script lives in meta/ (read-only to the agent); its logs go to .pilot/, which it can write."""
    base = os.path.dirname(meta)
    p = os.path.join(base, ".pilot")
    lines = ["#!/bin/sh", "P=%s; S=$(date +%%s); rc=0; : > \"$P/gate.last.log\"" % shlex.quote(p)]
    for repo, v in r["repos"].items():
        lines.append("rein gate %s --tier task --base %s >> \"$P/gate.last.log\" 2>&1; x=$?; "
                     "[ $x -eq 1 ] && rc=1; [ $x -eq 126 ] && [ $rc -eq 0 ] && rc=126" % (shlex.quote(v["link"]), v["base"]))
    lines += ["n=$(ls \"$P\"/gate-*.log 2>/dev/null | wc -l | tr -d ' '); cp \"$P/gate.last.log\" \"$P/gate-$((n+1)).log\"",
              "printf '{\"kind\":\"gate\",\"start\":%s,\"t\":%s,\"rc\":%s}\\n' $S $(date +%s) $rc >> \"$P/gate.jsonl\"",
              "if [ $rc -eq 1 ]; then echo 'rein gate is RED -- fix it before you stop:' >&2; tail -80 \"$P/gate.last.log\" >&2; exit 2; fi",
              "exit 0"]
    f = os.path.join(meta, "gate.sh")
    open(f, "w").write("\n".join(lines) + "\n")
    return f


def settings(meta, r, hook=True):
    s = json.load(open(SR.settings(meta, r["run_id"])))
    s["permissions"]["deny"] += ["Read(/%s/**)" % d for d in REIN_DENY]
    if hook:
        s["hooks"] = {"Stop": [{"hooks": [{"type": "command", "command": "sh " + shlex.quote(gate_hook(meta, r)),
                                           "timeout": 1200}]}]}
    p = os.path.join(meta, "settings.json")
    json.dump(s, open(p, "w"), indent=1)
    return p


# ---------------------------------------------------------------- sessions
def claude(r, meta, prompt, sid, phase, model=None, resume=False, hook=True, usd=PER_SESSION_USD, minutes=PER_PHASE_MIN):
    SR_stop()
    argv = ["sandbox-exec", "-f", profile(meta, r["run_id"], sid),
            "claude", "-p", "--model", model or H.MODEL, "--strict-mcp-config", "--permission-mode", "bypassPermissions",
            "--settings", settings(meta, r, hook), "--output-format", "json", "--max-budget-usd", str(usd)]
    argv += ["--resume", sid] if resume else ["--session-id", sid]
    ev(meta, "phase_start", phase=phase, session=sid, model=model or H.MODEL)
    try:
        p = subprocess.run(argv + [prompt], cwd=r["cwd"], capture_output=True, text=True, timeout=minutes * 60, stdin=subprocess.DEVNULL,
                           env=SR.agent_env(r["cid"], r["run_id"]))
        out, rc = p.stdout, p.returncode
    except subprocess.TimeoutExpired as exc:
        out = exc.stdout.decode() if isinstance(exc.stdout, bytes) else (exc.stdout or "")
        rc = "timeout"
    open(os.path.join(meta, "%s.out.json" % phase), "w").write(out or "")
    capped = rc == "timeout"
    try:
        capped = capped or "budget" in str(json.loads(out).get("subtype", ""))
    except ValueError:
        pass
    ev(meta, "phase_end", phase=phase, session=sid, rc=rc, capped=capped)
    return rc, capped


def SR_stop():
    if os.path.exists(STOP):
        raise SystemExit("STOP file present")


def native_prompt(r, intent, plan=None):
    p = H.NATIVE_PROMPT.format(intent=intent)
    if len(r["repos"]) > 1:
        p = p.replace("You are working in this repository (the current directory).",
                      "You are working in these repositories (subdirectories of the current directory: %s)."
                      % ", ".join(r["repos"]))
    if plan:
        p += H.PLAN_ADDON.format(plan=plan)
    return p


def why_of(intent):
    m = re.search(r"# What to build\s*\n(.*?)\n\s*\n", intent, re.S)
    return (m.group(1) if m else intent[:600]).strip()


def changed_files(path, base_sha):
    out = S.sh(["git", "diff", "--name-only", base_sha], cwd=path).stdout.split()
    out += [l[3:] for l in S.sh(["git", "status", "--porcelain"], cwd=path).stdout.splitlines() if l[:2] == "??"]
    return sorted(set(out))


def audit(r, meta, intent):
    """N2/P2's auditor: Codex with REIN's own audit brief (read-only use of the installed kit),
    inside the sandbox. Returns (verdict, blocking findings)."""
    SR_stop()
    sys.path.insert(0, os.path.join(KIT, "lib"))
    import ship, roles  # noqa: E402
    links = {v["link"]: changed_files(v["path"], v["base"]) for v in r["repos"].values()}
    root = next(iter(links))
    spec = ship.build_audit_spec({"why": why_of(intent)}, links, roles.profile("reviewer", KIT)["text"],
                                 audit_root=root, change=CFG["candidates"][r["cid"]]["change"],
                                 gate_commands={p: ship.audit_gate_command(p) for p in links})
    open(os.path.join(meta, "audit-brief.md"), "w").write(spec)
    ev(meta, "phase_start", phase="audit", model=AUDITOR)
    argv = ["sandbox-exec", "-f", profile(meta, r["run_id"], "none", codex=True),
            "codex", "exec", "-m", AUDITOR, "--dangerously-bypass-approvals-and-sandbox", "--skip-git-repo-check",
            "--json", "-C", root, spec]
    try:
        p = subprocess.run(argv, cwd=root, capture_output=True, text=True, timeout=PER_PHASE_MIN * 60, stdin=subprocess.DEVNULL,
                           env=SR.agent_env(r["cid"], r["run_id"]))
        rc, out = p.returncode, (p.stdout or "") + "\n" + (p.stderr or "")[-5000:]
    except subprocess.TimeoutExpired as exc:
        rc, out = "timeout", exc.stdout.decode() if isinstance(exc.stdout, bytes) else (exc.stdout or "")
    open(os.path.join(meta, "audit.jsonl"), "w").write(out)
    thread = next(iter(re.findall(r'"thread_id"\s*:\s*"([^"]+)"', out)), "")
    ev(meta, "phase_end", phase="audit", rc=rc, thread=thread)
    eps = sorted(glob.glob(os.path.join(root, ".rein", "reviews", "**", "*.json"), recursive=True), key=os.path.getmtime)
    if not eps:
        return None, []
    e = json.load(open(eps[-1]))
    shutil.copy(eps[-1], os.path.join(meta, "review.json"))
    sev = lambda f: (f.get("severity") or "") if isinstance(f, dict) else str(f).split(":")[0]
    txt = lambda f: ("BLOCKING: " + f.get("text", "")) if isinstance(f, dict) else str(f)
    return e.get("verdict"), [txt(f) for f in e.get("findings") or [] if sev(f).strip().upper() == "BLOCKING"]


def snapshot(r, meta):
    """The tree the auditor saw, so the pre-fix state can be scored too (FASE2 §6)."""
    pre = os.path.join(meta, "pre")
    os.makedirs(pre)
    repos = {}
    for repo, v in r["repos"].items():
        S.sh(["cp", "-c", "-R", v["path"], os.path.join(pre, repo)], check=True)
        repos[repo] = dict(path=os.path.join(pre, repo), base=v["base"],
                           head=S.sh(["git", "rev-parse", "HEAD"], cwd=v["path"]).stdout.strip())
    return repos


# ---------------------------------------------------------------- plan
def plan(cid, rep):
    run_id = rid(cid, "plan", rep)
    r = prepare(run_id, cid)
    base, meta = SR.run_paths(run_id)
    c = CFG["candidates"][cid]
    intent = open(os.path.join(ROOT, cid, "task", "intent.md")).read()
    target = os.path.join(r["plan_root"], "openspec", "changes", c["change"], "tasks.md")
    ev(meta, "run_start", quota=quotas(), claude=S.sh(["claude", "--version"]).stdout.strip())
    ok = False
    for attempt in (1, 2):
        sid = str(uuid.uuid4())
        prompt = PLAN_PROMPT.format(change=c["change"], repos=", ".join(r["repos"]), target=target,
                                    skill=open(os.path.join(KIT, "skills", "rein-plan", "SKILL.md")).read(), intent=intent)
        open(os.path.join(meta, "prompt.txt"), "w").write(prompt)
        claude(r, meta, prompt, sid, "plan%d" % attempt, model=PLANNER, hook=False, usd=PLAN_USD, minutes=PLAN_MIN)
        t = S.sh(["rein", "tasks", r["plan_root"], "--change", c["change"]])
        try:
            tasks = json.loads(t.stdout)
            n = len(tasks.get("tasks") or [])
        except ValueError:
            n = 0
        ev(meta, "plan_check", attempt=attempt, tasks=n, rc=t.returncode, out=(t.stdout + t.stderr)[-1500:])
        if os.path.exists(target) and n:
            ok = True
            break
    if ok:
        shutil.copy(target, os.path.join(meta, "tasks.md"))
    ev(meta, "run_end", ok=ok, quota=quotas())
    collect(run_id)
    return run_id


def plan_text(cid, rep):
    _, meta = SR.run_paths(rid(cid, "plan", rep))
    p = os.path.join(meta, "tasks.md")
    return open(p).read() if os.path.exists(p) else None


# ---------------------------------------------------------------- N1, N2, P2
def run(cid, arm, rep):
    run_id = rid(cid, arm, rep)
    the_plan = plan_text(cid, rep) if arm == "p2" else None
    if arm == "p2" and the_plan is None:  # FASE2 §2: a plan that failed twice fails P2 and A
        base, meta = SR.run_paths(run_id)
        os.makedirs(meta)
        ev(meta, "run_start"); ev(meta, "run_end", rc="no-plan", capped=False, no_plan=True)
        return run_id
    r = prepare(run_id, cid)
    base, meta = SR.run_paths(run_id)
    c = CFG["candidates"][cid]
    if the_plan:
        d = os.path.join(r["plan_root"], "openspec", "changes", c["change"])
        os.makedirs(d, exist_ok=True)
        open(os.path.join(d, "tasks.md"), "w").write(the_plan)
        if r["plan_root"] in [v["path"] for v in r["repos"].values()]:
            with open(os.path.join(r["plan_root"], ".git", "info", "exclude"), "a") as fh:
                fh.write("openspec/changes/%s/\n" % c["change"])
    intent = open(os.path.join(ROOT, cid, "task", "intent.md")).read()
    prompt = native_prompt(r, intent, the_plan)
    open(os.path.join(meta, "prompt.txt"), "w").write(prompt)
    sid = str(uuid.uuid4())
    ev(meta, "run_start", session=sid, arm=arm, quota=quotas(), claude=S.sh(["claude", "--version"]).stdout.strip())
    rc, capped = claude(r, meta, prompt, sid, "implement")
    rounds, verdict, blocking = 0, None, []
    if arm in ("n2", "p2") and not capped:
        pre = snapshot(r, meta)
        verdict, blocking = audit(r, meta, intent)
        ev(meta, "verdict", verdict=verdict, blocking=blocking, round=1, pre=pre)
        if blocking:
            rounds = 1
            rc, capped = claude(r, meta, H.FIX_PROMPT.format(findings="\n".join(blocking)), sid, "fix", resume=True)
    heads = {repo: S.sh(["git", "rev-parse", "HEAD"], cwd=v["path"]).stdout.strip() for repo, v in r["repos"].items()}
    ev(meta, "run_end", rc=rc, capped=capped, heads=heads, fix_rounds=rounds, verdict=verdict,
       blocking=len(blocking), quota=quotas())
    collect(run_id)
    return run_id


# ---------------------------------------------------------------- A
OWNER = ("Decide within the acceptance criteria and the plan's decisions; the owner has no "
         "further input for this pilot.")


def owner_choice(kind, options, seen):
    """The smoke's scripted owner (watch_a.py): one retry/resend per gate kind, then let Rein go on."""
    if not options:
        return OWNER
    for pick in (("retry", "resend") if seen.get(kind, 0) < 1 else ()) + ("approve", "continue", "accept", "wait", "skip"):
        if pick in options:
            return pick
    return options[0]


def runa(cid, rep):
    """A: REIN as it runs today (FASE2 §1, §5). Clones are Orca repos named pilot-<run>-<repo>, and
    the plan's `Repo:` names them, never a real repo. One harness commit per clone: orca.yaml (the
    worktree's setup) and the gate's DB cleanup aimed at the pilot Postgres, never the owner's."""
    run_id = rid(cid, "a", rep)
    the_plan = plan_text(cid, rep)
    base, meta = SR.run_paths(run_id)
    if the_plan is None:
        os.makedirs(meta)
        ev(meta, "run_start"); ev(meta, "run_end", rc="no-plan", capped=False, no_plan=True)
        return run_id
    os.makedirs(meta)
    c = CFG["candidates"][cid]
    names, repos = {}, {}
    for repo, spec in c["repos"].items():
        name = "pilot-%s-%s" % (run_id, repo)
        names[repo] = name
        dst = os.path.join(base, name)
        S.sh(["git", "clone", "-q", "--no-hardlinks", os.path.join(ROOT, cid, "fixture", repo), dst], check=True)
        S.sh(["git", "remote", "remove", "origin"], cwd=dst, check=True)
        head = S.sh(["git", "rev-parse", "HEAD"], cwd=dst, check=True).stdout.strip()
        assert head == json.load(open(os.path.join(ROOT, cid, "fixture", repo + ".json")))["base"]
        with open(os.path.join(dst, ".git", "info", "exclude"), "a") as fh:
            fh.write("\n.rein/\nnode_modules/\n.venv/\n.env.local\n")
        st, env = S.stack_env(spec, run_id)
        for step in st["setup"]:
            x = S.sh(step, cwd=dst, env=env, timeout=1800)
            if x.returncode:
                raise SystemExit("setup failed %s: %s" % (repo, (x.stdout + x.stderr)[-1500:]))
        if st.get("env"):  # the worker's shell has no CI env: the app reads .env.local (ENV=local)
            open(os.path.join(dst, ".env.local"), "w").write("".join("%s=%s\n" % kv for kv in st["env"].items()))
        setup = ["set -e", 'MAIN="$(dirname "$(git rev-parse --path-format=absolute --git-common-dir)")"']
        if st.get("env"):
            setup.append('[ -f .env.local ] || cp "$MAIN/.env.local" .env.local')
        setup += st["setup"]
        open(os.path.join(dst, "orca.yaml"), "w").write("scripts:\n  setup: |\n" + "".join("    %s\n" % l for l in setup))
        fc = os.path.join(dst, "flow.config.json")
        text = open(fc).read() if os.path.exists(fc) else ""
        if "proxima-suite-db" in text:  # read first: open(fc, "w") truncates (it emptied the file in the first A)
            open(fc, "w").write(text.replace("proxima-suite-db", CFG["postgres"]["container"]))
            json.loads(open(fc).read())
        S.sh(["git", "add", "-A", "orca.yaml"] + (["flow.config.json"] if os.path.exists(fc) else []), cwd=dst, check=True)
        S.sh("git -c user.name=pilot -c user.email=pilot@local commit -qm 'pilot harness: Orca setup and the pilot "
             "Postgres' && git branch -f pilot-base", cwd=dst, check=True)
        repos[repo] = dict(path=dst, base=head)
    stub = os.path.join(base, "proxima-engineering")
    os.symlink(S.STUB, stub)
    the_plan = re.sub(r"(?m)^(\s+- Repo: )(\S+)\s*$", lambda m: m.group(1) + names.get(m.group(2).strip("`"), m.group(2)), the_plan)
    if len(repos) == 1:
        root = next(iter(repos.values()))["path"]
    else:
        root = os.path.join(base, "pilot-%s-proxima-engineering" % run_id)
        shutil.copytree(S.STUB, root, symlinks=True)
        S.sh("git init -q -b pilot-base && git add -A && git -c user.name=pilot -c user.email=pilot@local commit -qm stub",
             cwd=root, check=True)
    d = os.path.join(root, "openspec", "changes", c["change"])
    os.makedirs(d, exist_ok=True)
    open(os.path.join(d, "tasks.md"), "w").write(the_plan)
    with open(os.path.join(root, ".git", "info", "exclude"), "a") as fh:
        fh.write("openspec/changes/%s/\n" % c["change"])
    for path in [v["path"] for v in repos.values()] + ([root] if root not in [v["path"] for v in repos.values()] else []):
        S.sh(["orca", "repo", "add", "--path", path, "--json"], check=True)
        S.sh(["orca", "repo", "set-base-ref", "--repo", "path:" + path, "--ref", "pilot-base", "--json"], check=True)
        ws = os.path.join(HOME, "orca", "workspaces", os.path.basename(path))
        os.makedirs(ws, exist_ok=True)
        if not os.path.lexists(os.path.join(ws, "proxima-engineering")):
            os.symlink(S.STUB, os.path.join(ws, "proxima-engineering"))
    json.dump(dict(run_id=run_id, cid=cid, repos=repos, names=names, plan_root=root, prepared=time.time()),
              open(os.path.join(meta, "run.json"), "w"), indent=1)
    ev(meta, "run_start", arm="a", quota=quotas(), claude=S.sh(["claude", "--version"]).stdout.strip())
    p = S.sh(["rein", "ship", c["change"], "--root", root, "--agent", "claude", "--model", "sonnet", "--detach"])
    ev(meta, "ship_launch", rc=p.returncode, out=(p.stdout + p.stderr)[-2000:])
    state_file = os.path.join(root, ".rein", "ship", c["change"] + ".json")
    t0, answered, seen = time.time(), set(), {}
    while True:
        st = json.load(open(state_file)) if os.path.exists(state_file) else {}
        if st.get("phase") in ("done", "failed", "in-review", "rejected"):
            break
        if p.returncode or time.time() - t0 > A_MIN * 60 or os.path.exists(STOP):
            ev(meta, "budget_stop", phase=st.get("phase"), launch_rc=p.returncode)
            if st.get("driver_pid"):
                subprocess.run(["kill", str(st["driver_pid"])])
            for t in (st.get("tasks") or {}).values():
                if t.get("dispatch_id") and t.get("status") == "running":
                    S.sh(["orca", "orchestration", "worker-stop", "--dispatch", t["dispatch_id"], "--json"])
            break
        out = S.sh(["rein", "watch", c["change"], "--root", root, "--once"], timeout=120).stdout
        for m in re.finditer(r"PENDING (\S+) \((\w+)\) task (\S+)\n(?:question: (.*)\n)?(?:options: (.*))?", out):
            gate, kind, tid, question, opts = m.groups()
            if gate in answered:
                continue
            options = [o.strip() for o in (opts or "").split(",") if o.strip() and not o.strip().startswith("(")]
            answer = owner_choice(kind, options, seen)
            seen[kind] = seen.get(kind, 0) + 1
            x = S.sh(["rein", "answer", c["change"], answer, "--gate", gate, "--root", root])
            answered.add(gate)
            ev(meta, "owner_answer", gate=gate, gate_kind=kind, task=tid, question=(question or "")[:400],
               options=options, answer=answer, rc=x.returncode)
        time.sleep(30)
    st = json.load(open(state_file)) if os.path.exists(state_file) else {}
    wts = st.get("worktree_paths") or {}
    final = {}
    for repo, name in names.items():
        tree = wts.get(name) or next((p for k, p in wts.items() if k.endswith(name)), "")
        final[repo] = dict(path=tree or repos[repo]["path"], base=repos[repo]["base"],
                           head=S.sh(["git", "rev-parse", "HEAD"], cwd=tree).stdout.strip() if tree else "")
    rj = json.load(open(os.path.join(meta, "run.json")))
    rj.update(repos=final, ship=dict(phase=st.get("phase"), audit=st.get("audit_result"), audit_rounds=st.get("audit_rounds")))
    json.dump(rj, open(os.path.join(meta, "run.json"), "w"), indent=1)
    ev(meta, "run_end", rc=st.get("phase"), capped=st.get("phase") not in ("done", "in-review"), heads={k: v["head"] for k, v in final.items()},
       fix_rounds=st.get("audit_rounds"), verdict=st.get("audit_result"), quota=quotas(),
       dispatches=sum(t.get("dispatches", 0) for t in (st.get("tasks") or {}).values()))
    collect(run_id)
    return run_id


# ---------------------------------------------------------------- collect
def transcripts(run_id, evs):
    """Every Claude transcript (with subagents) and Codex rollout the run produced."""
    base, meta = SR.run_paths(run_id)
    t0 = min(e["t"] for e in evs)
    t1 = max(e["t"] for e in evs)
    cl, cx = [], []
    for sid in {e["session"] for e in evs if e.get("session")}:
        cl += glob.glob(HOME + "/.claude/projects/*/%s.jsonl" % sid)
        cl += glob.glob(HOME + "/.claude/projects/*/%s/**/*.jsonl" % sid, recursive=True)
    roots = [base]
    if "-a-r" in run_id:  # Orca's worktrees of the pilot repos
        rj = json.load(open(os.path.join(meta, "run.json")))
        roots += [os.path.join(HOME, "orca", "workspaces", n) for n in list(rj.get("names", {}).values()) +
                  ["pilot-%s-proxima-engineering" % run_id]]
        for rt in roots[1:]:
            for f in glob.glob(HOME + "/.claude/projects/%s*/**/*.jsonl" % SR.slug(rt), recursive=True):
                if os.path.getmtime(f) >= t0 - 60:
                    cl.append(f)
    for e in evs:
        if e.get("thread"):
            cx += glob.glob(HOME + "/.codex/sessions/**/*%s.jsonl" % e["thread"], recursive=True)
    if "-a-r" in run_id:
        for f in glob.glob(HOME + "/.codex/sessions/**/*.jsonl", recursive=True):
            if os.path.getmtime(f) < t0 - 60:
                continue
            with open(f, errors="ignore") as fh:
                head = fh.read(30000)
            if any(rt in head for rt in roots):
                cx.append(f)
    return sorted(set(cl)), sorted(set(cx))


def calls_of(cl, cx):
    """(tool input, what came back) pairs from Claude transcripts and Codex rollouts."""
    out = []
    for files, kind in ((cl, "claude"), (cx, "codex")):
        calls, results = {}, {}
        for f in files:
            for line in open(f, errors="ignore"):
                try:
                    d = json.loads(line)
                except ValueError:
                    continue
                if kind == "claude":
                    m = d.get("message") or {}
                    for b in m.get("content") if isinstance(m.get("content"), list) else []:
                        if isinstance(b, dict) and b.get("type") == "tool_use":
                            calls[b.get("id")] = json.dumps(b.get("input"))
                        elif isinstance(b, dict) and b.get("type") == "tool_result":
                            c = b.get("content")
                            results[b.get("tool_use_id")] = c if isinstance(c, str) else json.dumps(c)
                else:
                    p = d.get("payload") or {}
                    if d.get("type") != "response_item":
                        continue
                    if p.get("type") in ("function_call", "custom_tool_call", "local_shell_call"):
                        calls[p.get("call_id")] = json.dumps(p.get("arguments") or p.get("input") or p.get("action"))
                    elif p.get("type") in ("function_call_output", "custom_tool_call_output"):
                        o = p.get("output")
                        results[p.get("call_id")] = o if isinstance(o, str) else json.dumps(o)
        out += [(kind, cid, c, results.get(cid, "")) for cid, c in calls.items()]
    return out


def scan(run_id, cl, cx, own_roots):
    """The screen's evidence rule (CRIBA §15-16): a call that names a forbidden place is a leak only
    if what came back carries something from there; otherwise it is a harmless attempt."""
    rx = forbidden_rx(run_id)
    outside = re.compile("|".join([re.escape(d) for d in SR.SANDBOX_DENY] +
                                  [re.escape(d.replace(HOME, "~", 1)) for d in SR.SANDBOX_DENY] +
                                  [r"\$HOME/(Developer|orca|\.orca|\.codex|\.ssh)",
                                   r"cd (~|\$HOME|%s)/?(Developer)?/?(\s|&|;|$)" % re.escape(HOME)]))
    own = re.compile("|".join(re.escape(p) + "|" + re.escape(p.replace(HOME, "~", 1)) for p in own_roots))
    relative = re.compile(r"products/proxima/|proxima-engineering/\.rein|rein-pilot/(screen|oracle|fixtures|validate|runs|screen-runs)(?![\w-])|"
                          r"orca/workspaces/(?!pilot-%s)|\.claude/projects/-|docs/research/2026|phase2-runs/(?!%s)[a-z]|"
                          r"claude/rein/(audit-reports|runs\.jsonl|events\.jsonl)" % (re.escape(run_id), re.escape(run_id)))
    blocked = re.compile(r"Operation not permitted|blocked by a deny rule|has been denied|not logged into any GitHub|"
                         r"terminal prompts disabled|Permission denied")
    base, _ = SR.run_paths(run_id)
    bg = re.compile(r"moved to the background \(ID: (\w+)\)")
    hits, attempts = [], []
    for kind, _, c, out in calls_of(cl, cx):
        cc = own.sub("", c)
        found = [x.group(0) for x in rx.finditer(cc)] + [x.group(0) for x in outside.finditer(cc)]
        if not found:
            continue
        for t in bg.findall(out):
            out += "".join(open(f, errors="ignore").read() for f in glob.glob("%s/*/*/tasks/%s.output" % (SR.TMP_CLAUDE, t)))
        seen = own.sub("", "\n".join(l for l in out.splitlines() if not blocked.search(l)))
        evidence = [x.group(0) for r in (rx, outside, relative) for x in r.finditer(seen)]
        rec = dict(agent=kind, match=found[:3], call=c[:240], result=out[:300], evidence=evidence[:3])
        (hits if evidence else attempts).append(rec)
    return hits, attempts


def collect(run_id):
    base, meta = SR.run_paths(run_id)
    evs = [json.loads(l) for l in open(os.path.join(meta, "events.jsonl"))]
    st = next(e for e in evs if e["kind"] == "run_start")
    en = next(e for e in reversed(evs) if e["kind"] == "run_end")
    cl, cx = transcripts(run_id, evs)
    phase_of = {e["session"]: e["phase"] for e in evs if e["kind"] == "phase_start" and e.get("session")}
    sess = []
    for f in cl:
        s = H.parse_claude(f)
        s["phase"] = phase_of.get(os.path.basename(f)[:-6], "worker" if "-a-r" in run_id else "subagent")
        sess.append(s)
    cxs = [H.parse_codex(f) for f in cx]
    gates = []
    gl = os.path.join(base, ".pilot", "gate.jsonl")
    if os.path.exists(gl):
        gates = [json.loads(l) for l in open(gl) if l.strip()]
    for g in glob.glob(os.path.join(base, "**", ".rein", "ship", "*.gates.jsonl"), recursive=True):
        for line in open(g):
            try:
                x = json.loads(line)
            except ValueError:
                continue
            secs = sum((s.get("elapsedMs") or 0) for s in x.get("slots") or [] if not s.get("cached")) / 1000
            gates.append(dict(start=x["at"] - secs, t=x["at"], rc=x.get("exitCode", x.get("rc"))))
    active = H.union([(s["start"], s["end"]) for s in sess + cxs if s["start"]] + [(g["start"], g["t"]) for g in gates])
    wall = en["t"] - st["t"]
    own_roots = [base]
    rj = json.load(open(os.path.join(meta, "run.json"))) if os.path.exists(os.path.join(meta, "run.json")) else {}
    own_roots += [os.path.join(HOME, "orca", "workspaces", n) for n in list(rj.get("names", {}).values()) +
                  ["pilot-%s-proxima-engineering" % run_id]]
    hits, attempts = scan(run_id, cl, cx, own_roots)
    by_phase = {}
    for s in sess:
        by_phase[s["phase"]] = round(by_phase.get(s["phase"], 0) + s["usd"], 2)
    m = dict(run_id=run_id, rc=en.get("rc"), capped=en.get("capped"), wall_min=round(wall / 60, 1),
             active_min=round(active / 60, 1), idle_min=round((wall - active) / 60, 1),
             turns=sum(s["turns"] for s in sess),
             tokens={k: sum(s[k] for s in sess) for k in ("read", "write5", "write1", "input", "output")},
             usd=round(sum(s["usd"] for s in sess), 2), usd_by_phase=by_phase,
             models=sorted({s["model"] for s in sess if s["model"]}), transcripts=len(sess),
             codex_sessions=len(cxs), codex_tokens={k: sum(s[k] for s in cxs) for k in ("input", "cached", "output")},
             codex_models=sorted({s["model"] for s in cxs if s["model"]}),
             gate_runs=len(gates), gate_red=sum(1 for g in gates if g.get("rc") == 1),
             fix_rounds=en.get("fix_rounds"), verdict=en.get("verdict"), blocking=en.get("blocking"),
             interventions=sum(1 for e in evs if e["kind"] == "owner_answer"),
             quota_before=st.get("quota"), quota_after=en.get("quota"), leaks=hits, blocked_attempts=attempts)
    json.dump(m, open(os.path.join(meta, "metrics.json"), "w"), indent=1)
    return m


# ---------------------------------------------------------------- scoring
def score(run_id):
    """Final state with the screen's scorer; then the state the auditor saw, as `<run>-pre`, when a
    fix round followed it (N2/P2: the snapshot; A: the head before its first audit is not kept, so
    A's pre-audit state is not scored)."""
    base, meta = SR.run_paths(run_id)
    if not os.path.exists(os.path.join(meta, "run.json")):
        return None
    res = SR.score(run_id)
    v = next((json.loads(l) for l in open(os.path.join(meta, "events.jsonl")) if '"verdict"' in l and '"kind": "verdict"' in l), None)
    end = [json.loads(l) for l in open(os.path.join(meta, "events.jsonl"))][-1]
    if v and v.get("blocking") and end.get("fix_rounds"):
        pid = run_id + "-pre"
        pbase, pmeta = SR.run_paths(pid)
        os.makedirs(pmeta)
        json.dump(dict(run_id=pid, cid=parse_rid(run_id)[0], repos=v["pre"]), open(os.path.join(pmeta, "run.json"), "w"), indent=1)
        res["pre"] = SR.score(pid)
    return res


# ---------------------------------------------------------------- launcher
def jobs(batch):
    plans = [("plan", c, r) for c, r in BATCHES[batch]]
    runs = [(a, c, r) for c, r in BATCHES[batch] for a in ARMS]
    random.Random(SEED + batch).shuffle(plans)
    random.Random(SEED + batch).shuffle(runs)
    return plans + runs


def job_id(j):
    return rid(j[1], j[0], j[2])


def done(j):
    return os.path.exists(SR.run_paths(job_id(j))[0])


def spent(batch=None):
    keys = {job_id(j) for j in jobs(batch)} if batch else None
    tot = 0.0
    for f in glob.glob(os.path.join(RUNS, "*", "meta", "metrics.json")) + glob.glob(os.path.join(RUNS, "_*", "*", "meta", "metrics.json")):
        rid_ = f.split(os.sep)[-3]
        if keys is None or rid_ in keys or "/_" in f:
            tot += json.load(open(f)).get("usd", 0)
    return tot


def launch(batch, dry=False):
    if not dry:
        verify_frozen()
    order = [j for j in jobs(batch) if not done(j)]
    print("pending:", len(order))
    if dry:
        for j in order:
            print(" ", job_id(j))
        return
    log = open(os.path.join(RUNS, "launch.log"), "a")
    lock, stop = threading.Lock(), threading.Event()
    busy = {}
    scoreq = queue.Queue()

    def say(*a):
        line = time.strftime("%H:%M:%S ") + " ".join(str(x) for x in a)
        print(line, flush=True)
        log.write(line + "\n"); log.flush()

    def gatekeep():
        while not stop.is_set():
            if os.path.exists(STOP):
                say("STOP file"); stop.set(); return False
            if spent(batch) >= BUDGET[batch]:
                say("batch budget reached: $%.2f" % spent(batch)); stop.set(); return False
            q = quotas()
            if q["claude"]["weekly"] is None or q["codex"]["weekly"] is None:
                say("quota unreadable: stopping", q); stop.set(); return False
            if q["claude"]["weekly"] >= WEEKLY_STOP:
                say("claude weekly %s%% >= %s%%: stopping" % (q["claude"]["weekly"], WEEKLY_STOP)); stop.set(); return False
            if q["codex"]["weekly"] >= CODEX_STOP:
                say("codex weekly %s%% >= %s%%: stopping" % (q["codex"]["weekly"], CODEX_STOP)); stop.set(); return False
            if q["claude"]["session"] >= SESSION_PAUSE:
                say("5-hour window %s%%: pausing 10 min" % q["claude"]["session"]); time.sleep(600); continue
            return True
        return False

    def ready(j):
        """A plan before the P2/A that use it; A only when nothing else runs, and nothing beside A;
        never two runs of one task at once."""
        if j[0] in ("p2", "a") and not os.path.exists(os.path.join(SR.run_paths(rid(j[1], "plan", j[2]))[1], "metrics.json")):
            return False
        if any(b[0] == "a" for b in busy.values()):
            return False
        if j[0] == "a" and busy:
            return False
        return not any(b[1] == j[1] for b in busy.values())

    def worker(n):
        while True:
            with lock:
                if stop.is_set() or not order:
                    return
                j = next((x for x in order if ready(x)), None)
                if j is not None:
                    order.remove(j)
                    busy[n] = j
            if j is None:
                time.sleep(20)
                continue
            try:
                if gatekeep():
                    say("start", job_id(j))
                    if j[0] == "plan":
                        x = plan(j[1], j[2])
                    elif j[0] == "a":
                        x = runa(j[1], j[2])
                    else:
                        x = run(j[1], j[0], j[2])
                    m = json.load(open(os.path.join(SR.run_paths(x)[1], "metrics.json"))) \
                        if os.path.exists(os.path.join(SR.run_paths(x)[1], "metrics.json")) else {}
                    say("end", x, "rc=%s capped=%s wall=%s usd=%s leaks=%d" % (
                        m.get("rc"), m.get("capped"), m.get("wall_min"), m.get("usd"), len(m.get("leaks", []))))
                    if m.get("leaks"):
                        say("LEAK in", x, "-> stopping (FASE2 §5)"); stop.set()
                    if j[0] != "plan":
                        scoreq.put(x)
            except BaseException as exc:  # a broken run is a methodological problem: stop
                say("ERROR", job_id(j), repr(exc)[:500]); stop.set()
            finally:
                with lock:
                    busy.pop(n, None)

    def scorer():
        while True:
            x = scoreq.get()
            if x is None:
                return
            try:
                s = score(x)
                if s:
                    say("scored", x, "correct=%s correct+=%s F=%s Dspec=%s reg=%s gate=%s%s" % (
                        s["correct"], s["correct_plus"], s["f_ok"], s["dspec_ok"], s["regressions"], s["gate_ok"],
                        " | pre: correct=%s Dspec=%s" % (s["pre"]["correct"], s["pre"]["dspec_ok"]) if s.get("pre") else ""))
            except BaseException as exc:
                say("SCORE ERROR", x, repr(exc)[:500])

    say("batch %d: %d jobs, quotas %s" % (batch, len(order), quotas()))
    sc = threading.Thread(target=scorer); sc.start()
    ws = [threading.Thread(target=worker, args=(n,)) for n in range(2)]
    for w in ws:
        w.start(); time.sleep(5)
    for w in ws:
        w.join()
    scoreq.put(None); sc.join()
    say("batch %d done; spent $%.2f; quotas %s" % (batch, spent(batch), quotas()))


def freeze():
    out = dict(frozen=time.time(), claude=S.sh(["claude", "--version"]).stdout.strip(),
               codex=S.sh(["codex", "--version"]).stdout.strip(), rein=S.sh(["rein", "--version"]).stdout.strip(),
               model=H.MODEL, planner=PLANNER, auditor=AUDITOR, seed=SEED, files={}, kit={}, tasks={})
    for p in ("FASE2.md", "CRIBA.md", "pilot/phase2.py", "pilot/screen_run.py", "pilot/screen.py", "pilot/harness.py", "pilot/screen.json"):
        out["files"][p] = S.sha256(os.path.join(os.path.dirname(HERE), p))
    for p in sorted(glob.glob(os.path.join(KIT, "lib", "*.py")) + [os.path.join(KIT, "skills", "rein-plan", "SKILL.md"),
                                                                   os.path.join(KIT, "skills", "rein-audit", "SKILL.md")]):
        out["kit"][os.path.relpath(p, KIT)] = S.sha256(p)
    crib = json.load(open(os.path.join(HERE, "manifest-criba.json")))
    for cid in HARD + EASY:
        d = os.path.join(ROOT, cid, "task")
        out["tasks"][cid] = {p: S.sha256(os.path.join(d, p)) for p in crib["tasks"][cid]}
        bad = [p for p, h in crib["tasks"][cid].items() if out["tasks"][cid][p] != h]
        if bad:
            raise SystemExit("%s changed since the screen: %s" % (cid, bad))
    dst = os.path.join(HERE, "manifest-fase2.json")
    if os.path.exists(dst):
        raise SystemExit("already frozen: %s" % dst)
    json.dump(out, open(dst, "w"), indent=1)
    print("frozen", dst)


def verify_frozen():
    mf = os.path.join(HERE, "manifest-fase2.json")
    if not os.path.exists(mf):
        raise SystemExit("not frozen: run `phase2.py freeze` first")
    m = json.load(open(mf))
    bad = [p for p, h in m["files"].items() if S.sha256(os.path.join(os.path.dirname(HERE), p)) != h]
    bad += [p for p, h in m["kit"].items() if S.sha256(os.path.join(KIT, p)) != h]
    for cid, files in m["tasks"].items():
        bad += ["%s/%s" % (cid, p) for p, h in files.items() if S.sha256(os.path.join(ROOT, cid, "task", p)) != h]
    if bad:
        raise SystemExit("changed since freeze: %s" % bad[:10])


def table():
    for f in sorted(glob.glob(os.path.join(RUNS, "*", "meta", "metrics.json"))):
        x = f.split(os.sep)[-3]
        m = json.load(open(f))
        sp = os.path.join(os.path.dirname(f), "score.json")
        s = json.load(open(sp)) if os.path.exists(sp) else {}
        print("%-30s wall=%-5s usd=%-6s gate=%s/%s fix=%s blk=%s int=%s leaks=%d | correct=%s correct+=%s F=%s D=%s reg=%s" % (
            x, m.get("wall_min"), m.get("usd"), m.get("gate_red"), m.get("gate_runs"), m.get("fix_rounds"), m.get("blocking"),
            m.get("interventions"), len(m.get("leaks", [])), s.get("correct"), s.get("correct_plus"), s.get("f_ok"),
            s.get("dspec_ok"), s.get("regressions")))


if __name__ == "__main__":
    a = sys.argv[1:]
    if not a:
        raise SystemExit(__doc__)
    if a[0] == "plan":
        print(plan(a[1], int(a[2])))
    elif a[0] == "run":
        print(run(a[1], a[2], int(a[3])))
    elif a[0] == "runa":
        print(runa(a[1], int(a[2])))
    elif a[0] == "score":
        print(json.dumps(score(a[1]), indent=1)[:4000])
    elif a[0] == "collect":
        print(json.dumps(collect(a[1]), indent=1)[:3000])
    elif a[0] == "launch":
        launch(int(a[1]), dry="--dry" in a)
    elif a[0] == "freeze":
        freeze()
    elif a[0] == "table":
        table()
    else:
        raise SystemExit(__doc__)
