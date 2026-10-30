#!/usr/bin/env python3
"""E2 — context strategies A..E on the same model, same lean launch config, same commit.

  A native            the agent's own tools only
  B native+CodeGraph  + a short hint that the `codegraph` CLI and a fresh index are available
  C native+Graphify   + a short hint that graphify-out/graph.json and the `graphify` CLI exist
  D context pack      + a deterministic pack (ripgrep over the task's identifiers) in the prompt
  E pack+CodeGraph    D + B

Runs are shuffled (seeded) and interleaved across strategies to spread drift; one warm-up call
primes the shared system-prompt cache first. Every run is a fresh `claude -p` session.
Usage: e2_context.py --repos <dir> --out <dir> [--model haiku] [--reps 2] [--only Q2,Q4] [--strategies A,B]
"""
import argparse, json, os, random, re, shutil, subprocess, sys, time

HERE = os.path.dirname(os.path.abspath(__file__))
NONTEST = ("Count only non-test files: exclude anything under a tests/ directory and files named "
           "test_*.py, *_test.py or conftest.py (scripts/ and src/ count).")
ANSWER = ('End your reply with one fenced ```json block of the form {"files": ["<repo-relative path>", ...]} '
          "and nothing after it.")

QUESTIONS = {
    "Q1": dict(repo="rein", kind="callers", gt="gt_q1.json", idents=["brake_reason"],
               text="In this repository, which Python files contain a call to the function `brake_reason` "
                    "(including the file that defines it, if it calls it)? " + NONTEST + " " + ANSWER),
    "Q2": dict(repo="api", kind="callers", gt="gt_q2.json", idents=["resolve_effective_sale_price"],
               text="In this repository, which Python files contain a call to the function "
                    "`resolve_effective_sale_price` (including the file that defines it, if it calls it)? "
                    + NONTEST + " " + ANSWER),
    "Q3": dict(repo="api", kind="impact", gt="gt_q3.json", idents=["invalidate_website_composition"],
               text="We are going to add a required keyword argument to `invalidate_website_composition`. "
                    "List every Python file whose code calls it and would therefore have to change "
                    "(including the defining file if it calls it itself). " + NONTEST + " " + ANSWER),
    "Q5": dict(repo="api", kind="callers", gt="gt_q5.json", idents=["resolve_effective_sale_price", "ctx"],
               text="Which non-test Python files contain at least one call to `resolve_effective_sale_price` "
                    "that passes a `ctx` argument (as the keyword `ctx=` or as the fifth positional argument)? "
                    + NONTEST + " " + ANSWER),
    "Q6": dict(repo="api", kind="callers", gt="gt_q6.json", idents=["resolve_effective_sale_price"],
               text="Impact analysis, two levels deep. List every Python file that contains a function which "
                    "either calls `resolve_effective_sale_price` directly (level 1), or calls one of those level-1 "
                    "functions (level 2). For level 2 only follow level-1 functions whose name is defined exactly "
                    "once in the repository's non-test code. " + NONTEST + " " + ANSWER),
    "Q4": dict(repo="rein-bug", kind="bugfix", gt=None,
               idents=[], keywords=["brake", "weekly", "usage", "codex", "percent"],
               text="Bug report: `rein ship` keeps launching Codex workers when Orca reports Codex weekly "
                    "usage at exactly 90%, although the default brake should stop a provider at 90%. Find "
                    "the cause and fix it with the smallest change. Run the relevant unit tests with "
                    "`python3 -m unittest tests.<module>` (never the whole suite). Do not commit. "
                    'End your reply with one fenced ```json block {"files": [<files you changed>]}.'),
}

HINT_CG = ("A fresh CodeGraph index of this repository is available through the `codegraph` CLI (run it with "
           "Bash): `codegraph callers <symbol> -p . -l 500`, `codegraph callees <symbol> -p .`, "
           "`codegraph impact <symbol> -p .`, `codegraph query <text> -p .`, `codegraph explore <query> -p .`, "
           "`codegraph context <task> -p .`. Use it when it helps.")
HINT_GF = ("A Graphify knowledge graph of this repository is in graphify-out/graph.json (summary in "
           "graphify-out/GRAPH_REPORT.md), queried with the `graphify` CLI (run it with Bash): "
           "`graphify query \"<question>\"`, `graphify explain \"<node>\"`, `graphify path \"A\" \"B\"`. "
           "Use it when it helps.")

LEAN = ["--strict-mcp-config", "--disable-slash-commands", "--tools", "Bash,Read,Edit,Write,Grep,Glob",
        "--settings", json.dumps({"enabledPlugins": {"aws-startup-advisor@claude-plugins-official": False,
                                                      "superpowers@anthropic-plugin-directory": False,
                                                      "playwright@anthropic-plugin-directory": False,
                                                      "rein@rein-agentic-kit": False}}),
        "--permission-mode", "acceptEdits", "--allowedTools", "Bash,Read,Edit,Write,Grep,Glob"]

PACK_CAP = 7000


