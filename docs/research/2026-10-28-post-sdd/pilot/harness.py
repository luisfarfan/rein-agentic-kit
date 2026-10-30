#!/usr/bin/env python3
"""Rein pilot harness: prepare isolated runs, drive the native arms, score, collect, leak-scan.

Usage:
  harness.py prepare <task> <arm> <rep>        # clone the frozen fixture into runs/<id>/<repo>
  harness.py native  <task> <arm> <rep>        # arms N0 N1 N2 P2 (claude -p; codex auditor)
  harness.py score   <run-id> [--tree PATH]    # hidden tests + suite on a clone of the result
  harness.py collect <run-id>                  # transcripts -> tokens, API-eq cost, timeline
  harness.py leaks   <run-id>                  # forbidden reads/commands in every transcript
  harness.py baseline <task>                   # suite failures at the base (regression reference)

Never writes outside <root>/runs, <root>/logs and ~/.claude/rein (rein gate cache).
Stop everything: touch <root>/STOP (checked before every phase).
"""
import glob, json, os, re, shlex, shutil, subprocess, sys, time, uuid
import xml.etree.ElementTree as ET

HERE = os.path.dirname(os.path.abspath(__file__))
CFG = json.load(open(os.path.join(HERE, "tasks.json")))
ROOT = CFG["root"]
REIN_LIB = "/Users/luisfarfan/Developer/labs/agents/rein/plugins/rein/lib"
PLUGIN = os.path.dirname(REIN_LIB)
STOP = os.path.join(ROOT, "STOP")
STUB = os.path.join(ROOT, "engineering-stub")
MODEL = CFG["implementer_model"]
PRICES = {"opus-5-5": (4, 20, 0.20), "sonnet-5-5": (2, 10, 0.20), "haiku-5-5": (0.10, 0.50, 0.01)}
FORBIDDEN = [r"/Developer/products/", r"/orca/workspaces/proxima-", r"rein-pilot/oracle", r"rein-pilot/validate",
             r"rein-pilot/fixtures", r"\.claude/projects", r"\.codex/sessions", r"\borca search\b", r"git log --all",
             r"\bdf887b43", r"\bf9010b0", r"\be05ccf7", r"openspec/changes/archive", r"2026-10-28-post-sdd/pilot",
             r"zz-hidden|zz_hidden"]

NATIVE_PROMPT = """You are working in this repository (the current directory). Implement the change described below.

Work as a careful senior engineer using Claude Code: read the relevant code first, plan briefly, implement, and add or update tests that cover every "Done when" item. Run the project's own checks (lint, typecheck and the relevant tests; the commands are in flow.config.json, mise.toml, package.json or CLAUDE.md) until they pass. Then review your own diff critically -- correctness, edge cases, races, contract mismatches -- and fix what you find. Commit your work on the current branch with a clear message. Do not push. Work only inside this directory. No one will answer questions: decide within the request and say what you assumed.

{intent}"""

PLAN_ADDON = """
A plan for this change was written and confirmed beforehand. Follow its tasks in order, its decisions (binding) and its verification commands:

{plan}"""

FIX_PROMPT = """An independent reviewer (a different model) audited your change and recorded these blocking problems. Fix each one, add a regression test for it, run the project's checks, and commit. Do not push.

{findings}"""


def rid(task, arm, rep):
    return "%s-%s-r%s" % (task, arm.lower(), rep)


def paths(run_id):
    task = run_id.split("-")[0] if not run_id.startswith("obs-cors") else "obs-cors"
    base = os.path.join(ROOT, "runs", run_id)
    return task, base, os.path.join(base, "pilot-" + run_id), os.path.join(base, "meta")


def ev(meta, kind, **kw):
    kw.update(kind=kind, t=time.time())
    with open(os.path.join(meta, "events.jsonl"), "a") as fh:
        fh.write(json.dumps(kw) + "\n")


def sh(cmd, cwd=None, timeout=None, env=None, check=False):
    p = subprocess.run(cmd, cwd=cwd, shell=isinstance(cmd, str), capture_output=True, text=True,
                       timeout=timeout, env=env)
    if check and p.returncode != 0:
        raise SystemExit("failed: %s\n%s%s" % (cmd, p.stdout[-2000:], p.stderr[-2000:]))
    return p


