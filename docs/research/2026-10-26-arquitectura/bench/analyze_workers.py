#!/usr/bin/env python3
"""Observational analysis of real `rein ship` worker transcripts (Claude Code + Codex).

Read-only: parses ~/.claude/projects/**.jsonl and ~/.codex/sessions/**.jsonl whose first user
brief contains the Rein ship worker preamble, plus the ship state files. Writes workers.jsonl and
summary.json next to --out. No network, no model calls.
"""
import argparse, collections, glob, json, os, re, shlex, statistics, sys
from datetime import datetime

MARK = "You are a Rein ship worker"
AUDIT_MARK = "Audit the change as a whole; you wrote none of it"
REAUDIT_MARK = "This is a re-audit after a fix round"
WS = os.path.expanduser("~/orca/workspaces/")

META_RE = re.compile(r"(AGENTS\.md|CLAUDE\.md|SKILL\.md|GEMINI\.md|\.claude/rules/|AGENT_START_HERE|feature-map\.md)")
TEST_RE = re.compile(r"\b(pytest|vitest|jest|npm (run )?test|pnpm (run )?test|yarn test|go test|cargo test|unittest|ng test|mise run test|make test|playwright)\b")
ORCA_RE = re.compile(r"\borca(-dev)?\b.*\b(orchestration|terminal|worktree)\b")
CG_RE = re.compile(r"\bcodegraph\b")
GF_RE = re.compile(r"\bgraphify\b")
READ_CMD = re.compile(r"^\s*(cat|sed -n|head|tail|nl|less|bat)\b")
BASH_WRITE = re.compile(r"(sed -i|perl -p?i|\bcat\s*>|\bcat\s*<<[^\n]*>|(?<![0-9&>])>>?\s*(?!&|/dev/null)['\"]?[\w./-]+\.[A-Za-z0-9]+|\btee\s+(-a\s+)?[\w./-]+\.\w+|python3?\s+-\s*<<|\bapply_patch\b|git apply|patch -p|\.write_text\(|open\([^)]*['\"]w)")
SEARCH_CMD = re.compile(r"^\s*(rg|grep|ag|ack|fd|find|git grep|ls)\b")


def ts(s):
    try:
        return datetime.fromisoformat(s.replace("Z", "+00:00")).timestamp()
    except Exception:
        return None


def kind_of(text):
    if REAUDIT_MARK in text:
        return "reaudit"
    if AUDIT_MARK in text:
        return "audit"
    if "The audit found these blocking problems" in text:
        return "audit_fix"
    if "The full test suite of" in text and "failed; fix it" in text:
        return "gate_fix"
    if "The previous attempt failed this check" in text:
        return "retry"
    return "task"


def task_of(text):
    m = re.search(r"Task (\S+?):", text)
    return m.group(1) if m else ""


def cwd_parts(cwd):
    cwd = (cwd or "").replace("file://", "")
    if cwd.startswith(WS):
        rest = cwd[len(WS):].split("/")
        return rest[0], rest[1] if len(rest) > 1 else ""
    return "", ""


def new_rec(provider, path):
    return dict(provider=provider, file=path, model="", cwd="", repo="", change="", task="", kind="",
                start=None, end=None, first_edit=None, turns=0, tool_calls=0,
                cat=collections.Counter(), files_read=[], files_edited=[], searches=[],
                tok=dict(input=0, cache_read=0, cache_write=0, output=0), ctx_max=0,
                meta_reads=0, tool_calls_before_edit=0, codegraph=0, graphify=0, serena=0,
                brief_chars=0)


def norm(p, cwd):
    if not p:
        return ""
    p = p.replace("file://", "")
    if not os.path.isabs(p) and cwd:
        p = os.path.normpath(os.path.join(cwd, p))
    # make worktree-relative so different workers of a change compare
    r, c = cwd_parts(p)
    if r and c:
        p = p[len(WS) + len(r) + len(c) + 2:]
    return p


def classify_cmd(cmd):
    cats = []
    if TEST_RE.search(cmd):
        cats.append("test")
    if ORCA_RE.search(cmd):
        cats.append("orca")
    if CG_RE.search(cmd):
        cats.append("codegraph")
    if GF_RE.search(cmd):
        cats.append("graphify")
    if READ_CMD.search(cmd):
        cats.append("read")
    elif SEARCH_CMD.search(cmd):
        cats.append("search")
    if BASH_WRITE.search(cmd) and not TEST_RE.search(cmd):
        cats.append("edit")
    if re.search(r"\bgit (commit|add)\b", cmd):
        cats.append("git_write")
    elif re.search(r"\bgit (log|show|diff|status|blame)\b", cmd):
        cats.append("git_read")
    return cats or ["bash_other"]


