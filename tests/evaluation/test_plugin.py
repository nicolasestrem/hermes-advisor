import importlib.util
import json
import re
import subprocess
import sys
import unittest
from contextlib import redirect_stdout
from io import StringIO
from pathlib import Path
from types import SimpleNamespace

sys.path.insert(0, str(Path(__file__).resolve().parent))
import support  # noqa: E402
from advisor_eval import common  # noqa: E402

REPO = support.REPO
PLUGIN = support.RUNNER / "plugin" / "advisor-eval"
RUNNER_PKG = support.RUNNER / "advisor_eval"


class FakeFacade:
    """SYNTHETIC stand-in for ctx.llm. Records every kwarg the product reviewer passes."""

    def __init__(self, response=None, route=None, usage=None):
        self.calls = []
        self.response = response or {"outcome": "no_findings", "findings": []}
        self.route = route or common.FIXED_ROUTE
        self.usage = usage

    async def acomplete_structured(self, **kwargs):
        self.calls.append(kwargs)
        return SimpleNamespace(parsed=self.response, provider=self.route["provider"], model=self.route["model"],
                               usage=self.usage)


class FakeCtx:
    def __init__(self, facade, grants=("llm.provider_override", "llm.model_override")):
        self.llm = facade
        self.grants = set(grants)
        self.commands = {}

    def has_capability(self, name):
        return name in self.grants

    def register_cli_command(self, name, help, setup_fn, handler_fn=None, description=""):
        self.commands[name] = (setup_fn, handler_fn)


def load_plugin():
    spec = importlib.util.spec_from_file_location("advisor_eval_plugin_under_test", PLUGIN / "__init__.py")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def invoke(ctx, argv):
    import argparse
    parser = argparse.ArgumentParser()
    setup, handler = ctx.commands["advisor-eval"]
    setup(parser)
    out = StringIO()
    code = 0
    with redirect_stdout(out):
        try:
            handler(parser.parse_args(argv))
        except SystemExit as exc:
            code = exc.code or 0
    return code, out.getvalue()


class PluginTests(unittest.TestCase):
    def setUp(self):
        self._tmp = support.tmpdir()
        self.tmp = Path(self._tmp.name)
        self.prepared, self.sha = support.prepared_in(self.tmp)
        self.base = ["--repo", str(REPO), "--prepared", str(self.prepared), "--runs-root", str(self.tmp / "runs")]

    def tearDown(self):
        self._tmp.cleanup()

    def ctx(self, **kw):
        facade = FakeFacade(**kw)
        ctx = FakeCtx(facade)
        load_plugin().register(ctx)
        return ctx, facade

    def test_registers_one_public_cli_command_and_manifest_declares_grants(self):
        ctx, _ = self.ctx()
        self.assertEqual(list(ctx.commands), ["advisor-eval"])
        manifest = (PLUGIN / "plugin.yaml").read_text()
        self.assertIn("llm.provider_override", manifest)
        self.assertIn("llm.model_override", manifest)

    def test_preflight_makes_no_call(self):
        ctx, facade = self.ctx()
        code, out = invoke(ctx, ["preflight", *self.base, "--expect-manifest-sha256", self.sha])
        self.assertEqual(code, 0, out)
        report = json.loads(out)
        self.assertEqual(report["inference"], "NOT RUN")
        self.assertTrue(report["freeze_ok"])
        self.assertEqual(report["manifest_sha256"], self.sha)
        self.assertEqual(facade.calls, [])

    def test_missing_grants_refuse_before_any_call(self):
        facade = FakeFacade()
        ctx = FakeCtx(facade, grants=("llm.provider_override",))
        load_plugin().register(ctx)
        code, out = invoke(ctx, ["run", *self.base, "--expect-manifest-sha256", self.sha,
                                 "--authorize-live", common.AUTHORIZE_TOKEN, "--run-id", "g4"])
        self.assertEqual(code, 2)
        self.assertEqual(facade.calls, [])

    def test_run_refused_without_authorization_or_with_stale_hash(self):
        ctx, facade = self.ctx()
        for extra in (["--expect-manifest-sha256", self.sha, "--run-id", "g4"],
                      ["--expect-manifest-sha256", "0" * 64, "--authorize-live", common.AUTHORIZE_TOKEN, "--run-id", "g4"],
                      ["--authorize-live", common.AUTHORIZE_TOKEN, "--run-id", "g4"]):
            code, out = invoke(ctx, ["run", *self.base, *extra])
            self.assertEqual(code, 1, out)
        self.assertEqual(facade.calls, [])

    def test_full_run_uses_product_review_facade_path_with_exact_bounds(self):
        usage = SimpleNamespace(input_tokens=700, output_tokens=120, total_tokens=820, cache_read_tokens=0,
                                cache_write_tokens=0, cost_usd=None)
        ctx, facade = self.ctx(usage=usage)
        code, out = invoke(ctx, ["run", *self.base, "--expect-manifest-sha256", self.sha,
                                 "--authorize-live", common.AUTHORIZE_TOKEN, "--run-id", "g4-test"])
        self.assertEqual(code, 0, out)
        self.assertEqual(len(facade.calls), 10)
        core, host = common.load_product(REPO)
        for call in facade.calls:
            self.assertEqual((call["provider"], call["model"]), ("nous", "z-ai/glm-5.3-flash"))
            self.assertEqual(call["max_tokens"], 2048)
            self.assertEqual(call["timeout"], 90)
            self.assertEqual(call["temperature"], 0.0)
            self.assertEqual(call["instructions"], host.INSTRUCTIONS)
            self.assertEqual(call["json_schema"]["additionalProperties"], False)
            text = call["input"][0]["text"]
            for token in ("expected_observations", "supported_defect", "correct_control", "independent_defect"):
                self.assertNotIn(token, text)
        results = json.loads((self.tmp / "runs" / "g4-test" / "results.json").read_text())
        self.assertEqual(results["mode"], "live")
        self.assertEqual(results["header"]["facade_class"], "test_plugin.FakeFacade")
        self.assertTrue(results["cases"]["D01"]["usage"]["known"])
        self.assertEqual(results["reserved_calls"], 10)
        # a fake facade can never masquerade as the host facade in the report
        from advisor_eval import score
        gold = json.loads((self.prepared / "gold.json").read_text())
        text = score.render_report(results, score.score_run(results, gold), gold)
        self.assertIn("not the host plugin facade", text)
        # one live run only
        code, _ = invoke(ctx, ["run", *self.base, "--expect-manifest-sha256", self.sha,
                               "--authorize-live", common.AUTHORIZE_TOKEN, "--run-id", "g4-again"])
        self.assertEqual(code, 1)
        self.assertEqual(len(facade.calls), 10)

    def test_zero_filled_usage_stays_unknown_through_product_path(self):
        usage = SimpleNamespace(input_tokens=0, output_tokens=0, total_tokens=0, cache_read_tokens=0,
                                cache_write_tokens=0, cost_usd=None)
        ctx, facade = self.ctx(usage=usage)
        invoke(ctx, ["run", *self.base, "--expect-manifest-sha256", self.sha,
                     "--authorize-live", common.AUTHORIZE_TOKEN, "--run-id", "z"])
        results = json.loads((self.tmp / "runs" / "z" / "results.json").read_text())
        self.assertFalse(results["cases"]["I02"]["usage"]["known"])


