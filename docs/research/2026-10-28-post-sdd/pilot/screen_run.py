#!/usr/bin/env python3
"""Difficulty screen, phase 1: N0 runs, scoring and the launcher (CRIBA §5, §6, §10).

  screen_run.py schedule              print the seeded run order (26 runs)
  screen_run.py run <cand> <rep>      one N0 run: clone the fixture, set up, `claude -p`, collect, leak-scan
  screen_run.py score <run-id>        hidden tests + suite (isolated reruns) + gate on a copy of the result
  screen_run.py launch [--dry]        all pending runs: 2 at a time, quota/budget/STOP checks, serial scoring
  screen_run.py table                 one line per run

N0 is the smoke's N0: same prompt, model, flags and per-run caps (harness.py). What changed is the
environment (CRIBA §3): CI env vars instead of a copied .env.local, the base lockfile instead of
HEAD's node_modules, and the dedicated Postgres.
"""
import glob, json, os, random, re, shutil, subprocess, sys, threading, time, uuid, queue

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
import screen as S          # noqa: E402
import harness as H         # noqa: E402  (prompt, parsers, prices: identical to the smoke)

CFG = S.CFG
ROOT = S.ROOT                                   # .../rein-pilot/screen  (oracle: agents never read it)
RUNS = os.path.join(os.path.dirname(ROOT), "screen-runs")
STOP = os.path.join(os.path.dirname(ROOT), "STOP")
SEED, REPS, CONCURRENT = 20261029, 2, 2
BUDGET_USD = 120.0                               # API-eq cap for the whole screen
SESSION_PAUSE, WEEKLY_STOP = 70, 60              # Claude quota %, CRIBA §10
PER_RUN_USD, PER_RUN_MIN = 8.0, 60


def rid(cid, rep):
    return "%s-n0-r%d" % (cid, rep)


def run_paths(run_id):
    base = os.path.join(RUNS, run_id)
    return base, os.path.join(base, "meta")


def ev(meta, kind, **kw):
    kw.update(kind=kind, t=time.time())
    with open(os.path.join(meta, "events.jsonl"), "a") as fh:
        fh.write(json.dumps(kw) + "\n")


def claude_quota():
    q = H.quota()
    try:
        c = q["result"]["rateLimits"]["claude"]
        return dict(session=c["session"]["usedPercent"], weekly=c["weekly"]["usedPercent"])
    except (KeyError, TypeError):
        return dict(session=None, weekly=None, raw=str(q)[:300])


def forbidden_rx(run_id):
    own = re.escape(run_id)
    extra = [r"rein-pilot/screen/", r"rein-pilot/screen-runs/(?!%s)" % own, r"/Developer/sandbox/rein-pilot/fixtures"]
    # The clone stops at the base, so its openspec archive only holds changes that landed before it:
    # history any developer has. Only the run's own change there is a leak (CRIBA §14).
    cid = run_id.rsplit("-n0-", 1)[0]
    change = re.escape(CFG["candidates"][cid]["change"])
    base = [x for x in H.FORBIDDEN if x != r"openspec/changes/archive"]
    return re.compile("|".join(base + extra + [r"openspec/changes/archive\S*%s" % change]))


# ---------------------------------------------------------------- one run
def settings(meta, run_id):
    s = {"permissions": {"deny": [
        "Read(//Users/luisfarfan/Developer/products/**)", "Read(//Users/luisfarfan/orca/**)",
        "Read(//Users/luisfarfan/Developer/sandbox/rein-pilot/screen/**)",
        "Read(//Users/luisfarfan/Developer/sandbox/rein-pilot/fixtures/**)",
        "Read(//Users/luisfarfan/Developer/sandbox/rein-pilot/oracle/**)",
        "Read(//Users/luisfarfan/Developer/labs/agents/rein/docs/research/**)",
        "Read(//Users/luisfarfan/.claude/projects/**)", "Read(//Users/luisfarfan/.codex/**)",
        "Bash(orca search:*)", "Bash(git log --all:*)"]}}
    p = os.path.join(meta, "settings.json")
    json.dump(s, open(p, "w"), indent=1)
    return p