def stop_check():
    if os.path.exists(STOP):
        raise SystemExit("STOP file present: %s" % STOP)


def quota():
    p = sh(["orca", "account", "list", "--json"], timeout=60)
    try:
        return json.loads(p.stdout)
    except ValueError:
        return {"raw": p.stdout[-2000:]}


# ---------------------------------------------------------------- prepare
def prepare(task, arm, rep):
    t = CFG["tasks"][task]
    run_id = rid(task, arm, rep)
    _, base, repo, meta = paths(run_id)
    if os.path.exists(base):
        raise SystemExit("exists: %s" % base)
    os.makedirs(meta)
    fx = os.path.join(ROOT, "fixtures", task)
    sh(["git", "clone", "-q", "--no-hardlinks", os.path.join(fx, "repo"), repo], check=True)
    # The repos' own Claude hooks call ../proxima-engineering/scripts/hooks/*: give every clone (and
    # Orca's worktree folder for arm A) a sibling stub holding ONLY those scripts -- never the real
    # repo, whose openspec archive holds the historical solutions.
    os.symlink(STUB, os.path.join(base, "proxima-engineering"))
    if arm == "A":
        ws = os.path.expanduser("~/orca/workspaces/" + os.path.basename(repo))
        os.makedirs(ws, exist_ok=True)
        if not os.path.lexists(os.path.join(ws, "proxima-engineering")):
            os.symlink(STUB, os.path.join(ws, "proxima-engineering"))
    sh(["git", "remote", "remove", "origin"], cwd=repo, check=True)
    head = sh(["git", "rev-parse", "HEAD"], cwd=repo, check=True).stdout.strip()
    assert head == t["base"], (head, t["base"])
    for sha in (t.get("final"), t.get("prefix")):
        if sha and sh(["git", "cat-file", "-e", sha + "^{commit}"], cwd=repo).returncode == 0:
            raise SystemExit("LEAK: %s present in %s" % (sha, repo))
    with open(os.path.join(repo, ".git", "info", "exclude"), "a") as fh:
        fh.write("\n.rein/\n.env.local\nnode_modules/\n")
    if t["deps"] == "node":
        sh(["cp", "-c", "-R", os.path.join(t["product_repo"], "node_modules"), os.path.join(repo, "node_modules")], check=True)
    else:
        shutil.copy(os.path.join(repo, ".env.local.example"), os.path.join(repo, ".env.local"))
        sh(["uv", "sync", "--frozen", "-q"], cwd=repo, check=True, timeout=600)
    if arm in ("P2", "A"):
        plan = open(os.path.join(fx, "plan", "tasks.md")).read()
        plan = re.sub(r"(?m)^(\s+- Repo: ).+$", r"\g<1>" + os.path.basename(repo), plan)
        d = os.path.join(repo, "openspec", "changes", t["change"])
        os.makedirs(d, exist_ok=True)
        open(os.path.join(d, "tasks.md"), "w").write(plan)
        with open(os.path.join(repo, ".git", "info", "exclude"), "a") as fh:
            fh.write("openspec/changes/%s/\n" % t["change"])
    if arm != "A":
        sh(["git", "checkout", "-q", "-b", "pilot/work"], cwd=repo, check=True)
    json.dump(dict(run_id=run_id, task=task, arm=arm, rep=rep, repo=repo, base=head, prepared=time.time()),
              open(os.path.join(meta, "run.json"), "w"), indent=1)
    print(run_id, repo)
    return run_id


