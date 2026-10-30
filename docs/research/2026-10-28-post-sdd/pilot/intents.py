#!/usr/bin/env python3
"""Write fixtures/<task>/intent.md from the frozen plan: Why, Scope and every acceptance
criterion, plus the decisions the criteria cite (the hidden tests check them). No task split,
no Verification, no Repo, no uncited decisions: that is what the planning arms add."""
import json, os, re, sys

CFG = json.load(open(os.path.join(os.path.dirname(__file__), "tasks.json")))


def intent(plan: str) -> str:
    why = re.search(r"## Why\n(.*?)\n## ", plan, re.S).group(1).strip()
    scope = re.search(r"## Scope\n(.*?)\n## ", plan, re.S).group(1).strip()
    decisions = dict(re.findall(r"^- (D\d+) (.+)$", plan, re.M))
    crits = []
    for block in re.findall(r"  - Acceptance:\n((?:    - .+\n?)+)", plan):
        crits += [c.strip()[2:] for c in block.splitlines() if c.strip().startswith("- ")]
    cited = sorted({d for c in crits for d in re.findall(r"\bD\d+\b", c)})
    out = ["# What to build", "", why, "", "## Scope", scope, "", "## Done when"]
    out += ["- %s" % c for c in crits]
    if cited:
        out += ["", "## Constraints the criteria refer to"] + ["- %s %s" % (d, decisions[d]) for d in cited]
    return "\n".join(out) + "\n"


if __name__ == "__main__":
    for name in CFG["tasks"]:
        d = os.path.join(CFG["root"], "fixtures", name)
        text = intent(open(os.path.join(d, "plan", "tasks.md")).read())
        open(os.path.join(d, "intent.md"), "w").write(text)
        print("== %s\n%s" % (name, text))