HOME = os.path.expanduser("~")
TMP_CLAUDE = "/private/tmp/claude-%d" % os.getuid()
# Read and write are denied here for every program the agent starts (CRIBA §15). Its own run dir,
# its own transcript dirs and the engineering stub are the only exceptions.
SANDBOX_DENY = [HOME + "/Developer", HOME + "/orca", HOME + "/.orca", HOME + "/Library/Application Support/orca",
                HOME + "/.codex", HOME + "/.ssh", HOME + "/.config/gh", HOME + "/.claude/projects",
                HOME + "/.claude/file-history", HOME + "/.claude/plans", HOME + "/.claude/todos",
                HOME + "/.claude/history.jsonl", HOME + "/Documents", HOME + "/Desktop", HOME + "/Downloads",
                HOME + "/proxima-marcas", HOME + "/sercoplus-migracion",
                HOME + "/Library/Logs"]  # Raycast's file watcher logs every changed path, oracle files included
# The machine-wide hooks of ~/.claude/settings.json ran in every N0 run before the sandbox; each may read
# exactly its own script (the qa guardrail and Orca's agent hook), nothing next to it.
SANDBOX_HOOK_FILES = [HOME + "/Developer/products/proxima/proxima-engineering/scripts/hooks/block-qa-access.py",
                      HOME + "/.orca/agent-hooks/claude-hook.sh"]


def slug(path):
    """How Claude Code names a project's transcript dir."""
    return re.sub(r"[^A-Za-z0-9]", "-", path)


def sandbox_profile(meta, run_id, sid):
    base, _ = run_paths(run_id)
    q = lambda p: '"%s"' % p.replace("\\", "\\\\").replace('"', '\\"')
    rx = lambda p: '#"%s"' % p
    own = re.escape(slug(base))
    lines = ["(version 1)", "(allow default)",
             "(deny file-read* file-write* %s)" % " ".join("(subpath %s)" % q(p) for p in SANDBOX_DENY),
             "(deny file-read* file-write* (regex %s))" % rx("^%s/-" % re.escape(TMP_CLAUDE)),
             "(allow file-read* file-write* (subpath %s))" % q(base),
             "(deny file-write* (subpath %s))" % q(os.path.join(base, "meta")),
             "(allow file-read* (subpath %s))" % q(S.STUB),
             "(allow file-read* %s)" % " ".join("(literal %s)" % q(p) for p in SANDBOX_HOOK_FILES),
             "(allow file-read* file-write* (regex %s) (regex %s))" % (
                 rx("^%s/\\.claude/projects/%s(-|/|$)" % (re.escape(HOME), own)),
                 rx("^%s/%s(-|/|$)" % (re.escape(TMP_CLAUDE), own))),
             "(allow file-read* file-write* (regex %s) (regex %s))" % (
                 rx("^%s/\\.claude/file-history/%s(/|$)" % (re.escape(HOME), sid)),
                 rx("^%s/\\.claude/todos/%s" % (re.escape(HOME), sid))),
             # history is appended to, never read back by `claude -p`
             "(allow file-write* (literal %s))" % q(HOME + "/.claude/history.jsonl"),
             # so getcwd() works under a denied parent
             "(allow file-read-metadata %s)" % " ".join(
                 "(literal %s)" % q(p) for p in (HOME + "/Developer", HOME + "/Developer/sandbox",
                                                 os.path.dirname(ROOT), RUNS, HOME + "/.claude/projects", TMP_CLAUDE))]
    p = os.path.join(meta, "sandbox.sb")
    open(p, "w").write("\n".join(lines) + "\n")
    return p