# ---------------------------------------------------------------- native arms
def settings_for(arm, meta):
    s = {"permissions": {"deny": [
        "Read(//Users/luisfarfan/Developer/products/**)", "Read(//Users/luisfarfan/orca/**)",
        "Read(//Users/luisfarfan/Developer/sandbox/rein-pilot/oracle/**)",
        "Read(//Users/luisfarfan/Developer/sandbox/rein-pilot/validate/**)",
        "Read(//Users/luisfarfan/Developer/sandbox/rein-pilot/fixtures/**)",
        "Read(//Users/luisfarfan/.claude/projects/**)", "Read(//Users/luisfarfan/.codex/**)",
        "Bash(orca search:*)", "Bash(git log --all:*)"]}}
    if arm in ("N1", "N2", "P2"):
        hook = ("cd \"$CLAUDE_PROJECT_DIR\" && S=$(date +%%s); rein gate . --tier task --base develop > %s/gate.log 2>&1; rc=$?; "
                "printf '{\"kind\":\"gate\",\"start\":%%s,\"t\":%%s,\"rc\":%%s}\\n' $S $(date +%%s) $rc >> %s/events.jsonl; "
                "if [ $rc -eq 1 ]; then echo 'rein gate is RED -- fix it before you stop:' >&2; tail -80 %s/gate.log >&2; exit 2; fi; exit 0"
                ) % (meta, meta, meta)
        s["hooks"] = {"Stop": [{"hooks": [{"type": "command", "command": hook, "timeout": 1200}]}]}
    p = os.path.join(meta, "settings.json")
    json.dump(s, open(p, "w"), indent=1)
    return p


def claude(repo, meta, prompt, settings, sid=None, resume=None, label="implement"):
    stop_check()
    budget = CFG["budget"]
    argv = ["claude", "-p", "--model", MODEL, "--strict-mcp-config", "--permission-mode", "bypassPermissions", "--settings", settings,
            "--output-format", "json", "--max-budget-usd", str(budget["claude_usd_per_session"])]
    argv += ["--resume", resume] if resume else ["--session-id", sid]
    ev(meta, "phase_start", phase=label, session=resume or sid)
    try:
        p = subprocess.run(argv + [prompt], cwd=repo, capture_output=True, text=True,
                           timeout=budget["run_wall_minutes"] * 60, env=test_env(os.path.basename(repo)))
        out, rc = p.stdout, p.returncode
    except subprocess.TimeoutExpired as exc:
        out, rc = (exc.stdout or b"").decode() if isinstance(exc.stdout, bytes) else (exc.stdout or ""), "timeout"
    ev(meta, "phase_end", phase=label, session=resume or sid, rc=rc)
    open(os.path.join(meta, "%s.out.json" % label), "w").write(out or "")
    return rc


def audit_brief(task, repo):
    sys.path.insert(0, REIN_LIB)
    import ship, roles  # noqa: E402  (read-only use of Rein's own builders)
    plan = open(os.path.join(ROOT, "fixtures", task, "plan", "tasks.md")).read()
    why = re.search(r"## Why\n(.*?)\n## ", plan, re.S).group(1).strip()
    changed = sh(["git", "diff", "--name-only", "develop"], cwd=repo).stdout.split()
    changed += [l[3:] for l in sh(["git", "status", "--porcelain"], cwd=repo).stdout.splitlines() if l[:2] == "??"]
    prof = roles.profile("reviewer", PLUGIN)["text"]
    return ship.build_audit_spec({"why": why}, {repo: sorted(set(changed))}, prof, audit_root=repo,
                                 change=CFG["tasks"][task]["change"],
                                 gate_commands={repo: ship.audit_gate_command(repo)})


def codex_audit(task, repo, meta):
    stop_check()
    brief = audit_brief(task, repo)
    open(os.path.join(meta, "audit-brief.md"), "w").write(brief)
    ev(meta, "phase_start", phase="audit")
    a = CFG["auditor"]
    try:
        p = subprocess.run(["codex", "exec", "-m", a["model"], "--dangerously-bypass-approvals-and-sandbox",
                            "--skip-git-repo-check", "-C", repo, brief], capture_output=True, text=True,
                           timeout=CFG["budget"]["run_wall_minutes"] * 60, env=test_env(os.path.basename(repo)))
        rc = p.returncode
        open(os.path.join(meta, "audit.out.txt"), "w").write((p.stdout or "")[-20000:] + (p.stderr or "")[-5000:])
    except subprocess.TimeoutExpired:
        rc = "timeout"
    ev(meta, "phase_end", phase="audit", rc=rc)
    eps = sorted(glob.glob(os.path.join(repo, ".rein", "reviews", "**", "*.json"), recursive=True), key=os.path.getmtime)
    if not eps:
        return None, []
    e = json.load(open(eps[-1]))
    def sev(f):
        return (f.get("severity") or "") if isinstance(f, dict) else str(f).split(":")[0]
    def txt(f):
        return ("BLOCKING: " + f.get("text", "")) if isinstance(f, dict) else str(f)
    blocking = [txt(f) for f in e.get("findings") or [] if sev(f).strip().upper() == "BLOCKING"]
    return e.get("verdict"), blocking


