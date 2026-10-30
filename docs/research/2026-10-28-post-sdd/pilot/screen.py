#!/usr/bin/env python3
"""Difficulty screen, phase 0: standalone fixtures and a CI-equivalent baseline per candidate.

  screen.py fixture <cand>|all    clone each repo at its base; drop every ref and object past it
  screen.py baseline <cand>|all   run the repo's own suite twice on a fresh copy; write baseline.json
  screen.py intent <cand>        task/plan.md (+ task/intent_extra.md) -> task/intent.md
  screen.py validate <cand> [v]  run the hidden tests 3x on every version in task/hidden.json; check §4.3
  screen.py gate <cand>          run only the gate on an existing baseline copy
  screen.py report                one line per candidate: fixture ok, suite green, flaky, minutes

Never runs an agent. Never writes to the product repos (they are only cloned from).
"""
import json, os, re, shutil, subprocess, sys, time
import xml.etree.ElementTree as ET

HERE = os.path.dirname(os.path.abspath(__file__))
CFG = json.load(open(os.path.join(HERE, "screen.json")))
ROOT, PROD = CFG["root"], CFG["products"]
STUB = os.path.join(os.path.dirname(ROOT), "engineering-stub")


def sh(cmd, cwd=None, env=None, check=False, timeout=None):
    shell = isinstance(cmd, str)
    r = subprocess.run(cmd, cwd=cwd, env=env, shell=shell, capture_output=True, text=True, timeout=timeout,
                       executable="/bin/zsh" if shell else None)
    if check and r.returncode:
        raise SystemExit("FAILED %s\n%s%s" % (cmd, r.stdout[-2000:], r.stderr[-2000:]))
    return r


def fixture(cid):
    for repo, spec in CFG["candidates"][cid]["repos"].items():
        dst = os.path.join(ROOT, cid, "fixture", repo)
        if os.path.exists(dst):
            continue
        base = sh(["git", "-C", os.path.join(PROD, repo), "rev-parse", spec["base"] + "^{commit}"], check=True).stdout.strip()
        sh(["git", "clone", "-q", "--no-hardlinks", "--no-checkout", os.path.join(PROD, repo), dst], check=True)
        sh(["git", "checkout", "-q", "-B", "develop", base], cwd=dst, check=True)
        refs = sh(["git", "for-each-ref", "--format=%(refname)"], cwd=dst).stdout.split()
        for ref in refs:
            if ref != "refs/heads/develop":
                sh(["git", "update-ref", "-d", ref], cwd=dst)
        sh(["git", "remote", "remove", "origin"], cwd=dst)
        sh(["git", "reflog", "expire", "--expire=now", "--all"], cwd=dst, check=True)
        sh(["git", "gc", "-q", "--prune=now"], cwd=dst, check=True, timeout=1800)
        later = sh(["git", "rev-list", "--all", "--not", base], cwd=dst).stdout.split()
        assert not later, "%s: %d commits past the base" % (repo, len(later))
        json.dump(dict(repo=repo, base=base, built=time.time()), open(dst + ".json", "w"))
        print(cid, repo, base[:10], "fixture ok", flush=True)


def stack_env(spec, tag):
    st = CFG["stacks"][spec["stack"]]
    env = dict(os.environ, CI="1", **st.get("env", {}))
    if st.get("db_env"):
        env[st["db_env"]] = "proxima_test_screen_" + tag.replace("-", "_")
    return st, env


