#!/bin/sh
# kayaclaw effective-LOC counter and budget enforcer.
# Effective LOC = total lines minus blank lines minus comment-only lines.
# Exits 0 if all modules within hard cap AND total <= 800; non-zero otherwise.
set -eu

# module:hard_cap pairs (CONTRACT-0.1.md §Size Discipline).
MODULES="agent/connectors/telegram.py:250 agent/registry.py:150 agent/runtime.py:100 agent/memory.py:150 agent/__main__.py:50 agent/config.py:100"
TOTAL_CAP=800
total=0; fail=0

printf '%-32s %s\n%-32s %s\n' "MODULE" "LOC/CAP" "------" "-------"
for entry in $MODULES; do
    file=${entry%:*}; cap=${entry#*:}
    if [ ! -f "$file" ]; then printf '%-32s %s\n' "$file" "MISSING"; fail=1; continue; fi
    loc=$(awk '!/^[[:space:]]*$/ && !/^[[:space:]]*#/ {n++} END {print n+0}' "$file")
    total=$((total + loc))
    over=""; [ "$loc" -gt "$cap" ] && { over=" [OVER CAP]"; fail=1; }
    printf '%-32s %s/%s%s\n' "$file" "$loc" "$cap" "$over"
done

[ "$total" -gt "$TOTAL_CAP" ] && fail=1
[ "$fail" -eq 0 ] && verdict="PASS" || verdict="FAIL"
printf '%-32s %s/%s  [%s]\n' "TOTAL" "$total" "$TOTAL_CAP" "$verdict"
exit $fail