def agent_env(cid, run_id):
    """The stacks' env (Postgres, secrets for tests), without CI=1: the agent works as a developer would.
    No way to the code hosts: no ssh agent, no gh login, no git credential helper (CRIBA §15)."""
    env = {k: v for k, v in os.environ.items()
           if k not in ("SSH_AUTH_SOCK", "GH_TOKEN", "GITHUB_TOKEN", "GH_ENTERPRISE_TOKEN")}
    _, meta = run_paths(run_id)
    gh = os.path.join(meta, "gh")
    os.makedirs(gh, exist_ok=True)
    gitconfig = os.path.join(meta, "gitconfig")
    open(gitconfig, "w").write("[user]\n\tname = %s\n\temail = %s\n[init]\n\tdefaultBranch = main\n" % (
        S.sh(["git", "config", "--global", "user.name"]).stdout.strip(),
        S.sh(["git", "config", "--global", "user.email"]).stdout.strip()))
    env.update(GIT_CONFIG_NOSYSTEM="1", GIT_CONFIG_GLOBAL=gitconfig, GH_CONFIG_DIR=gh, GIT_TERMINAL_PROMPT="0",
               GIT_SSH_COMMAND="ssh -F /dev/null -o IdentitiesOnly=yes -o IdentityFile=/dev/null -o BatchMode=yes")
    for repo, spec in CFG["candidates"][cid]["repos"].items():
        st = CFG["stacks"][spec["stack"]]
        env.update(st.get("env", {}))
        if st.get("db_env"):
            env[st["db_env"]] = "proxima_test_run_" + re.sub(r"[^a-z0-9_]", "_", run_id)[:40]
    return env


def prepare(cid, rep):
    run_id = rid(cid, rep)
    base, meta = run_paths(run_id)
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
        later = S.sh(["git", "rev-list", "--all", "--not", head], cwd=dst).stdout.split()
        assert not later, "future commits in %s" % dst
        with open(os.path.join(dst, ".git", "info", "exclude"), "a") as fh:
            fh.write("\n.rein/\nnode_modules/\n.venv/\n")
        st, env = S.stack_env(spec, run_id)
        for step in st["setup"]:
            r = S.sh(step, cwd=dst, env=env, timeout=1800)
            if r.returncode:
                raise SystemExit("setup failed %s: %s" % (repo, (r.stdout + r.stderr)[-1500:]))
        S.sh(["git", "checkout", "-q", "-b", "pilot/work"], cwd=dst, check=True)
        repos[repo] = dict(path=dst, base=head)
    os.symlink(S.STUB, os.path.join(base, "proxima-engineering"))
    cwd = next(iter(repos.values()))["path"] if len(repos) == 1 else base
    json.dump(dict(run_id=run_id, cid=cid, rep=rep, repos=repos, cwd=cwd, prepared=time.time()),
              open(os.path.join(meta, "run.json"), "w"), indent=1)
    return run_id


def run(cid, rep):
    run_id = prepare(cid, rep)
    base, meta = run_paths(run_id)
    r = json.load(open(os.path.join(meta, "run.json")))
    intent = open(os.path.join(ROOT, cid, "task", "intent.md")).read()
    prompt = H.NATIVE_PROMPT.format(intent=intent)
    if len(r["repos"]) > 1:
        prompt = prompt.replace("You are working in this repository (the current directory).",
                                "You are working in these repositories (subdirectories of the current directory: %s)."
                                % ", ".join(r["repos"]))
    open(os.path.join(meta, "prompt.txt"), "w").write(prompt)
    sid = str(uuid.uuid4())
    argv = ["sandbox-exec", "-f", sandbox_profile(meta, run_id, sid),
            "claude", "-p", "--model", H.MODEL, "--strict-mcp-config", "--permission-mode", "bypassPermissions",
            "--settings", settings(meta, run_id), "--output-format", "json",
            "--max-budget-usd", str(PER_RUN_USD), "--session-id", sid, prompt]
    ev(meta, "run_start", session=sid, quota=claude_quota(),
       claude=S.sh(["claude", "--version"]).stdout.strip())
    try:
        p = subprocess.run(argv, cwd=r["cwd"], capture_output=True, text=True, timeout=PER_RUN_MIN * 60,
                           env=agent_env(cid, run_id))
        out, rc = p.stdout, p.returncode
    except subprocess.TimeoutExpired as exc:
        out = exc.stdout.decode() if isinstance(exc.stdout, bytes) else (exc.stdout or "")
        rc = "timeout"
    open(os.path.join(meta, "claude.out.json"), "w").write(out or "")
    capped = rc == "timeout"
    try:
        res = json.loads(out)
        capped = capped or res.get("subtype") == "error_max_budget_usd" or "budget" in str(res.get("subtype", ""))
    except ValueError:
        pass
    heads = {repo: S.sh(["git", "rev-parse", "HEAD"], cwd=v["path"]).stdout.strip() for repo, v in r["repos"].items()}
    ev(meta, "run_end", rc=rc, capped=capped, heads=heads, quota=claude_quota())
    collect(run_id)
    return run_id