def baseline(cid, runs=2):
    c = CFG["candidates"][cid]
    out = dict(candidate=cid, repos={})
    for repo, spec in c["repos"].items():
        wt = os.path.join(ROOT, cid, "baseline", repo)
        if not os.path.exists(wt):
            sh(["git", "clone", "-q", "--no-hardlinks", os.path.join(ROOT, cid, "fixture", repo), wt], check=True)
            stub = os.path.join(ROOT, cid, "baseline", "proxima-engineering")
            if not os.path.lexists(stub):
                os.symlink(STUB, stub)
        st, env = stack_env(spec, cid)
        t0 = time.time()
        for step in st["setup"]:
            r = sh(step, cwd=wt, env=env, timeout=1800)
            if r.returncode:
                out["repos"][repo] = dict(setup_failed=step, tail=(r.stdout + r.stderr)[-3000:])
                break
        else:
            setup_min, res = round((time.time() - t0) / 60, 1), []
            for i in range(runs):
                s = time.time()
                r = sh(st["suite"], cwd=wt, env=env, timeout=3600)
                log = os.path.join(ROOT, cid, "baseline", "%s.run%d.log" % (repo, i + 1))
                open(log, "w").write(r.stdout + r.stderr)
                res.append(dict(rc=r.returncode, minutes=round((time.time() - s) / 60, 1), log=log,
                                tail=(r.stdout + r.stderr)[-1500:]))
            g = sh(st["gate"], cwd=wt, env=env, timeout=3600) if st.get("gate") else None
            if g is not None:
                open(os.path.join(ROOT, cid, "baseline", "%s.gate.log" % repo), "w").write(g.stdout + g.stderr)
            out["repos"][repo] = dict(setup_minutes=setup_min, gate_rc=g and g.returncode,
                                      runs=res, green=all(x["rc"] == 0 for x in res),
                                      flaky=len({x["rc"] == 0 for x in res}) > 1)
    json.dump(out, open(os.path.join(ROOT, cid, "baseline.json"), "w"), indent=1)
    print(cid, {r: v.get("green", "SETUP-FAILED") for r, v in out["repos"].items()}, flush=True)


# ---------------------------------------------------------------- task packages (CRIBA §4)
# <root>/<cand>/task/
#   plan.md            the change's frozen tasks.md (intent source; also the P2 arm's plan)
#   intent_extra.md    optional: public names a hidden test needs that neither base nor plan gives (§4.2.1)
#   intent.md          generated: what every arm receives
#   hidden/<repo>/...  the hidden test files
#   hidden.json        {"repos": {repo: {"files": [{"file", "dest"}], "cmd": "..."}},
#                       "defects": {key: {"escaped": bool, "source": "...", "prefix": V, "postfix": V}},
#                       "versions": {"base": V, "final": V, "stub": V},
#                       "tests": {name-substring: {"class": "F"|"D-spec"|"D-impl", "defect": key?, "guard": bool?}}}
#   where V = {repo: sha | {"at": sha, "patch": "<file in task/>"}}
#   validation.json    generated by `validate`

def task_dir(cid):
    return os.path.join(ROOT, cid, "task")


def intent_cmd(cid):
    sys.path.insert(0, HERE)
    from intents import intent
    d = task_dir(cid)
    text = intent(open(os.path.join(d, "plan.md")).read())
    extra = os.path.join(d, "intent_extra.md")
    if os.path.exists(extra):
        text += "\n## Interfaces the checks use\n" + open(extra).read().strip() + "\n"
    open(os.path.join(d, "intent.md"), "w").write(text)
    print(text)


def junit_results(path):
    out = {}
    if not os.path.exists(path):
        return out
    for tc in ET.parse(path).getroot().iter("testcase"):
        name = "%s::%s" % (tc.get("classname"), tc.get("name"))
        bad = tc.find("failure") is not None or tc.find("error") is not None
        out[name] = "skip" if (not bad and tc.find("skipped") is not None) else ("fail" if bad else "pass")
    return out


# §12 note 1: the local Postgres sometimes drops a connection. A test that must pass, passes in some
# run and failed in the others only on this, is reported as noise, not as a non-deterministic test.
NOISE_RX = re.compile(r"TimeoutError|rejected SSL upgrade")


def junit_noise(path):
    """Names of the failed testcases whose failure text is connection noise."""
    out = set()
    if not os.path.exists(path):
        return out
    for tc in ET.parse(path).getroot().iter("testcase"):
        bad = tc.find("failure") if tc.find("failure") is not None else tc.find("error")
        if bad is None:
            continue
        text = (bad.get("message") or "") + "\n" + (bad.text or "")
        # a test that runs spec files in a subprocess: every failed spec must have failed on noise
        failed = re.findall(r"FAILED \S+ - ([^\n\\]*)", text)
        if all(NOISE_RX.search(f) for f in failed) if failed else NOISE_RX.search(bad.get("message") or ""):
            out.add("%s::%s" % (tc.get("classname"), tc.get("name")))
    return out