def files_in_read_cmd(cmd):
    try:
        toks = shlex.split(cmd.split("|")[0].split("&&")[0])
    except ValueError:
        return []
    return [t for t in toks[1:] if not t.startswith("-") and re.search(r"[./]", t) and not re.match(r"^\d+(,\d+)?p$", t)]


def note_tool(rec, cats, t):
    rec["tool_calls"] += 1
    for c in cats:
        rec["cat"][c] += 1
    if "edit" in cats and rec["first_edit"] is None:
        rec["first_edit"] = t
        rec["tool_calls_before_edit"] = rec["tool_calls"] - 1


def parse_claude(path):
    rec = new_rec("claude", path)
    seen_msg = set()
    brief = None
    for line in open(path, errors="replace"):
        try:
            d = json.loads(line)
        except ValueError:
            continue
        t = ts(d.get("timestamp", "")) if d.get("timestamp") else None
        if t:
            rec["start"] = rec["start"] or t
            rec["end"] = t
        if d.get("cwd") and not rec["cwd"]:
            rec["cwd"] = d["cwd"]
        msg = d.get("message") or {}
        if d.get("type") == "user" and brief is None:
            c = msg.get("content")
            text = c if isinstance(c, str) else " ".join(x.get("text", "") for x in c or [] if isinstance(x, dict))
            if MARK in text or AUDIT_MARK in text or REAUDIT_MARK in text:
                brief = text
        if d.get("type") != "assistant":
            continue
        mid = msg.get("id")
        u = msg.get("usage") or {}
        if mid and mid not in seen_msg:
            seen_msg.add(mid)
            rec["turns"] += 1
            rec["model"] = msg.get("model") or rec["model"]
            rec["tok"]["input"] += u.get("input_tokens", 0) or 0
            rec["tok"]["cache_read"] += u.get("cache_read_input_tokens", 0) or 0
            rec["tok"]["cache_write"] += u.get("cache_creation_input_tokens", 0) or 0
            rec["tok"]["output"] += u.get("output_tokens", 0) or 0
            ctx = (u.get("input_tokens", 0) or 0) + (u.get("cache_read_input_tokens", 0) or 0) + (u.get("cache_creation_input_tokens", 0) or 0)
            rec["ctx_max"] = max(rec["ctx_max"], ctx)
        for b in msg.get("content") or []:
            if not isinstance(b, dict) or b.get("type") != "tool_use":
                continue
            name, inp = b.get("name", ""), b.get("input") or {}
            cwd = rec["cwd"]
            if name == "Read":
                f = norm(inp.get("file_path"), cwd)
                rec["files_read"].append(f)
                if META_RE.search(f):
                    rec["meta_reads"] += 1
                note_tool(rec, ["read"], t)
            elif name in ("Grep", "Glob"):
                rec["searches"].append(name + ":" + str(inp.get("pattern")))
                note_tool(rec, ["search"], t)
            elif name in ("Edit", "Write", "MultiEdit", "NotebookEdit"):
                rec["files_edited"].append(norm(inp.get("file_path"), cwd))
                note_tool(rec, ["edit"], t)
            elif name == "Bash":
                cmd = inp.get("command", "")
                cats = classify_cmd(cmd)
                if "read" in cats:
                    for f in files_in_read_cmd(cmd):
                        f = norm(f, cwd)
                        rec["files_read"].append(f)
                        if META_RE.search(f):
                            rec["meta_reads"] += 1
                if "search" in cats:
                    rec["searches"].append("bash:" + cmd[:120])
                rec["codegraph"] += "codegraph" in cats
                rec["graphify"] += "graphify" in cats
                note_tool(rec, cats, t)
            elif name.startswith("mcp__serena"):
                rec["serena"] += 1
                note_tool(rec, ["serena"], t)
            elif "codegraph" in name.lower():
                rec["codegraph"] += 1
                note_tool(rec, ["codegraph"], t)
            elif name in ("Task", "Agent"):
                note_tool(rec, ["subagent"], t)
            else:
                note_tool(rec, ["other:" + name], t)
    if brief is None:
        return None
    rec["brief_chars"] = len(brief)
    rec["kind"], rec["task"] = kind_of(brief), task_of(brief)
    return rec