def collect(run_id):
    base, meta = run_paths(run_id)
    evs = [json.loads(l) for l in open(os.path.join(meta, "events.jsonl"))]
    st = next(e for e in evs if e["kind"] == "run_start")
    en = next(e for e in reversed(evs) if e["kind"] == "run_end")
    files = glob.glob(os.path.expanduser("~/.claude/projects/*/%s.jsonl" % st["session"]))
    sess = [H.parse_claude(f) for f in files]
    # subagent transcripts of the same session live under <session>/subagents/
    for f in glob.glob(os.path.expanduser("~/.claude/projects/*/%s/**/*.jsonl" % st["session"]), recursive=True):
        sess.append(H.parse_claude(f))
    active = H.union([(s["start"], s["end"]) for s in sess if s["start"]])
    wall = en["t"] - st["t"]
    # Every call that names a forbidden path or one of the sandbox's denied dirs. It is a leak only if
    # what came back to the agent (its result, or the output of the background task it was moved to)
    # carries something from there; otherwise it is an attempt, reported but harmless (CRIBA §15-16).
    rx = forbidden_rx(run_id)
    outside = re.compile("|".join([re.escape(d) for d in SANDBOX_DENY] +
                                  [re.escape(d.replace(HOME, "~", 1)) for d in SANDBOX_DENY] +
                                  [r"\$HOME/(Developer|orca|\.orca|\.codex|\.ssh)",
                                   r"cd (~|\$HOME|%s)/?(Developer)?/?(\s|&|;|$)" % re.escape(HOME)]))
    own = re.compile(re.escape(base) + "|" + re.escape(base.replace(HOME, "~", 1)))
    # what a listing from a parent dir prints: paths relative to it
    relative = re.compile(r"products/proxima/|proxima-engineering/\.rein|rein-pilot/(screen|oracle|fixtures|validate|runs)(?![\w-])|"
                          r"orca/workspaces|\.claude/projects/-|docs/research/2026|screen-runs/(?!%s)[a-z]" % re.escape(run_id))
    blocked = re.compile(r"Operation not permitted|blocked by a deny rule|has been denied|not logged into any GitHub|"
                         r"terminal prompts disabled")
    bg = re.compile(r"moved to the background \(ID: (\w+)\)")
    task_out = lambda tid: "".join(open(f, errors="ignore").read() for f in glob.glob(
        "%s/%s*/%s/tasks/%s.output" % (TMP_CLAUDE, slug(base), st["session"], tid)))
    calls, results = [], {}
    for f in files + glob.glob(os.path.expanduser("~/.claude/projects/*/%s/**/*.jsonl" % st["session"]), recursive=True):
        for line in open(f, errors="ignore"):
            try:
                m = json.loads(line).get("message") or {}
            except ValueError:
                continue
            for b in m.get("content") if isinstance(m.get("content"), list) else []:
                if isinstance(b, dict) and b.get("type") == "tool_use":
                    calls.append(b)
                elif isinstance(b, dict) and b.get("type") == "tool_result":
                    c = b.get("content")
                    results[b.get("tool_use_id")] = c if isinstance(c, str) else json.dumps(c)
    hits, attempts = [], []
    for b in calls:
        c = json.dumps(b.get("input"))
        found = [x.group(0) for x in rx.finditer(c)] + [x.group(0) for x in outside.finditer(own.sub("", c))]
        if not found:
            continue
        out = results.get(b.get("id"), "")
        out += "".join(task_out(t) for t in bg.findall(out))
        seen = own.sub("", "\n".join(l for l in out.splitlines() if not blocked.search(l)))
        evidence = [x.group(0) for r in (rx, outside, relative) for x in r.finditer(seen)]
        rec = dict(match=found[:3], call=c[:240], result=out[:300], evidence=evidence[:3])
        (hits if evidence else attempts).append(rec)
    m = dict(run_id=run_id, rc=en["rc"], capped=en["capped"], wall_min=round(wall / 60, 1),
             active_min=round(active / 60, 1), idle_min=round((wall - active) / 60, 1),
             turns=sum(s["turns"] for s in sess),
             tokens={k: sum(s[k] for s in sess) for k in ("read", "write5", "write1", "input", "output")},
             usd=round(sum(s["usd"] for s in sess), 2), transcripts=len(sess),
             quota_before=st.get("quota"), quota_after=en.get("quota"), leaks=hits, blocked_attempts=attempts)
    json.dump(m, open(os.path.join(meta, "metrics.json"), "w"), indent=1)
    return m