def expected(t, version, defects):
    """§4.3 as data: what each test class must do on each version (None = no requirement)."""
    cls, k = t["class"], t.get("defect")
    if version == "stub":
        return None if t.get("guard") else "fail"
    if cls == "F":
        if version == "base":
            return "pass" if t.get("guard") else "fail"
        if version == "final" or version.startswith("postfix:"):
            return "pass"
        return None
    if version == "final":
        return "fail" if defects[k]["escaped"] else "pass"
    if version == "prefix:" + k:
        return "fail"
    if version == "postfix:" + k:
        return "pass"
    return None


def checkout(cid, repo, v):
    wt = os.path.join(ROOT, cid, "validate", repo)
    if not os.path.exists(wt):
        sh(["git", "clone", "-q", "--no-hardlinks", os.path.join(PROD, repo), wt], check=True)
    at, patch = (v, None) if isinstance(v, str) else (v["at"], v.get("patch"))
    sh(["git", "checkout", "-q", "-f", at], cwd=wt, check=True)
    sh(["git", "clean", "-q", "-fdx", "-e", "node_modules", "-e", ".venv"], cwd=wt, check=True)
    if patch:
        sh(["git", "apply", "--whitespace=nowarn", os.path.join(task_dir(cid), patch)], cwd=wt, check=True)
    return wt


def validate(cid, only=None, reps=3):
    d = task_dir(cid)
    h = json.load(open(os.path.join(d, "hidden.json")))
    versions = dict(h["versions"])
    for k, dv in h.get("defects", {}).items():
        versions["prefix:" + k], versions["postfix:" + k] = dv["prefix"], dv["postfix"]
    if only:
        versions = {only: versions[only]}
    spec = {"stack": None}
    res, noise_runs = {}, {}
    for vname, vmap in versions.items():
        res[vname] = {}
        for repo, hr in h["repos"].items():
            if repo not in vmap:
                continue
            wt = checkout(cid, repo, vmap[repo])
            st, env = stack_env(dict(spec, stack=repo), "val_" + cid)
            for step in st["setup"]:
                r = sh(step, cwd=wt, env=env, timeout=1800)
                if r.returncode:
                    raise SystemExit("%s %s setup failed: %s" % (vname, repo, (r.stdout + r.stderr)[-1500:]))
            for f in hr["files"]:
                os.makedirs(os.path.join(wt, f["dest"]), exist_ok=True)
                shutil.copy(os.path.join(d, "hidden", repo, f["file"]), os.path.join(wt, f["dest"]))
            runs, noisy = [], []
            for i in range(reps):
                xml = os.path.join(ROOT, cid, "validate", "%s.%s.%d.xml" % (repo, vname.replace(":", "_"), i))
                if os.path.exists(xml):
                    os.remove(xml)
                r = sh(hr["cmd"] + st["junit"].format(junit=xml), cwd=wt, env=env, timeout=1800)
                got = junit_results(xml)
                if not got:
                    got = {"<no junit>": "fail"}
                    open(xml + ".log", "w").write(r.stdout + r.stderr)
                runs.append(got)
                noisy.append(junit_noise(xml))
            res[vname][repo] = runs
            noise_runs.setdefault(vname, {})[repo] = noisy
    # judge
    problems, table, noise = [], {}, []
    defects = h.get("defects", {})
    for vname, repos in res.items():
        for repo, runs in repos.items():
            names = sorted(set().union(*runs))
            for n in names:
                outs = [r.get(n, "missing") for r in runs]
                keys = [k for k in h["tests"] if k in n]
                if len(keys) != 1:
                    problems.append("%s: %s matches %d test keys" % (vname, n, len(keys)))
                    continue
                t = h["tests"][keys[0]]
                exp = expected(t, vname, defects)
                table.setdefault(n.split("::")[-1], {"key": keys[0]})[vname] = outs[0] if len(set(outs)) == 1 else "FLAKY %s" % outs
                fails = [i for i, o in enumerate(outs) if o != "pass"]
                if (len(set(outs)) > 1 and exp == "pass" and len(fails) < len(outs)
                        and all(n in noise_runs[vname][repo][i] for i in fails)):
                    noise.append("%s: %s %s" % (vname, n, outs))
                elif len(set(outs)) > 1:
                    problems.append("%s: %s not deterministic %s" % (vname, n, outs))
                elif exp and outs[0] != exp:
                    problems.append("%s: %s [%s] is %s, must %s" % (vname, n, t["class"], outs[0], exp))
    if not only:
        for k in h["tests"]:
            if not any(row["key"] == k for row in table.values()):
                problems.append("test key %s matched no test" % k)
    out = dict(candidate=cid, validated=time.time(), only=only, table=table, problems=problems, noise=noise,
               ok=not problems, hidden_sha256={repo + "/" + f["file"]: sha256(os.path.join(d, "hidden", repo, f["file"]))
                                              for repo, hr in h["repos"].items() for f in hr["files"]})
    json.dump(out, open(os.path.join(d, "validation%s.json" % ("" if not only else "." + only.replace(":", "_"))), "w"), indent=1)
    for n, row in table.items():
        print("%-64s %-7s %s" % (n[:64], h["tests"][row["key"]]["class"],
                                 " ".join("%s=%s" % x for x in row.items() if x[0] != "key")))
    if noise:
        print("CONNECTION NOISE (§12 note 1):\n  " + "\n  ".join(noise))
    print("OK" if not problems else "PROBLEMS:\n  " + "\n  ".join(problems))