def context_pack(repo, q):
    """Deterministic: files matching the task's identifiers (or keywords), most distinct terms first,
    with their matching lines, capped at PACK_CAP characters."""
    terms = q.get("idents") or q.get("keywords") or []
    hits = {}
    for t in terms:
        args = ["rg", "-n", "--type", "py", "-i" if not q.get("idents") else "-w", t, "."]
        out = subprocess.run(args, cwd=repo, capture_output=True, text=True).stdout
        for line in out.splitlines():
            f, _, rest = line.partition(":")
            f = f[2:] if f.startswith("./") else f
            hits.setdefault(f, {}).setdefault(t, []).append(rest[:160])
    ranked = sorted(hits.items(), key=lambda kv: (-len(kv[1]), -sum(len(v) for v in kv[1].values()), kv[0]))
    lines = ["Context pack (generated deterministically: ripgrep for %s; %d matching files):"
             % (", ".join("`%s`" % t for t in terms), len(ranked))]
    size = len(lines[0])
    for f, by in ranked:
        block = [f] + ["  %s" % x for t in sorted(by) for x in by[t][:6]]
        b = "\n".join(block)
        if size + len(b) > PACK_CAP:
            lines.append("(pack truncated at %d chars; %d more files not shown)" % (PACK_CAP, len(ranked) - (len(lines) - 1)))
            break
        lines.append(b)
        size += len(b) + 1
    return "\n".join(lines)


HANDOFF = ("Handoff from the previous worker on this change (its result was checked by Rein): the non-test "
           "files that call `resolve_effective_sale_price` are: scripts/bootstrap_crm_goal_merchant.py, "
           "src/modules/banners/application/banner_composition.py, src/modules/catalog/application/sale_pricing.py, "
           "src/modules/catalog/application/use_cases.py, src/modules/commerce/application/buyer_prices.py, "
           "src/modules/commerce/application/use_cases.py, src/modules/pos/application/service.py, "
           "src/modules/quotes/application/use_cases.py, src/modules/storefront/application/feed.py, "
           "src/modules/storefront/application/pc_builder_catalog.py. It is defined in "
           "src/modules/catalog/application/sale_pricing.py as resolve_effective_sale_price(variant, product, "
           "promotions=None, now=None, ctx=None).")


def build_prompt(strategy, repo, q):
    parts = []
    if strategy == "H":
        parts.append(HANDOFF)
    if strategy in ("D", "E"):
        parts.append(context_pack(repo, q))
    parts.append("Task: " + q["text"])
    return "\n\n".join(parts)


FORCE_CG = (" Start with CodeGraph: answer from `codegraph callers`/`codegraph impact` first and use grep or "
            "file reads only to check what it returns.")
FORCE_GF = (" Start with Graphify: answer from `graphify query`/`graphify explain` first and use grep or file "
            "reads only to check what it returns.")


def append_system(strategy):
    return {"B": HINT_CG, "C": HINT_GF, "E": HINT_CG,
            "BF": HINT_CG + FORCE_CG, "CF": HINT_GF + FORCE_GF}.get(strategy, "")


def parse_stream(path):
    m = dict(tool_calls=0, by_tool={}, bash_cg=0, bash_gf=0, reads=0, files_read=set(), first_tool_ms=None,
             first_edit_ms=None, result="", num_turns=None, duration_ms=None, usage={}, cost=None, is_error=None)
    t0 = None
    for line in open(path, errors="replace"):
        try:
            d = json.loads(line)
        except ValueError:
            continue
        now = time.time()
        if d.get("type") == "system" and t0 is None:
            t0 = d
        if d.get("type") == "assistant":
            for b in (d.get("message") or {}).get("content") or []:
                if b.get("type") != "tool_use":
                    continue
                m["tool_calls"] += 1
                n = b.get("name")
                m["by_tool"][n] = m["by_tool"].get(n, 0) + 1
                inp = b.get("input") or {}
                if n == "Bash":
                    c = inp.get("command", "")
                    m["bash_cg"] += bool(re.search(r"\bcodegraph\b", c))
                    m["bash_gf"] += bool(re.search(r"\bgraphify\b", c))
                    for f in re.findall(r"(?:cat|sed -n '[^']*'|head|tail)\s+(?:-\S+\s+)*([\w./-]+\.py)", c):
                        m["files_read"].add(f)
                if n == "Read":
                    m["reads"] += 1
                    m["files_read"].add(inp.get("file_path", ""))
        if d.get("type") == "result":
            m["result"] = d.get("result") or ""
            m["num_turns"] = d.get("num_turns")
            m["duration_ms"] = d.get("duration_ms")
            m["usage"] = d.get("usage") or {}
            m["cost"] = d.get("total_cost_usd")
            m["is_error"] = d.get("is_error")
    m["files_read"] = len(m["files_read"])
    return m


def answer_files(text):
    blocks = re.findall(r"```json\s*(\{.*?\})\s*```", text or "", re.S)
    if not blocks:
        return None
    try:
        files = json.loads(blocks[-1]).get("files") or []
    except ValueError:
        return None
    return sorted(set(os.path.normpath(f).lstrip("./") if not f.startswith("/") else f for f in files))