# ---------------------------------------------------------------- scoring
def junit_names_to_targets(stack, names, root):
    """Failing JUnit names -> what to rerun in isolation (pytest node ids, or spec files).
    A pytest classname is the module's dotted path, followed by the test class if there is one:
    the module is the longest prefix that is a file under `root`."""
    out = set()
    for n in names:
        cls, _, name = n.partition("::")
        if stack in ("proxima-api", "proxima-rag-api"):
            parts = cls.split(".")
            k = next((k for k in range(len(parts), 0, -1)
                      if os.path.isfile(os.path.join(root, *parts[:k]) + ".py")), len(parts))
            out.add("::".join(["/".join(parts[:k]) + ".py"] + parts[k:] + [name]))
        else:
            out.add(cls)
    return sorted(out)


def rerun_cmd(stack, targets):
    q = " ".join("'%s'" % t for t in targets)
    if stack in ("proxima-api", "proxima-rag-api"):
        py = "uv run pytest" if stack == "proxima-api" else ".venv/bin/pytest"
        return "%s -q -p no:cacheprovider %s" % (py, q)
    if stack in ("proxima-pos", "proxima-admin"):
        return "pnpm exec ng test --watch=false " + " ".join("--include '%s'" % t for t in targets)
    if stack == "proxima-storefront-v2":
        return "pnpm exec vitest run " + q
    return "npx vitest run " + q