def native(task, arm, rep):
    run_id = rid(task, arm, rep)
    _, base, repo, meta = paths(run_id)
    fx = os.path.join(ROOT, "fixtures", task)
    intent = open(os.path.join(fx, "intent.md")).read()
    prompt = NATIVE_PROMPT.format(intent=intent)
    if arm == "P2":
        prompt += PLAN_ADDON.format(plan=open(os.path.join(repo, "openspec", "changes", CFG["tasks"][task]["change"], "tasks.md")).read())
    settings = settings_for(arm, meta)
    sid = str(uuid.uuid4())
    ev(meta, "run_start", arm=arm, quota=quota())
    claude(repo, meta, prompt, settings, sid=sid)
    rounds = 0
    if arm in ("N2", "P2"):
        verdict, blocking = codex_audit(task, repo, meta)
        ev(meta, "verdict", verdict=verdict, blocking=blocking, round=1)
        if blocking:
            rounds = 1
            claude(repo, meta, FIX_PROMPT.format(findings="\n".join(blocking)), settings, resume=sid, label="fix")
    head = sh(["git", "rev-parse", "HEAD"], cwd=repo).stdout.strip()
    dirty = sh(["git", "status", "--porcelain"], cwd=repo).stdout
    ev(meta, "run_end", head=head, dirty=dirty[:2000], fix_rounds=rounds, quota=quota(), session=sid)
    print(run_id, "done", head)


def fixround(run_id):
    """Run a fix round that the harness failed to run (a parser bug), recording the gap so the
    arm's wall-clock excludes the time it sat waiting on the harness."""
    task, base, repo, meta = paths(run_id)
    evs = [json.loads(l) for l in open(os.path.join(meta, "events.jsonl"))]
    end = [e for e in evs if e["kind"] == "run_end"][-1]
    sid = end["session"]
    eps = sorted(glob.glob(os.path.join(repo, ".rein", "reviews", "**", "*.json"), recursive=True), key=os.path.getmtime)
    e = json.load(open(eps[-1]))
    blocking = ["BLOCKING: " + f["text"] for f in e.get("findings") or [] if isinstance(f, dict) and f.get("severity") == "BLOCKING"]
    if not blocking:
        raise SystemExit("no blocking findings")
    ev(meta, "harness_gap", start=end["t"], reason="fix round skipped by a findings-parser bug; resumed")
    ev(meta, "verdict", verdict=e.get("verdict"), blocking=blocking, round=1, corrected=True)
    claude(repo, meta, FIX_PROMPT.format(findings="\n".join(blocking)), os.path.join(meta, "settings.json"), resume=sid, label="fix")
    head = sh(["git", "rev-parse", "HEAD"], cwd=repo).stdout.strip()
    dirty = sh(["git", "status", "--porcelain"], cwd=repo).stdout
    ev(meta, "run_end", head=head, dirty=dirty[:2000], fix_rounds=1, quota=quota(), session=sid)
    print(run_id, "fix round done", head)


# ---------------------------------------------------------------- scoring
def junit(path):
    out = {"passed": [], "failed": [], "skipped": []}
    if not os.path.exists(path):
        return out
    for tc in ET.parse(path).getroot().iter("testcase"):
        name = "%s::%s" % (tc.get("classname"), tc.get("name"))
        if tc.find("failure") is not None or tc.find("error") is not None:
            out["failed"].append(name)
        elif tc.find("skipped") is not None:
            out["skipped"].append(name)
        else:
            out["passed"].append(name)
    return out


