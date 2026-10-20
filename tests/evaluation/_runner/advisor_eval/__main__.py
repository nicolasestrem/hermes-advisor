"""Stand-alone, inference-free harness: python3 -m advisor_eval {freeze-check,dry-run,score}.

Live inference is NOT available here; it exists only in the parent-run plugin command.
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

from .common import REPO, RUNNER_DIR
from .freeze import verify_freeze
from .prepare import PreparationError, prepare
from .score import score_files


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(prog="python3 -m advisor_eval", description=__doc__)
    sub = parser.add_subparsers(dest="cmd", required=True)
    for name in ("freeze-check", "dry-run", "score"):
        p = sub.add_parser(name)
        p.add_argument("--repo", default=str(REPO))
    sub.choices["dry-run"].add_argument("--out", default=str(RUNNER_DIR / "prepared"))
    sc = sub.choices["score"]
    sc.add_argument("--run", required=True, help="run directory containing results.json")
    sc.add_argument("--gold", default=str(RUNNER_DIR / "prepared" / "gold.json"))
    sc.add_argument("--adjudication", default=None)
    args = parser.parse_args(argv)
    if args.cmd == "freeze-check":
        result = verify_freeze(Path(args.repo))
        print(json.dumps(result, indent=2))
        return 0 if result["ok"] else 1
    if args.cmd == "dry-run":
        try:
            summary = prepare(Path(args.repo), Path(args.out))
        except Exception as exc:  # fail closed, never partial
            print(f"DRY-RUN FAILED: {type(exc).__name__}: {exc}", file=sys.stderr)
            return 1
        print(json.dumps(summary, indent=2))
        print("NOT RUN: no model/reviewer call was made.")
        return 0
    scores = score_files(Path(args.run), Path(args.gold), Path(args.adjudication) if args.adjudication else None)
    print(json.dumps({"success_total": scores["success_total"], "pending": scores["pending_adjudication"],
                      "failed_retained": scores["failed_retained"]}, indent=2))
    return 0


if __name__ == "__main__":
    sys.exit(main())
