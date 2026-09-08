#!/bin/sh
# A/B harness: does retrieval beat plain search for "what governs this file?"
#   usage: sh sweep.sh <retrieval|control> <rep>
# Reads tasks.tsv: tag<TAB>slice<TAB>path<TAB>expected-spec-id
# Writes one <tag>.<arm>.r<rep>.json per run into $OUT (default ./runs).
PATH=/opt/homebrew/bin:/usr/local/bin:/usr/bin:/bin
export PATH
arm="$1"; rep="$2"
REPO="${REPO:-$(pwd)}"                 # repo under test
OUT="${OUT:-./runs}"
SKILL="${SKILL:-$HOME/.claude/skills/memo-bank-query}"
STASH="${STASH:-/tmp/mb-skill-stash}"
[ -z "$arm" ] && { echo "usage: sh sweep.sh <retrieval|control> <rep>" >&2; exit 2; }
mkdir -p "$OUT"

# The control must see neither the MCP tools nor the skill's description, or it is
# being coached about where to look — which silently narrows the gap being measured.
restore() { [ -d "$STASH/memo-bank-query" ] && mv "$STASH/memo-bank-query" "$SKILL"; return 0; }
trap restore EXIT INT TERM
if [ "$arm" = "control" ] && [ -d "$SKILL" ]; then mkdir -p "$STASH"; mv "$SKILL" "$STASH/"; fi

while IFS='	' read -r tag slice path expect; do
  [ -z "$tag" ] && continue
  P="In the ${slice} subproject of this repo, what rules govern the file \`${path}\`? Name the governing spec document and state its key restrictions. Be concise."
  if [ "$arm" = "control" ]; then
    (cd "$REPO" && claude -p "$P" --output-format json --max-turns 20 \
        --strict-mcp-config --mcp-config '{"mcpServers":{}}') > "$OUT/${tag}.${arm}.r${rep}.json" 2>&1
  else
    (cd "$REPO" && claude -p "$P" --output-format json --max-turns 20) \
        > "$OUT/${tag}.${arm}.r${rep}.json" 2>&1
  fi
  printf "  %s %s r%s\n" "$tag" "$arm" "$rep"
done < "${TASKS:-./tasks.tsv}"
restore
exit 0