def score(run_id):
    base, meta = run_paths(run_id)
    r = json.load(open(os.path.join(meta, "run.json")))
    cid = r["cid"]
    h = json.load(open(os.path.join(ROOT, cid, "task", "hidden.json")))
    dest_root = os.path.join(base, "score")
    if os.path.exists(dest_root):
        raise SystemExit("score copy exists: %s" % dest_root)
    os.makedirs(dest_root)
    res = dict(run_id=run_id, hidden={}, suite={}, gate={}, tampering={}, diffstat={})
    for repo, v in r["repos"].items():  # every copy first: new-errors' api tests run the sibling rag job
        S.sh(["cp", "-c", "-R", v["path"], os.path.join(dest_root, repo)], check=True)
    order = list(h["repos"]) + [x for x in r["repos"] if x not in h["repos"]]
    for repo in order:
        v = r["repos"][repo]
        spec = CFG["candidates"][cid]["repos"][repo]
        st, env = S.stack_env(spec, run_id + "_s")
        dest = os.path.join(dest_root, repo)
        res["diffstat"][repo] = S.sh(["git", "diff", "--shortstat", v["base"]], cwd=dest).stdout.strip()
        changed = S.sh(["git", "diff", "--name-status", v["base"]], cwd=dest).stdout.splitlines()
        changed += ["?\t" + l[3:] for l in S.sh(["git", "status", "--porcelain"], cwd=dest).stdout.splitlines() if l[:2] == "??"]
        res["tampering"][repo] = [l for l in changed if l[0] in "MDR" and re.search(r"(^|/)(tests?/|.*\.spec\.ts$|.*_test\.py$|test_[^/]*\.py$)", l.split("\t")[-1])]
        # hidden tests
        hr = h["repos"].get(repo)
        if hr:
            for f in hr["files"]:
                os.makedirs(os.path.join(dest, f["dest"]), exist_ok=True)
                shutil.copy(os.path.join(ROOT, cid, "task", "hidden", repo, f["file"]), os.path.join(dest, f["dest"]))
            # a hidden test fails only if it fails 3 runs out of 3 (CRIBA §12.2, decision 5):
            # the whole hidden file is rerun, at most twice, while any test has not passed yet
            got, runs = {}, []
            for i in range(3):
                xml = os.path.join(meta, "hidden%s.%s.xml" % ("" if i == 0 else i, repo))
                p = S.sh(hr["cmd"] + st["junit"].format(junit=xml), cwd=dest, env=env, timeout=1800)
                open(xml + ".log", "w").write(p.stdout + p.stderr)
                this = S.junit_results(xml)
                runs.append(this)
                for n, o in this.items():
                    got[n] = "pass" if o == "pass" or got.get(n) == "pass" else o
                if got and all(o == "pass" for o in got.values()):
                    break
            res["hidden"][repo] = got
            res.setdefault("hidden_runs", {})[repo] = runs
            for f in hr["files"]:
                os.remove(os.path.join(dest, f["dest"], f["file"]))
        # suite, failures rerun twice in isolation (CRIBA §6)
        xml = os.path.join(meta, "suite.%s.xml" % repo)
        p = S.sh(st["suite"] + st["junit"].format(junit=xml), cwd=dest, env=env, timeout=3600)
        failed = sorted(n for n, o in S.junit_results(xml).items() if o == "fail")
        if p.returncode and not failed:
            failed = ["<suite rc=%s without JUnit failures>" % p.returncode]
            open(xml + ".log", "w").write((p.stdout + p.stderr)[-20000:])
        confirmed = failed
        if failed and not failed[0].startswith("<"):
            targets = junit_names_to_targets(spec["stack"], failed, dest)
            confirmed = set(failed)
            for i in (1, 2):
                rx = os.path.join(meta, "rerun%d.%s.xml" % (i, repo))
                S.sh(rerun_cmd(spec["stack"], targets) + st["junit"].format(junit=rx), cwd=dest, env=env, timeout=3600)
                again = S.junit_results(rx)
                if not again:  # pytest ran nothing (a bad node id aborts the whole run): never a confirmation
                    raise RuntimeError("rerun %d of %s collected no test: %s" % (i, repo, targets[:3]))
                confirmed &= {n for n in confirmed if again.get(n, "fail") == "fail"}
            quarantine = set(CFG["candidates"][cid].get("quarantine", []))
            confirmed = sorted(n for n in confirmed
                               if junit_names_to_targets(spec["stack"], [n], dest)[0] not in quarantine)
        res["suite"][repo] = dict(rc=p.returncode, failed=failed, regressions=confirmed)
        # gate
        gate = st["gate"]
        override = CFG["candidates"][cid].get("gate_override", {}).get(repo)
        g = S.sh(gate, cwd=dest, env=env, timeout=3600)
        ok = g.returncode == 0
        if not ok and override:
            files = " ".join("'%s'" % l.split("\t")[-1] for l in changed if l[0] != "D")
            g2 = S.sh(override.format(changed=files or "package.json"), cwd=dest, env=env, timeout=3600)
            ok = g2.returncode == 0
        open(os.path.join(meta, "gate.%s.log" % repo), "w").write((g.stdout + g.stderr)[-20000:])
        res["gate"][repo] = dict(rc=g.returncode, ok=ok, override_used=bool(override and g.returncode))
    res.update(judge(h, res))
    res["dropped_dbs"] = drop_run_dbs(run_id)
    json.dump(res, open(os.path.join(meta, "score.json"), "w"), indent=1)
    return res


def drop_run_dbs(run_id):
    """Drop every database of a run once it is scored: the agent's, the scorer's and the spec copies.
    They live on the container's tmpfs, and leaving them filled it on 2026-10-30 (CRIBA §17)."""
    tag = re.sub(r"[^a-z0-9_]", "_", run_id)
    q = ("SELECT datname FROM pg_database WHERE datname LIKE 'proxima_test_%%%s%%' OR datname LIKE '%s%%'"
         % (tag, "proxima_test_run_" + tag[:40]))
    names = S.sh(["docker", "exec", "rein-pilot-pg", "psql", "-U", "proxima", "-d", "postgres", "-tAc", q]).stdout.split()
    for n in names:
        S.sh(["docker", "exec", "rein-pilot-pg", "psql", "-U", "proxima", "-d", "postgres", "-c",
              'DROP DATABASE IF EXISTS "%s" WITH (FORCE)' % n])
    return names


