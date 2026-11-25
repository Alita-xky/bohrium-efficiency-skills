#!/bin/bash
# One agent run on one case, with or without a Skill, under the platform's budget wording.
#   run_side_by_side.sh CASE_DIR SKILL_DIR|none RUN_DIR [MODEL] [SECONDS]
# CASE_DIR holds task.md + case.json (+ inputs); put the paper PDF named in task.md next to them first
# (see README.md for the arXiv links). Needs the Claude Code CLI as `claude` (or $CLAUDE) and Python with
# matplotlib, python-pptx and PyMuPDF; LibreOffice for slide renders.
CASE=$1; SKILL=$2; R=$3; MODEL=${4:-haiku}; SECS=${5:-900}; CL=${CLAUDE:-claude}
rm -rf "$R"; mkdir -p "$R/input" "$R/work" "$R/output"; cp -R "$CASE/." "$R/input/"
SK=""
if [ "$SKILL" != "none" ]; then
  mkdir -p "$R/skills"; cp -R "$SKILL" "$R/skills/weekly-skill"
  SK="A Skill is installed for you at $R/skills/weekly-skill (this is /skills/weekly-skill). Read its SKILL.md first and follow it."
fi
cat > "$R/prompt.txt" <<P
You are working on one fixed evaluation case inside a sandbox.
Path mapping for this sandbox: /input is $R/input ; /app/work is $R/work ; /app/output is $R/output .
Wherever any instruction or file mentions /input, /app/work or /app/output, use these real paths instead.
$SK
Task: read $R/input/task.md and $R/input/case.json and complete the task they describe.
Work under $R/work. Final actual results and editable artifacts belong under $R/output, including result.json
with all original output_contract fields and case_id. Include honest status, sources, uncertainty and change
records. Editable Office/slide/diagram outputs should also have real rendered PDF/PNG previews for inspection.
A partial result must identify its limits. Do not fabricate missing software state or scientific data.
Read actual files instead of assuming source content from labels.
Execution budget: at most 64 model requests and 900 seconds. Save honest partial deliverables early and improve
incrementally. Python 3 with matplotlib, python-pptx, PyMuPDF and LibreOffice (soffice) are available.
P
cd "$R"; t0=$(date +%s)
perl -e 'alarm shift; exec @ARGV' "$SECS" "$CL" -p "$(cat "$R/prompt.txt")" --model "$MODEL" --max-turns 64 \
  --permission-mode bypassPermissions --output-format stream-json --verbose > "$R/trace.jsonl" 2> "$R/stderr.txt"
echo "rc=$? seconds=$(( $(date +%s) - t0 ))" > "$R/run_status.txt"