def sha256(path):
    import hashlib
    return hashlib.sha256(open(path, "rb").read()).hexdigest()


def gate(cid):
    """Run only the gate on an existing baseline copy (for baselines recorded before the gate step existed)."""
    p = os.path.join(ROOT, cid, "baseline.json")
    b = json.load(open(p))
    for repo, spec in CFG["candidates"][cid]["repos"].items():
        st, env = stack_env(spec, cid)
        g = sh(st["gate"], cwd=os.path.join(ROOT, cid, "baseline", repo), env=env, timeout=3600)
        open(os.path.join(ROOT, cid, "baseline", "%s.gate.log" % repo), "w").write(g.stdout + g.stderr)
        b["repos"][repo]["gate_rc"] = g.returncode
    json.dump(b, open(p, "w"), indent=1)
    print(cid, "gate", {r: v.get("gate_rc") for r, v in b["repos"].items()}, flush=True)


def report():
    for cid, c in CFG["candidates"].items():
        p = os.path.join(ROOT, cid, "baseline.json")
        if not os.path.exists(p):
            print("%-22s %-7s no baseline yet" % (cid, c["prior"]))
            continue
        b = json.load(open(p))
        for repo, v in b["repos"].items():
            if "setup_failed" in v:
                print("%-22s %-7s %-22s SETUP FAILED: %s" % (cid, c["prior"], repo, v["setup_failed"]))
            else:
                print("%-22s %-7s %-22s green=%s flaky=%s rc=%s gate=%s min=%s" % (
                    cid, c["prior"], repo, v["green"], v["flaky"], [x["rc"] for x in v["runs"]], v.get("gate_rc"),
                    [x["minutes"] for x in v["runs"]]))


if __name__ == "__main__":
    cmd, arg = sys.argv[1], (sys.argv[2] if len(sys.argv) > 2 else "all")
    ids = list(CFG["candidates"]) if arg == "all" else [arg]
    if cmd == "report":
        report()
    elif cmd == "intent":
        intent_cmd(arg)
    elif cmd == "validate":
        validate(arg, sys.argv[3] if len(sys.argv) > 3 else None)
    else:
        for cid in ids:
            {"fixture": fixture, "baseline": baseline, "gate": gate}[cmd](cid)