def test_env(tag):
    """Each test run gets its own Postgres test database (tests/db_setup.py reads PROXIMA_TEST_DB):
    never the shared default `proxima_test`, which another checkout may be using."""
    env = dict(os.environ)
    env["PROXIMA_TEST_DB"] = "proxima_test_pilot_" + re.sub(r"[^a-z0-9_]", "_", tag.lower())[:40]
    return env


def drop_test_dbs():
    """Drop every database the pilot created (`proxima_test_pilot_*`) and nothing else."""
    sql = ("SELECT format('DROP DATABASE IF EXISTS %I WITH (FORCE)', datname) FROM pg_database "
           "WHERE datname LIKE 'proxima\\_test\\_pilot\\_%' \\gexec\n")
    p = subprocess.run(["docker", "exec", "-i", "proxima-suite-db", "sh", "-c",
                        'psql -U "$POSTGRES_USER" -d postgres -At -v ON_ERROR_STOP=1'],
                       input=sql, capture_output=True, text=True, timeout=300)
    return p.returncode, (p.stdout + p.stderr)[-500:]


def run_tests(tree, cmd, stack, xml, tag="x"):
    full = cmd + (" --junitxml=%s" % xml if stack == "python" else " --reporter=junit --outputFile=%s" % xml)
    t0 = time.time()
    p = sh(full, cwd=tree, timeout=1800, env=test_env(tag))
    r = junit(xml)
    r.update(rc=p.returncode, secs=round(time.time() - t0, 1), tail=(p.stdout + p.stderr)[-1500:])
    return r


def scoring_copy(task, tree, dest):
    t = CFG["tasks"][task]
    sh(["cp", "-c", "-R", tree, dest], check=True)
    for kind, h in t["hidden"].items():
        shutil.copy(os.path.join(ROOT, "fixtures", task, "hidden", h["file"]), os.path.join(dest, h["dest"]))


def score(run_id, tree=None):
    task, base, repo, meta = paths(run_id)
    t = CFG["tasks"][task]
    tree = tree or repo
    dest = os.path.join(base, "score")
    if os.path.exists(dest):
        raise SystemExit("score copy exists: %s" % dest)
    scoring_copy(task, tree, dest)
    res = {"tree": tree}
    for kind, h in t["hidden"].items():
        res[kind] = run_tests(dest, h["cmd"], t["stack"], os.path.join(meta, "hidden-%s.xml" % kind), tag=run_id + "_h")
    for kind, h in t["hidden"].items():  # the suite must not run our hidden files
        os.remove(os.path.join(dest, h["dest"], h["file"]))
    res["suite"] = run_tests(dest, t["suite"], t["stack"], os.path.join(meta, "suite.xml"), tag=run_id + "_s")
    bl = json.load(open(os.path.join(ROOT, "fixtures", task, "baseline.json")))
    suspect = set(res["suite"]["failed"]) - set(bl["failed"])
    if suspect:  # a regression must fail twice: the suite has environment-dependent flakes
        again = run_tests(dest, t["suite"], t["stack"], os.path.join(meta, "suite2.xml"), tag=run_id + "_s2")
        suspect &= set(again["failed"])
    res["regressions"] = sorted(suspect)
    diff = sh(["git", "diff", "--shortstat", t["base"]], cwd=tree).stdout.strip()
    res["diffstat"] = diff
    res["correct"] = (not any(res[k]["failed"] for k in t["hidden"]) and not res["regressions"]
                      and all(res[k]["passed"] for k in t["hidden"]))
    json.dump(res, open(os.path.join(meta, "score.json"), "w"), indent=1)
    print(json.dumps({k: (v if not isinstance(v, dict) else {kk: (len(vv) if isinstance(vv, list) else vv)
                                                              for kk, vv in v.items() if kk != "tail"})
                      for k, v in res.items()}, indent=1))