def parse_codex(path):
    rec = new_rec("codex", path)
    brief = None
    last_total = None
    for line in open(path, errors="replace"):
        try:
            d = json.loads(line)
        except ValueError:
            continue
        t = ts(d.get("timestamp", "")) if d.get("timestamp") else None
        if t:
            rec["start"] = rec["start"] or t
            rec["end"] = t
        p = d.get("payload") or {}
        typ = p.get("type")
        if d.get("type") == "turn_context" and not rec["cwd"]:
            rec["cwd"] = (p.get("cwd") or "").replace("file://", "")
        if typ == "thread_settings_applied":
            rec["model"] = (p.get("thread_settings") or {}).get("model") or rec["model"]
        if d.get("type") == "response_item" and typ == "message" and p.get("role") == "user" and brief is None:
            text = " ".join(x.get("text", "") for x in p.get("content") or [] if isinstance(x, dict))
            if MARK in text or AUDIT_MARK in text or REAUDIT_MARK in text:
                brief = text
        if d.get("type") == "response_item" and typ == "message" and p.get("role") == "assistant":
            rec["turns"] += 1
        if typ == "token_count":
            info = p.get("info") or {}
            tot = info.get("total_token_usage") or {}
            last = info.get("last_token_usage") or {}
            if tot:
                last_total = tot
            if last:
                rec["ctx_max"] = max(rec["ctx_max"], last.get("input_tokens", 0) or 0)
        if typ == "item_completed":
            it = p.get("item") or {}
            k = it.get("type")
            if k == "CommandExecution":
                cmd = (it.get("command") or ["", "", ""])[-1]
                pcs = it.get("parsed_cmd") or []
                cats = set(classify_cmd(cmd))
                for x in pcs:
                    if x.get("type") == "read":
                        f = norm(x.get("path") or x.get("name"), rec["cwd"])
                        rec["files_read"].append(f)
                        if META_RE.search(f):
                            rec["meta_reads"] += 1
                        cats.add("read")
                    elif x.get("type") in ("search", "list_files"):
                        rec["searches"].append("codex:" + (x.get("cmd") or "")[:120])
                        cats.add("search")
                if "read" in cats and not any(x.get("type") == "read" for x in pcs):
                    for f in files_in_read_cmd(cmd):
                        rec["files_read"].append(norm(f, rec["cwd"]))
                if len(cats) > 1:
                    cats.discard("bash_other")
                rec["codegraph"] += "codegraph" in cats
                rec["graphify"] += "graphify" in cats
                note_tool(rec, sorted(cats), t)
            elif k == "FileChange":
                for f in (it.get("changes") or {}):
                    rec["files_edited"].append(norm(f, rec["cwd"]))
                note_tool(rec, ["edit"], t)
            elif k == "McpToolCall":
                note_tool(rec, ["mcp:" + str(it.get("server"))], t)
    if brief is None:
        return None
    if last_total:
        cached = last_total.get("cached_input_tokens", 0) or 0
        rec["tok"]["input"] = (last_total.get("input_tokens", 0) or 0) - cached
        rec["tok"]["cache_read"] = cached
        rec["tok"]["output"] = last_total.get("output_tokens", 0) or 0
    rec["brief_chars"] = len(brief)
    rec["kind"], rec["task"] = kind_of(brief), task_of(brief)
    return rec


def finish(rec):
    rec["repo"], rec["change"] = cwd_parts(rec["cwd"])
    rec["dur_s"] = (rec["end"] - rec["start"]) if rec["start"] and rec["end"] else None
    rec["tfe_s"] = (rec["first_edit"] - rec["start"]) if rec["first_edit"] and rec["start"] else None
    fr = [f for f in rec["files_read"] if f]
    rec["n_reads"] = len(fr)
    rec["n_unique_read"] = len(set(fr))
    rec["n_repeat_reads"] = len(fr) - len(set(fr))
    rec["n_searches"] = len(rec["searches"])
    rec["n_repeat_searches"] = len(rec["searches"]) - len(set(rec["searches"]))
    rec["n_edited"] = len(set(f for f in rec["files_edited"] if f))
    rec["tok_total"] = sum(rec["tok"].values())
    rec["cat"] = dict(rec["cat"])
    return rec


def q(xs, p):
    xs = sorted(x for x in xs if x is not None)
    if not xs:
        return None
    k = (len(xs) - 1) * p
    lo = int(k)
    hi = min(lo + 1, len(xs) - 1)
    return xs[lo] + (xs[hi] - xs[lo]) * (k - lo)