def judge(h, res):
    """CRIBA §6: Correcta = every F (guards included) and D-spec passes, 0 confirmed regressions, gate ok.
    Correcta+ also needs every D-impl. A hidden test with no result (compile error, crash) is a failure."""
    outcome = {}
    for key, t in h["tests"].items():
        got = [o for repo in res["hidden"].values() for n, o in repo.items() if key in n]
        outcome[key] = dict(cls=t["class"], guard=bool(t.get("guard")),
                            ok=bool(got) and all(o == "pass" for o in got))
    f_ok = all(o["ok"] for o in outcome.values() if o["cls"] == "F")
    d_ok = all(o["ok"] for o in outcome.values() if o["cls"] == "D-spec")
    i_ok = all(o["ok"] for o in outcome.values() if o["cls"] == "D-impl")
    reg = sum(len(v["regressions"]) for v in res["suite"].values())
    gate = all(v["ok"] for v in res["gate"].values())
    correct = f_ok and d_ok and not reg and gate
    return dict(outcome=outcome, f_ok=f_ok, dspec_ok=d_ok, dimpl_ok=i_ok, regressions=reg, gate_ok=gate,
                correct=correct, correct_plus=correct and i_ok)


# ---------------------------------------------------------------- launcher
def schedule():
    runs = [(cid, rep) for cid in CFG["candidates"] for rep in range(1, REPS + 1)]
    random.Random(SEED).shuffle(runs)
    return runs


def spent():
    tot = 0.0
    # invalidated runs and sandbox tests (RUNS/_invalid, RUNS/_tests) were paid for too (CRIBA §15)
    for f in glob.glob(os.path.join(RUNS, "*", "meta", "metrics.json")) + \
            glob.glob(os.path.join(RUNS, "_*", "*", "meta", "metrics.json")):
        tot += json.load(open(f)).get("usd", 0)
    return tot


def verify_frozen():
    """Nothing runs unless the design and every package still match the frozen manifest."""
    mf = os.path.join(HERE, "manifest-criba.json")
    if not os.path.exists(mf):
        raise SystemExit("not frozen: run `screen_run.py freeze` first (CRIBA §13)")
    m = json.load(open(mf))
    bad = [p for p, h in m["files"].items() if S.sha256(os.path.join(os.path.dirname(HERE), p)) != h]
    for cid, files in m["tasks"].items():
        bad += ["%s/%s" % (cid, p) for p, h in files.items() if S.sha256(os.path.join(ROOT, cid, "task", p)) != h]
    if bad:
        raise SystemExit("changed since freeze: %s" % bad[:10])


def launch(dry=False):
    if not dry:
        verify_frozen()
    order =[x for x in schedule() if not os.path.exists(run_paths(rid(*x))[0])]
    print("pending:", len(order))
    if dry:
        for x in order:
            print(" ", rid(*x))
        return
    log = open(os.path.join(RUNS, "launch.log"), "a")
    lock, busy, stop = threading.Lock(), set(), threading.Event()
    scoreq = queue.Queue()

    def say(*a):
        line = time.strftime("%H:%M:%S ") + " ".join(str(x) for x in a)
        print(line, flush=True)
        log.write(line + "\n"); log.flush()

    def gatekeep():
        """True to start another run; waits while the 5-hour window is high; sets stop on hard limits."""
        while not stop.is_set():
            if os.path.exists(STOP):
                say("STOP file"); stop.set(); return False
            if spent() >= BUDGET_USD:
                say("budget reached: $%.2f" % spent()); stop.set(); return False
            q = claude_quota()
            if q["weekly"] is not None and q["weekly"] >= WEEKLY_STOP:
                say("weekly quota %s%% >= %s%%: stopping" % (q["weekly"], WEEKLY_STOP)); stop.set(); return False
            if q["session"] is not None and q["session"] >= SESSION_PAUSE:
                say("5-hour window %s%%: pausing 10 min" % q["session"]); time.sleep(600); continue
            if q["weekly"] is None:
                say("quota unreadable: stopping", q.get("raw")); stop.set(); return False
            return True
        return False

    def worker():
        while True:
            with lock:
                if stop.is_set():
                    return
                nxt = next((x for x in order if x[0] not in busy), None)
                if nxt is None:
                    return
                order.remove(nxt)
                busy.add(nxt[0])
            try:
                if gatekeep():
                    say("start", rid(*nxt))
                    m = collect(run(*nxt))
                    say("end", rid(*nxt), "rc=%s capped=%s wall=%s usd=%s leaks=%d" % (
                        m["rc"], m["capped"], m["wall_min"], m["usd"], len(m["leaks"])))
                    if m["leaks"]:
                        say("LEAK in", rid(*nxt), "-> stopping the screen"); stop.set()
                    scoreq.put(rid(*nxt))
            except BaseException as exc:  # a broken run is a methodological problem: stop (CRIBA §10)
                say("ERROR", rid(*nxt), repr(exc)[:500]); stop.set()
            finally:
                with lock:
                    busy.discard(nxt[0])

    def scorer():
        while True:
            x = scoreq.get()
            if x is None:
                return
            try:
                s = score(x)
                say("scored", x, "correct=%s correct+=%s F=%s Dspec=%s reg=%s gate=%s" % (
                    s["correct"], s["correct_plus"], s["f_ok"], s["dspec_ok"], s["regressions"], s["gate_ok"]))
            except BaseException as exc:
                say("SCORE ERROR", x, repr(exc)[:500])

    sc = threading.Thread(target=scorer); sc.start()
    ws = [threading.Thread(target=worker) for _ in range(CONCURRENT)]
    for w in ws:
        w.start(); time.sleep(5)
    for w in ws:
        w.join()
    scoreq.put(None); sc.join()
    say("launch done; spent $%.2f" % spent())