def rescore(run_id):
    """Re-run the CURRENT hidden tests in an existing score copy (after a hidden test was fixed
    for validity) and update score.json; the suite result is kept."""
    task, base, repo, meta = paths(run_id)
    t = CFG["tasks"][task]
    dest = os.path.join(base, "score")
    res = json.load(open(os.path.join(meta, "score.json")))
    for kind, h in t["hidden"].items():
        shutil.copy(os.path.join(ROOT, "fixtures", task, "hidden", h["file"]), os.path.join(dest, h["dest"]))
        res[kind] = run_tests(dest, h["cmd"], t["stack"], os.path.join(meta, "hidden-%s.xml" % kind), tag=run_id + "_h")
        os.remove(os.path.join(dest, h["dest"], h["file"]))
    res["correct"] = (not any(res[k]["failed"] for k in t["hidden"]) and not res["regressions"]
                      and all(res[k]["passed"] for k in t["hidden"]))
    res["hidden_version"] = {k: sha_of(os.path.join(ROOT, "fixtures", task, "hidden", h["file"])) for k, h in t["hidden"].items()}
    json.dump(res, open(os.path.join(meta, "score.json"), "w"), indent=1)
    print(run_id, "correct" if res["correct"] else "NOT correct",
          {k: (len(res[k]["passed"]), len(res[k]["failed"])) for k in t["hidden"]}, res[list(t["hidden"])[0]]["failed"][:3])


def sha_of(path):
    import hashlib
    return hashlib.sha256(open(path, "rb").read()).hexdigest()[:16]


def baseline(task):
    t = CFG["tasks"][task]
    dest = os.path.join(ROOT, "fixtures", task, "baseline-tree")
    if not os.path.exists(dest):
        sh(["git", "clone", "-q", "--no-hardlinks", os.path.join(ROOT, "fixtures", task, "repo"), dest], check=True)
        if t["deps"] == "node":
            sh(["cp", "-c", "-R", os.path.join(t["product_repo"], "node_modules"), os.path.join(dest, "node_modules")], check=True)
        else:
            shutil.copy(os.path.join(dest, ".env.local.example"), os.path.join(dest, ".env.local"))
            sh(["uv", "sync", "--frozen", "-q"], cwd=dest, check=True, timeout=600)
    runs = [run_tests(dest, t["suite"], t["stack"], os.path.join(ROOT, "fixtures", task, "baseline%d.xml" % i),
                      tag="base_%s_%d" % (task, i)) for i in (1, 2)]
    a, b = set(runs[0]["failed"]), set(runs[1]["failed"])
    r = dict(failed=sorted(a | b), stable=sorted(a & b), flaky=sorted(a ^ b), runs=[
        dict(passed=len(x["passed"]), failed=len(x["failed"]), skipped=len(x["skipped"]), secs=x["secs"]) for x in runs])
    json.dump(r, open(os.path.join(ROOT, "fixtures", task, "baseline.json"), "w"), indent=1)
    print(task, "base suite x2:", r["runs"], "stable failures", len(r["stable"]), "flaky", len(r["flaky"]))


# ---------------------------------------------------------------- transcripts
def sessions_for(run_id, t0, t1):
    task, base, repo, meta = paths(run_id)
    name = os.path.basename(repo)
    out = []
    for f in glob.glob(os.path.expanduser("~/.claude/projects/*/*.jsonl")):
        d = os.path.basename(os.path.dirname(f))
        if name in d and os.path.getmtime(f) >= t0 - 60:
            out.append(("claude", f))
    for f in glob.glob(os.path.expanduser("~/.codex/sessions/**/*.jsonl"), recursive=True):
        if os.path.getmtime(f) < t0 - 60:
            continue
        with open(f, errors="ignore") as fh:
            head = fh.read(20000)
        if name in head:
            out.append(("codex", f))
    return out


def ts(x):
    from datetime import datetime
    return datetime.fromisoformat(x.replace("Z", "+00:00")).timestamp()