def dist(xs):
    xs = [x for x in xs if x is not None]
    if not xs:
        return {}
    return dict(n=len(xs), p25=q(xs, .25), median=q(xs, .5), p75=q(xs, .75), p90=q(xs, .9), mean=statistics.mean(xs))


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--out", required=True)
    a = ap.parse_args()
    recs = []
    for path in glob.glob(os.path.expanduser("~/.claude/projects/*/*.jsonl")):
        try:
            with open(path, errors="replace") as fh:
                head = fh.read(400000)
        except OSError:
            continue
        if MARK in head or AUDIT_MARK in head or REAUDIT_MARK in head:
            r = parse_claude(path)
            if r:
                recs.append(finish(r))
    for path in glob.glob(os.path.expanduser("~/.codex/sessions/**/*.jsonl"), recursive=True):
        try:
            with open(path, errors="replace") as fh:
                head = fh.read(400000)
        except OSError:
            continue
        if MARK in head or AUDIT_MARK in head or REAUDIT_MARK in head:
            r = parse_codex(path)
            if r:
                recs.append(finish(r))
    recs = [r for r in recs if r["repo"]]
    os.makedirs(a.out, exist_ok=True)
    with open(os.path.join(a.out, "workers.jsonl"), "w") as fh:
        for r in recs:
            r2 = dict(r)
            r2["files_read"] = sorted(set(r["files_read"]))
            r2["searches"] = r["searches"][:200]
            fh.write(json.dumps(r2, default=str) + "\n")

    # cross-worker rediscovery: within (repo, change), files a later worker read that an earlier one read
    groups = collections.defaultdict(list)
    for r in recs:
        groups[(r["repo"], r["change"])].append(r)
    redisc = []
    for key, rs in groups.items():
        rs.sort(key=lambda r: r["start"] or 0)
        seen = set()
        for i, r in enumerate(rs):
            fr = set(f for f in r["files_read"] if f and not META_RE.search(f))
            if i > 0 and fr:
                redisc.append(dict(change=key[1], repo=key[0], idx=i, kind=r["kind"], provider=r["provider"],
                                   unique=len(fr), already=len(fr & seen), frac=len(fr & seen) / len(fr)))
            seen |= fr

    summ = {"n_workers": len(recs),
            "by_provider_kind": collections.Counter("%s/%s" % (r["provider"], r["kind"]) for r in recs),
            "models": collections.Counter("%s:%s" % (r["provider"], r["model"]) for r in recs),
            "changes": len(groups)}
    for prov in ("claude", "codex"):
        for kind in ("task", "retry", "audit_fix", "gate_fix", "audit", "reaudit", "ALL"):
            rs = [r for r in recs if r["provider"] == prov and (kind == "ALL" or r["kind"] == kind)]
            if not rs:
                continue
            impl = [r for r in rs if r["kind"] not in ("audit", "reaudit")]
            summ["%s/%s" % (prov, kind)] = {
                "n": len(rs),
                "dur_min": dist([r["dur_s"] / 60 if r["dur_s"] else None for r in rs]),
                "tfe_min": dist([r["tfe_s"] / 60 if r["tfe_s"] is not None else None for r in rs]),
                "turns": dist([r["turns"] for r in rs]),
                "tool_calls": dist([r["tool_calls"] for r in rs]),
                "tool_calls_before_edit": dist([r["tool_calls_before_edit"] for r in rs if r["first_edit"]]),
                "unique_files_read": dist([r["n_unique_read"] for r in rs]),
                "repeat_reads": dist([r["n_repeat_reads"] for r in rs]),
                "repeat_read_share": dist([r["n_repeat_reads"] / r["n_reads"] if r["n_reads"] else None for r in rs]),
                "searches": dist([r["n_searches"] for r in rs]),
                "meta_reads": dist([r["meta_reads"] for r in rs]),
                "tok_total_M": dist([r["tok_total"] / 1e6 for r in rs]),
                "cache_read_share": dist([r["tok"]["cache_read"] / r["tok_total"] if r["tok_total"] else None for r in rs]),
                "ctx_max_k": dist([r["ctx_max"] / 1e3 for r in rs]),
                "brief_chars": dist([r["brief_chars"] for r in rs]),
                "no_edit_share": (sum(1 for r in impl if not r["first_edit"]) / len(impl)) if impl else None,
                "used_codegraph": sum(1 for r in rs if r["codegraph"]),
                "used_graphify": sum(1 for r in rs if r["graphify"]),
                "used_serena": sum(1 for r in rs if r["serena"]),
                "cat_totals": dict(sum((collections.Counter(r["cat"]) for r in rs), collections.Counter()).most_common(14)),
            }
    summ["rediscovery"] = {
        "pairs": len(redisc),
        "frac": dist([x["frac"] for x in redisc]),
        "already_files": dist([x["already"] for x in redisc]),
        "by_kind": {k: dist([x["frac"] for x in redisc if x["kind"] == k]) for k in set(x["kind"] for x in redisc)},
    }
    with open(os.path.join(a.out, "summary.json"), "w") as fh:
        json.dump(summ, fh, indent=1, default=str)
    print(json.dumps(summ, indent=1, default=str))


if __name__ == "__main__":
    main()