def score(q, out_dir, answer, workdir):
    if q["kind"] == "bugfix":
        diff = subprocess.run(["git", "diff", "--name-only"], cwd=workdir, capture_output=True, text=True).stdout.split()
        t = subprocess.run([sys.executable, "-m", "unittest", "tests.test_quota", "tests.test_ship_tiers"],
                           cwd=workdir, capture_output=True, text=True)
        # the bug is an uncommitted edit of quota.py: an exact revert leaves `git diff` empty
        return dict(target_tests_pass=t.returncode == 0, changed=diff, exact_revert=diff == [],
                    minimal=diff in ([], ["plugins/rein/lib/quota.py"]))
    gt = set(json.load(open(os.path.join(out_dir, "..", q["gt"])))["files"])
    if answer is None:
        return dict(precision=0.0, recall=0.0, f1=0.0, parsed=False, gt=len(gt))
    a = set(answer)
    tp = len(a & gt)
    p = tp / len(a) if a else 0.0
    r = tp / len(gt) if gt else 0.0
    return dict(precision=p, recall=r, f1=(2 * p * r / (p + r)) if p + r else 0.0, parsed=True, gt=len(gt),
                missed=sorted(gt - a), extra=sorted(a - gt))


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--repos", required=True)
    ap.add_argument("--out", required=True)
    ap.add_argument("--model", default="haiku")
    ap.add_argument("--reps", type=int, default=2)
    ap.add_argument("--only", default="")
    ap.add_argument("--strategies", default="A,B,C,D,E")
    ap.add_argument("--seed", type=int, default=7)
    ap.add_argument("--max-turns", default="40")
    a = ap.parse_args()
    os.makedirs(a.out, exist_ok=True)
    qs = [k for k in QUESTIONS if not a.only or k in a.only.split(",")]
    jobs = [(q, s, r) for q in qs for s in a.strategies.split(",") for r in range(1, a.reps + 1)]
    random.Random(a.seed).shuffle(jobs)
    # warm-up: primes the shared system prompt + tools cache for this config
    subprocess.run(["claude", "-p", "Reply with exactly: OK", "--model", a.model, "--output-format", "json",
                    "--max-turns", "1", "--no-session-persistence"] + LEAN, cwd=os.path.join(a.repos, "rein"),
                   capture_output=True, text=True)
    results_path = os.path.join(a.out, "results.jsonl")
    done = set()
    if os.path.exists(results_path):
        for line in open(results_path):
            d = json.loads(line)
            done.add((d["q"], d["strategy"], d["rep"]))
    for i, (qk, s, rep) in enumerate(jobs, 1):
        if (qk, s, rep) in done:
            continue
        q = QUESTIONS[qk]
        src = os.path.join(a.repos, q["repo"])
        workdir = src
        if q["kind"] == "bugfix":
            workdir = os.path.join(a.out, "wt-%s-%s-%d" % (qk, s, rep))
            shutil.rmtree(workdir, ignore_errors=True)
            shutil.copytree(src, workdir, symlinks=True)
        prompt = build_prompt(s, workdir, q)
        args = ["claude", "-p", prompt, "--model", a.model, "--output-format", "stream-json", "--verbose",
                "--max-turns", a.max_turns, "--no-session-persistence"] + LEAN
        sysx = append_system(s)
        if sysx:
            args += ["--append-system-prompt", sysx]
        log = os.path.join(a.out, "%s-%s-%d.jsonl" % (qk, s, rep))
        t0 = time.time()
        with open(log, "w") as fh:
            subprocess.run(args, cwd=workdir, stdout=fh, stderr=subprocess.DEVNULL, timeout=1800)
        wall = time.time() - t0
        m = parse_stream(log)
        ans = answer_files(m["result"])
        sc = score(q, a.out, ans, workdir)
        u = m["usage"]
        row = dict(q=qk, strategy=s, rep=rep, model=a.model, wall_s=round(wall, 1), turns=m["num_turns"],
                   tool_calls=m["tool_calls"], by_tool=m["by_tool"], codegraph_calls=m["bash_cg"],
                   graphify_calls=m["bash_gf"], files_read=m["files_read"], prompt_chars=len(prompt),
                   input=u.get("input_tokens"), cache_write=u.get("cache_creation_input_tokens"),
                   cache_read=u.get("cache_read_input_tokens"), output=u.get("output_tokens"),
                   api_equiv_usd=m["cost"], is_error=m["is_error"], answer=ans, score=sc)
        with open(results_path, "a") as fh:
            fh.write(json.dumps(row) + "\n")
        print("[%d/%d] %s %s r%d  turns=%s tools=%s cg=%s gf=%s  %s" % (
            i, len(jobs), qk, s, rep, row["turns"], row["tool_calls"], row["codegraph_calls"],
            row["graphify_calls"], {k: (round(v, 2) if isinstance(v, float) else v) for k, v in sc.items()
                                    if k in ("f1", "precision", "recall", "target_tests_pass", "minimal", "full_suite_pass")}),
              flush=True)


if __name__ == "__main__":
    main()
