#!/usr/bin/env bash
# E1 — fixed startup context of a fresh Claude worker under different launch configurations.
# One `claude -p` turn per config ("Reply with exactly: OK"), Haiku, in a throwaway clone of a
# real repo (so its CLAUDE.md/AGENTS.md load as they would for a worker). Records usage only.
# Usage: e1_startup_context.sh <source-repo> <out-dir> [reps]
set -euo pipefail
SRC=$1; OUT=$2; REPS=${3:-1}
mkdir -p "$OUT"
WT="$OUT/clone"
[ -d "$WT/.git" ] || git clone -q --depth 1 "file://$SRC" "$WT"
cd "$WT"
PROMPT='Reply with exactly: OK'
NOPLUG='{"enabledPlugins":{"aws-startup-advisor@claude-plugins-official":false,"superpowers@anthropic-plugin-directory":false,"playwright@anthropic-plugin-directory":false}}'
TOOLS='Bash,Read,Edit,Write,Grep,Glob'
run() {  # name, extra args...
  local name=$1; shift
  for i in $(seq 1 "$REPS"); do
    rm -f .claude/settings.local.json
    if [ "$name" = "C5_project_local" ]; then
      mkdir -p .claude
      printf '%s' '{"enabledPlugins":{"aws-startup-advisor@claude-plugins-official":false,"superpowers@anthropic-plugin-directory":false,"playwright@anthropic-plugin-directory":false},"permissions":{"deny":["Artifact","Workflow","ScheduleWakeup","ReportFindings","AskUserQuestion","ListAgents","SendFeedback"]}}' > .claude/settings.local.json
    fi
    local t0=$(python3 -c 'import time;print(time.time())')
    claude -p "$PROMPT" --model haiku --output-format json --max-turns 1 --no-session-persistence "$@" > "$OUT/$name.$i.json" 2> "$OUT/$name.$i.err" || true
    local t1=$(python3 -c 'import time;print(time.time())')
    python3 - "$OUT/$name.$i.json" "$name" "$i" "$t0" "$t1" >> "$OUT/e1.jsonl" <<'EOF'
import json, sys
p, name, i, t0, t1 = sys.argv[1], sys.argv[2], sys.argv[3], float(sys.argv[4]), float(sys.argv[5])
try:
    d = json.load(open(p))
except Exception as e:
    print(json.dumps(dict(config=name, rep=i, error=str(e)))); sys.exit()
u = d.get("usage") or {}
ctx = (u.get("input_tokens") or 0) + (u.get("cache_creation_input_tokens") or 0) + (u.get("cache_read_input_tokens") or 0)
print(json.dumps(dict(config=name, rep=i, ctx=ctx, input=u.get("input_tokens"), cache_write=u.get("cache_creation_input_tokens"),
                      cache_read=u.get("cache_read_input_tokens"), output=u.get("output_tokens"), result=str(d.get("result"))[:40],
                      wall_s=round(t1 - t0, 2), is_error=d.get("is_error"))))
EOF
  done
}
rm -f "$OUT/e1.jsonl"
run C0_default
run C1_strict_mcp --strict-mcp-config
run C2_no_skills --strict-mcp-config --disable-slash-commands
run C3_core_tools --strict-mcp-config --disable-slash-commands --tools "$TOOLS"
run C4_no_plugins --strict-mcp-config --disable-slash-commands --tools "$TOOLS" --settings "$NOPLUG"
run C5_project_local
rm -f .claude/settings.local.json
cat "$OUT/e1.jsonl"
