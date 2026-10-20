#!/usr/bin/env bash
# Run every exported offline Python suite serially.
#
# Requirements: CPython 3.14.x (verified on 3.14.7), dependencies from
# requirements-test.txt, and coreutils' sha256sum for tests/evaluation.
#
# Environment:
#   PYTHON              interpreter to use (default: ./.venv/bin/python or python3.14)
#   ADVISOR_SCRATCH     scratch directory (default: a fresh per-run temporary directory)
#   ADVISOR_RECEIPTS    optional directory for per-suite stdout receipts
#   ADVISOR_KEEP_SCRATCH=1 keep the temporary scratch directory after the run
#
# The script deliberately clears PYTHONPATH: exported tests must import the
# repository's modules, never a private checkout or host installation.
set -euo pipefail

ROOT=$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")/.." && pwd)
cd "$ROOT"

unset PYTHONPATH

if [[ -n "${PYTHON:-}" ]]; then
    INTERPRETER=$PYTHON
elif [[ -x "$ROOT/.venv/bin/python" ]]; then
    INTERPRETER=$ROOT/.venv/bin/python
elif command -v python3.14 >/dev/null 2>&1; then
    INTERPRETER=$(command -v python3.14)
elif command -v python3 >/dev/null 2>&1; then
    INTERPRETER=$(command -v python3)
else
    echo "error: no Python interpreter found; CPython 3.14.7 is required" >&2
    exit 2
fi

if ! "$INTERPRETER" -c 'import sys; raise SystemExit(0 if sys.version_info[:2] >= (3, 14) else 1)'; then
    echo "error: CPython 3.14.7 is required; $INTERPRETER reports $("$INTERPRETER" -V 2>&1)" >&2
    exit 2
fi

OWN_SCRATCH=0
if [[ -n "${ADVISOR_SCRATCH:-}" ]]; then
    SCRATCH=$ADVISOR_SCRATCH
else
    SCRATCH=$(mktemp -d "${TMPDIR:-/tmp}/advisor-offline-tests.XXXXXX")
    OWN_SCRATCH=1
fi
mkdir -p "$SCRATCH"
export ADVISOR_SCRATCH=$SCRATCH

RECEIPTS=${ADVISOR_RECEIPTS:-}
if [[ -n "$RECEIPTS" ]]; then
    mkdir -p "$RECEIPTS"
    RECEIPTS=$(cd -- "$RECEIPTS" && pwd)
fi

cleanup() {
    if [[ $OWN_SCRATCH -eq 1 && "${ADVISOR_KEEP_SCRATCH:-0}" != "1" ]]; then
        rm -rf -- "$SCRATCH"
    fi
}
trap cleanup EXIT

echo "repository:   $ROOT"
echo "interpreter:  $("$INTERPRETER" -V 2>&1) ($INTERPRETER)"
echo "scratch:      $SCRATCH"
echo "PYTHONPATH:   <cleared>"
if [[ -n "$RECEIPTS" ]]; then
    echo "receipts:     $RECEIPTS"
fi

# Import provenance check: every product module must resolve inside this checkout.
if ! ROOT="$ROOT" "$INTERPRETER" - <<'PY'
import os
import pathlib
import sys

root = pathlib.Path(os.environ["ROOT"]).resolve()
import advisor_core
import advisor_host
import host_adapter
import tests._scratch_root

for module in (advisor_core, advisor_host, host_adapter, tests._scratch_root):
    path = pathlib.Path(module.__file__).resolve()
    if root not in path.parents:
        raise SystemExit(f"{module.__name__} resolved outside the checkout: {path}")
    print(f"import {module.__name__}: {path}")

print(f"sys.path[0]: {sys.path[0]!r}")
PY
then
    echo "error: import provenance check failed" >&2
    exit 2
fi

capture() {
    local log=$1
    shift
    if [[ -n "$log" ]]; then
        local rc
        set +e
        "$@" 2>&1 | tee "$log"
        rc=${PIPESTATUS[0]}
        set -e
        return "$rc"
    fi
    "$@"
}

COLLECT_LOG=""
if [[ -n "$RECEIPTS" ]]; then
    COLLECT_LOG=$RECEIPTS/collection.log
fi

echo
echo "=== collection ==="
if ! capture "$COLLECT_LOG" "$INTERPRETER" -m pytest --collect-only -q; then
    echo "collection failed (exit 2 means import/collection errors)" >&2
    exit 2
fi

SUITES=("$@")
if [[ ${#SUITES[@]} -eq 0 ]]; then
    SUITES=(unit privacy evaluation cli_feedback)
fi

FAILED=()
for suite in "${SUITES[@]}"; do
    path="tests/$suite"
    if [[ ! -d "$path" ]]; then
        echo "error: no such suite directory: $path" >&2
        exit 2
    fi
    log=""
    if [[ -n "$RECEIPTS" ]]; then
        log=$RECEIPTS/$suite.log
    fi
    echo
    echo "=== $suite ==="
    if capture "$log" "$INTERPRETER" -m pytest "$path" -q; then
        echo "suite $suite: PASS"
    else
        echo "suite $suite: FAIL" >&2
        FAILED+=("$suite")
    fi
done

echo
echo "=== summary ==="
if [[ ${#FAILED[@]} -eq 0 ]]; then
    echo "all suites passed: ${SUITES[*]}"
    exit 0
fi
echo "failed suites: ${FAILED[*]}" >&2
exit 1
