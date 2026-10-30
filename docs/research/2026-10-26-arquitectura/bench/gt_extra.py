#!/usr/bin/env python3
"""Ground truth for Q5 (callers passing `ctx`) and Q6 (two-level impact) on proxima-api.
Usage: gt_extra.py q5 <repo> <gt_q2.json>   |   gt_extra.py q6 <repo>"""
import ast, collections, json, os, subprocess, sys

T = "resolve_effective_sale_price"


def is_test(f):
    b = os.path.basename(f)
    return "/tests/" in "/" + f or b.startswith("test_") or b.endswith("_test.py") or "conftest" in b


def name_of(call):
    fn = call.func
    return fn.id if isinstance(fn, ast.Name) else getattr(fn, "attr", None)


def q5(repo, gt2):
    out = []
    for f in json.load(open(gt2))["files"]:
        tree = ast.parse(open(os.path.join(repo, f)).read())
        for n in ast.walk(tree):
            if isinstance(n, ast.Call) and name_of(n) == T and (
                    any(k.arg == "ctx" for k in n.keywords) or len(n.args) >= 5):
                out.append(f)
                break
    return {"files": sorted(set(out))}


def q6(repo):
    files = [f for f in subprocess.run(["git", "-C", repo, "ls-files", "*.py"], capture_output=True,
                                       text=True).stdout.split() if not is_test(f)]
    defs, calls = collections.defaultdict(list), []
    for f in files:
        try:
            tree = ast.parse(open(os.path.join(repo, f), errors="replace").read())
        except (SyntaxError, ValueError):
            continue
        for n in ast.walk(tree):
            if isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef)):
                defs[n.name].append(f)
                for c in ast.walk(n):
                    if isinstance(c, ast.Call) and name_of(c):
                        calls.append((f, n.name, name_of(c)))
    hop1 = {(f, fn) for f, fn, c in calls if c == T and fn != T}
    names1 = {fn for _, fn in hop1 if len(defs[fn]) == 1}
    hop2 = {(f, fn) for f, fn, c in calls if c in names1 and fn != c}
    return {"hop1": sorted(hop1), "hop2": sorted(hop2 - hop1), "files": sorted({f for f, _ in hop1 | hop2}),
            "ambiguous_hop1": sorted({fn for _, fn in hop1} - names1)}


if __name__ == "__main__":
    print(json.dumps(q5(sys.argv[2], sys.argv[3]) if sys.argv[1] == "q5" else q6(sys.argv[2])))