def parse_claude(f):
    r = dict(provider="claude", file=f, turns=0, read=0, write5=0, write1=0, input=0, output=0, usd=0.0, start=None, end=None, model="")
    for line in open(f, errors="ignore"):
        try:
            d = json.loads(line)
        except ValueError:
            continue
        if d.get("timestamp"):
            x = ts(d["timestamp"]); r["start"] = r["start"] or x; r["end"] = x
        m = d.get("message") or {}
        u = m.get("usage") if d.get("type") == "assistant" else None
        if not u:
            continue
        model = (m.get("model") or "").replace("claude-", "")
        r["model"] = model
        pi, po, pr = next((v for k, v in PRICES.items() if model.startswith(k)), PRICES["sonnet-5-5"])
        cc = u.get("cache_creation") or {}
        w5, w1 = cc.get("ephemeral_5m_input_tokens"), cc.get("ephemeral_1h_input_tokens")
        if w5 is None and w1 is None:
            w5, w1 = 0, u.get("cache_creation_input_tokens", 0)
        r["turns"] += 1; r["read"] += u.get("cache_read_input_tokens", 0); r["write5"] += w5 or 0; r["write1"] += w1 or 0
        r["input"] += u.get("input_tokens", 0); r["output"] += u.get("output_tokens", 0)
        r["usd"] += (u.get("cache_read_input_tokens", 0) * pr + ((w5 or 0) * 1.25 + (w1 or 0) * 2) * pi
                     + u.get("input_tokens", 0) * pi + u.get("output_tokens", 0) * po) / 1e6
    return r


def parse_codex(f):
    r = dict(provider="codex", file=f, turns=0, input=0, cached=0, output=0, start=None, end=None, model="")
    last = None
    for line in open(f, errors="ignore"):
        try:
            d = json.loads(line)
        except ValueError:
            continue
        if d.get("timestamp"):
            x = ts(d["timestamp"]); r["start"] = r["start"] or x; r["end"] = x
        p = d.get("payload") or {}
        if p.get("type") == "token_count" and (p.get("info") or {}).get("total_token_usage"):
            last = p["info"]["total_token_usage"]
        if d.get("type") == "response_item" and p.get("type") == "message" and p.get("role") == "assistant":
            r["turns"] += 1
        if d.get("type") == "turn_context" and p.get("model"):
            r["model"] = p["model"]
    if last:
        r["cached"] = last.get("cached_input_tokens", 0); r["input"] = last.get("input_tokens", 0) - r["cached"]
        r["output"] = last.get("output_tokens", 0)
    return r


def union(iv):
    tot, cur = 0.0, None
    for a, b in sorted(iv):
        if cur is None or a > cur[1]:
            tot += (cur[1] - cur[0]) if cur else 0
            cur = [a, b]
        else:
            cur[1] = max(cur[1], b)
    return tot + ((cur[1] - cur[0]) if cur else 0)


