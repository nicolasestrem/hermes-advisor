"""Public test plugin: ``hermes -p <lab-profile> advisor-eval {preflight,run}``.

Uses only public plugin surfaces: ``ctx.register_cli_command``, ``ctx.has_capability`` and the
host-owned ``ctx.llm`` facade (via the product's own ``advisor_host.Runtime.review``, called with a
ctx-only shim so no Service/SQLite/worker is created). No credentials, auth files, host managers
or private policy methods are touched. It makes no call unless every gate in ``run`` passes.
"""
from __future__ import annotations

import argparse
import json
import sys
import types
from pathlib import Path

DEFAULT_REPO = str(Path(__file__).resolve().parents[5])


def _import_runner(repo: Path):
    root = str(Path(repo).resolve() / "tests" / "evaluation" / "_runner")
    if root not in sys.path:
        sys.path.insert(0, root)
    import advisor_eval  # noqa: WPS433
    return advisor_eval


def register(ctx):
    def setup(parser: argparse.ArgumentParser):
        parser.add_argument("action", choices=("preflight", "run"))
        parser.add_argument("--repo", default=DEFAULT_REPO)
        parser.add_argument("--prepared", default=None, help="dry-run output dir (default <repo>/tests/evaluation/_runner/prepared)")
        parser.add_argument("--runs-root", default=None, help="default <repo>/tests/evaluation/_runner/runs")
        parser.add_argument("--run-id", default=None)
        parser.add_argument("--expect-manifest-sha256", default=None)
        parser.add_argument("--authorize-live", default=None)

    def handler(args):
        repo = Path(args.repo).resolve()
        _import_runner(repo)
        from advisor_eval import common, freeze, prepare, runner  # noqa: WPS433
        report = {"inference": "NOT RUN", "action": args.action}
        facade = ctx.llm
        facade_class = f"{type(facade).__module__}.{type(facade).__qualname__}"
        granted = {name: bool(ctx.has_capability(name)) for name in ("llm.provider_override", "llm.model_override")}
        report.update(facade_class=facade_class, grants=granted,
                      has_acomplete_structured=callable(getattr(facade, "acomplete_structured", None)))
        if not all(granted.values()):
            print(json.dumps({**report, "refused": "required capability grants are missing"}, indent=2))
            raise SystemExit(2)
        core, host = common.load_product(repo)
        import inspect
        if not inspect.iscoroutinefunction(host.Runtime.review):
            print(json.dumps({**report, "refused": "advisor_host.Runtime.review is not async"}, indent=2))
            raise SystemExit(2)
        prepared = Path(args.prepared) if args.prepared else repo / "tests" / "evaluation" / "_runner" / "prepared"
        if args.action == "preflight":
            fr = freeze.verify_freeze(repo)
            report.update(freeze_ok=fr["ok"], freeze_tool_exit=fr["tool_exit"], freeze_problems=fr["problems"])
            try:
                manifest, _p, sha = prepare.load_prepared(repo, prepared, args.expect_manifest_sha256 or "")
                report["manifest_sha256"] = sha
            except Exception as exc:  # expected when no hash was pinned yet; reported, never hidden
                report["manifest_check"] = f"{type(exc).__name__}: {exc}"
            print(json.dumps(report, indent=2))
            raise SystemExit(0 if fr["ok"] else 1)

        shim = types.SimpleNamespace(ctx=ctx)

        async def review(packet, settings):
            # The product's real review facade path; usage/route attribution stay as the host reports them.
            return await host.Runtime.review(shim, packet, settings)

        runs_root = Path(args.runs_root) if args.runs_root else repo / "tests" / "evaluation" / "_runner" / "runs"
        try:
            final = runner.run_live(repo=repo, prepared=prepared, expect_manifest_sha256=args.expect_manifest_sha256 or "",
                                    authorize=args.authorize_live or "", run_id=args.run_id or "", review=review,
                                    facade_class=facade_class, runs_root=runs_root)
        except Exception as exc:
            print(json.dumps({**report, "refused_or_failed": f"{type(exc).__name__}: {exc}"}, indent=2))
            raise SystemExit(1)
        summary = {"run_id": final["run_id"], "reserved_calls": final["reserved_calls"], "halted": final["halted"],
                   "statuses": {cid: rec["status"] for cid, rec in final["cases"].items()},
                   "results": str(runs_root / final["run_id"] / "results.json")}
        print(json.dumps({**report, "inference": "LIVE", **summary}, indent=2))

    ctx.register_cli_command(
        name="advisor-eval",
        help="Parent-run frozen G4 evaluation harness (preflight / bounded run).",
        description="Bounded ten-case Hermes Advisor usefulness evaluation through the host-owned LLM facade.",
        setup_fn=setup, handler_fn=handler)