def freeze():
    """CRIBA §4.3/§13: sha256 of the design, the code and every task package, before the first N0 run."""
    doc = os.path.join(os.path.dirname(HERE), "CRIBA.md")
    out = dict(frozen=time.time(), claude=S.sh(["claude", "--version"]).stdout.strip(), model=H.MODEL,
               seed=SEED, files={}, tasks={})
    for p in (doc, os.path.join(HERE, "screen.json"), os.path.join(HERE, "screen.py"), __file__,
              os.path.join(HERE, "harness.py"), os.path.join(HERE, "intents.py")):
        out["files"][os.path.relpath(p, os.path.dirname(HERE))] = S.sha256(p)
    for cid in CFG["candidates"]:
        d = os.path.join(ROOT, cid, "task")
        v = json.load(open(os.path.join(d, "validation.json")))
        if not v["ok"] or v.get("only"):
            raise SystemExit("%s: validation is not a full OK" % cid)
        out["tasks"][cid] = {os.path.relpath(p, d): S.sha256(p) for p in sorted(glob.glob(os.path.join(d, "**", "*"), recursive=True))
                             if os.path.isfile(p) and "/checks/" not in p}
    dst = os.path.join(HERE, "manifest-criba.json")
    if os.path.exists(dst):
        raise SystemExit("already frozen: %s" % dst)
    json.dump(out, open(dst, "w"), indent=1)
    print("frozen", dst, sum(len(t) for t in out["tasks"].values()), "task files")


def table():
    for cid, rep in schedule():
        run_id = rid(cid, rep)
        _, meta = run_paths(run_id)
        m = json.load(open(os.path.join(meta, "metrics.json"))) if os.path.exists(os.path.join(meta, "metrics.json")) else {}
        s = json.load(open(os.path.join(meta, "score.json"))) if os.path.exists(os.path.join(meta, "score.json")) else {}
        print("%-28s wall=%-5s usd=%-6s capped=%-5s correct=%-5s correct+=%-5s reg=%s gate=%s" % (
            run_id, m.get("wall_min"), m.get("usd"), m.get("capped"), s.get("correct"), s.get("correct_plus"),
            s.get("regressions"), s.get("gate_ok")))


if __name__ == "__main__":
    a = sys.argv[1:]
    if a[0] == "schedule":
        for x in schedule():
            print(rid(*x))
    elif a[0] == "run":
        print(run(a[1], int(a[2])))
    elif a[0] == "score":
        print(json.dumps(score(a[1]), indent=1)[:4000])
    elif a[0] == "launch":
        launch(dry="--dry" in a)
    elif a[0] == "table":
        table()
    elif a[0] == "freeze":
        freeze()
    else:
        raise SystemExit(__doc__)
