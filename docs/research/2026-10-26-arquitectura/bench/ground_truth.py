#!/usr/bin/env python3
"""Deterministic ground truth for call-site questions: every (file, enclosing def) whose AST calls
`name(...)` or `<x>.name(...)`. Usage: ground_truth.py <repo> <name> [--exclude-tests] [--json]
       ground_truth.py <repo> --candidates   (rank functions by caller-file spread)"""
import ast, collections, json, os, subprocess, sys


def py_files(repo):
    out = subprocess.run(["git", "-C", repo, "ls-files", "*.py"], capture_output=True, text=True).stdout.split()
    return [f for f in out if f.endswith(".py")]


def is_test(f):
    b = os.path.basename(f)
    return "/tests/" in "/" + f or b.startswith("test_") or b.endswith("_test.py") or "conftest" in b


def calls(repo, files):
    """yield (file, enclosing, callee_name)"""
    for f in files:
        try:
            tree = ast.parse(open(os.path.join(repo, f), errors="replace").read())
        except (SyntaxError, ValueError):
            continue
        stack = []

        def visit(node):
            pushed = isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef))
            if pushed:
                stack.append(node.name)
            if isinstance(node, ast.Call):
                fn = node.func
                n = fn.id if isinstance(fn, ast.Name) else fn.attr if isinstance(fn, ast.Attribute) else None
                if n:
                    yield_list.append((f, ".".join(stack) or "<module>", n))
            for ch in ast.iter_child_nodes(node):
                visit(ch)
            if pushed:
                stack.pop()
        yield_list = []
        visit(tree)
        for x in yield_list:
            yield x


def defs(repo, files):
    out = collections.defaultdict(list)
    for f in files:
        try:
            tree = ast.parse(open(os.path.join(repo, f), errors="replace").read())
        except (SyntaxError, ValueError):
            continue
        for n in ast.walk(tree):
            if isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef)):
                out[n.name].append(f)
    return out


def main():
    repo, name = sys.argv[1], sys.argv[2]
    files = py_files(repo)
    if "--exclude-tests" in sys.argv:
        files = [f for f in files if not is_test(f)]
    if name == "--candidates":
        d = defs(repo, files)
        uniq = {k: v for k, v in d.items() if len(v) == 1 and not k.startswith("__") and len(k) > 8}
        spread = collections.defaultdict(set)
        sites = collections.Counter()
        for f, enc, n in calls(repo, files):
            if n in uniq:
                spread[n].add(f)
                sites[n] += 1
        rows = sorted(((len(spread[k]), sites[k], k, uniq[k][0]) for k in spread), reverse=True)
        for r in rows:
            if 4 <= r[0] <= 14:
                print("%2d files %3d sites  %s  (def %s)" % r)
        return
    hits = sorted(set((f, enc) for f, enc, n in calls(repo, files) if n == name))
    if "--json" in sys.argv:
        print(json.dumps({"name": name, "files": sorted(set(f for f, _ in hits)), "sites": hits}))
    else:
        for h in hits:
            print("%s :: %s" % h)


if __name__ == "__main__":
    main()