def collect(run_id):
    task, base, repo, meta = paths(run_id)
    evs = [json.loads(l) for l in open(os.path.join(meta, "events.jsonl"))]
    t0 = min(e["t"] for e in evs if e["kind"] in ("run_start",))
    t1 = max(e["t"] for e in evs if e["kind"] in ("run_end",))
    gap_iv = [(e["start"], e["t"]) for e in evs if e["kind"] == "harness_gap"]
    sess = [parse_claude(f) if p == "claude" else parse_codex(f) for p, f in sessions_for(run_id, t0, t1)]
    sess = [s for s in sess if s["start"] and s["start"] <= t1 + 60 and s["end"] >= t0 - 60]
    gates = [(e["start"], e["t"]) for e in evs if e["kind"] == "gate"]
    for gl in glob.glob(os.path.join(repo, ".rein", "ship", "*.gates.jsonl")):  # arm A: Rein's own gate log
        for line in open(gl):
            try:
                g = json.loads(line)
            except ValueError:
                continue
            secs = sum((s.get("elapsedMs") or 0) for s in g.get("slots") or [] if not s.get("cached")) / 1000
            gates.append((g["at"] - secs, g["at"]))

    def minus_gaps(iv):
        out = []
        for a, b in iv:
            for ga, gb in gap_iv:
                if a < ga < b:  # an interval spanning the harness gap: drop the gap
                    out.append((a, ga)); a = min(max(a, gb), b)
            out.append((a, b))
        return [(a, b) for a, b in out if b > a]
    active = minus_gaps([(s["start"], s["end"]) for s in sess] + gates)
    gaps = sum(e["t"] - e["start"] for e in evs if e["kind"] == "harness_gap")
    wall = t1 - t0 - gaps
    m = dict(run_id=run_id, harness_gap_min=round(gaps / 60, 1), wall_min=round(wall / 60, 1), active_min=round(union(active) / 60, 1),
             idle_min=round((wall - union(active)) / 60, 1), gate_min=round(union(gates) / 60, 1),
             gate_runs=len(gates), gate_red=sum(1 for e in evs if e["kind"] == "gate" and e.get("rc") == 1),
             claude_sessions=sum(1 for s in sess if s["provider"] == "claude"),
             codex_sessions=sum(1 for s in sess if s["provider"] == "codex"),
             claude_turns=sum(s["turns"] for s in sess if s["provider"] == "claude"),
             claude_tokens={k: sum(s.get(k, 0) for s in sess if s["provider"] == "claude") for k in ("read", "write5", "write1", "input", "output")},
             claude_usd=round(sum(s.get("usd", 0) for s in sess if s["provider"] == "claude"), 2),
             codex_tokens={k: sum(s.get(k, 0) for s in sess if s["provider"] == "codex") for k in ("cached", "input", "output")},
             interventions=sum(1 for e in evs if e["kind"] == "owner_answer"),
             fix_rounds=next((e.get("fix_rounds") for e in reversed(evs) if e["kind"] == "run_end"), None),
             quota_before=next((e.get("quota") for e in evs if e["kind"] == "run_start"), None),
             quota_after=next((e.get("quota") for e in reversed(evs) if e["kind"] == "run_end"), None),
             sessions=[{k: v for k, v in s.items() if k != "file"} | {"file": os.path.basename(s["file"])} for s in sess])
    json.dump(m, open(os.path.join(meta, "metrics.json"), "w"), indent=1)
    print(json.dumps({k: v for k, v in m.items() if k not in ("sessions", "quota_before", "quota_after")}, indent=1))


def leaks(run_id):
    task, base, repo, meta = paths(run_id)
    evs = [json.loads(l) for l in open(os.path.join(meta, "events.jsonl"))]
    t0 = min(e["t"] for e in evs); t1 = max(e["t"] for e in evs)
    rx = re.compile("|".join(FORBIDDEN))
    hits = []
    for prov, f in sessions_for(run_id, t0, t1):
        for line in open(f, errors="ignore"):
            if not rx.search(line):
                continue
            try:
                d = json.loads(line)
            except ValueError:
                continue
            calls = []
            m = d.get("message") or {}
            if isinstance(m.get("content"), list):
                calls += [json.dumps(b.get("input")) for b in m["content"] if isinstance(b, dict) and b.get("type") == "tool_use"]
            p = d.get("payload") or {}
            if p.get("type") in ("function_call", "custom_tool_call", "local_shell_call"):
                calls.append(json.dumps(p.get("arguments") or p.get("input") or p.get("action")))
            for c in calls:
                for mm in rx.finditer(c):
                    hits.append(dict(provider=prov, file=os.path.basename(f), match=mm.group(0), call=c[:240]))
    json.dump(hits, open(os.path.join(meta, "leaks.json"), "w"), indent=1)
    print("%s: %d forbidden tool-call matches" % (run_id, len(hits)))
    for h in hits[:20]:
        print(" ", h["provider"], h["match"], "|", h["call"][:160])


if __name__ == "__main__":
    a = sys.argv[1:]
    cmd = a[0]
    if cmd == "prepare": prepare(a[1], a[2], a[3])
    elif cmd == "native": native(a[1], a[2], a[3])
    elif cmd == "score": score(a[1], a[a.index("--tree") + 1] if "--tree" in a else None)
    elif cmd == "collect": collect(a[1])
    elif cmd == "leaks": leaks(a[1])
    elif cmd == "baseline": baseline(a[1])
    elif cmd == "dropdbs": print(drop_test_dbs())
    elif cmd == "fixround": fixround(a[1])
    elif cmd == "rescore": rescore(a[1])
    else: raise SystemExit(__doc__)