class StaticSafetyTests(unittest.TestCase):
    BAD_MODULES = {"requests", "urllib", "httpx", "socket", "openai", "anthropic", "dotenv", "getpass", "http"}
    BAD_NAMES = {"environ", "getenv", "_manager", "_policy_loader", "api_key", "apikey", "plugin_capability_granted"}
    BAD_STRINGS = ("auth.json", "credentials.json", ".env")

    def test_no_credential_network_or_private_api_use(self):
        import ast
        files = [*RUNNER_PKG.glob("*.py"), PLUGIN / "__init__.py"]
        self.assertGreaterEqual(len(files), 9)
        for path in files:
            tree = ast.parse(path.read_text())
            docstrings = {id(n.body[0].value) for n in ast.walk(tree)
                          if isinstance(n, (ast.Module, ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef))
                          and n.body and isinstance(n.body[0], ast.Expr) and isinstance(n.body[0].value, ast.Constant)}
            for node in ast.walk(tree):
                if isinstance(node, ast.Import):
                    for alias in node.names:
                        self.assertNotIn(alias.name.split(".")[0], self.BAD_MODULES, path.name)
                elif isinstance(node, ast.ImportFrom):
                    self.assertNotIn((node.module or "").split(".")[0], self.BAD_MODULES, path.name)
                elif isinstance(node, ast.Attribute):
                    self.assertNotIn(node.attr, self.BAD_NAMES, path.name)
                elif isinstance(node, ast.Name):
                    self.assertNotIn(node.id, self.BAD_NAMES, path.name)
                elif isinstance(node, ast.Constant) and isinstance(node.value, str) and id(node) not in docstrings:
                    for bad in self.BAD_STRINGS:
                        self.assertNotIn(bad, node.value, path.name)

    def test_only_owned_paths_changed(self):
        head = subprocess.run(["git", "rev-parse", "--verify", "HEAD"], cwd=REPO, capture_output=True, text=True)
        if head.returncode == 0:
            out = subprocess.run(["git", "status", "--porcelain", "--", "evaluation", "evidence/g4-preparation"],
                                 cwd=REPO, capture_output=True, text=True)
            self.assertEqual(out.stdout.strip(), "")
        # An unborn staging tree has no tracked baseline; the freeze manifest is
        # the authoritative byte check for the one owned directory that matters.
        check = subprocess.run(["sha256sum", "-c", common.FREEZE_FILE], cwd=REPO, capture_output=True, text=True)
        self.assertEqual(check.returncode, 0, check.stdout)

    def test_plugin_never_reads_gold(self):
        text = (PLUGIN / "__init__.py").read_text() + (RUNNER_PKG / "runner.py").read_text()
        self.assertNotIn("gold.json", text)
        self.assertNotIn("build_gold", text)


if __name__ == "__main__":
    unittest.main()
