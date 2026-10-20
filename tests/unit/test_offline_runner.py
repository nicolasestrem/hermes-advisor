"""The offline runner must report each suite's real exit status.

``scripts/run-offline-tests.sh`` logs pytest output through ``tee`` and reads the
test command's status from ``PIPESTATUS``. Any command between the pipeline and
that read resets ``PIPESTATUS``, so a failing suite would be reported as passing.
These tests run the runner's own ``capture`` function under the runner's shell
options and check the status it returns.
"""

from __future__ import annotations

import subprocess
import tempfile
import unittest
from pathlib import Path

RUNNER = Path(__file__).resolve().parents[2] / "scripts" / "run-offline-tests.sh"


def capture_function(source: str) -> str:
    start = source.index("capture() {")
    end = source.index("\n}\n", start) + len("\n}\n")
    return source[start:end]


def run_capture(function: str, log: str, status: int) -> tuple[int, str]:
    script = (
        "set -euo pipefail\n"
        + function
        + f'if capture "$1" bash -c "echo suite-output; exit {status}"; then rc=0; else rc=$?; fi\n'
        + 'echo "RC=$rc"\n'
    )
    result = subprocess.run(
        ["bash", "-c", script, "runner-capture", log],
        capture_output=True,
        text=True,
        check=True,
        timeout=30,
    )
    return int(result.stdout.strip().splitlines()[-1].removeprefix("RC=")), result.stdout


class OfflineRunnerExitStatusTests(unittest.TestCase):
    def setUp(self) -> None:
        self.function = capture_function(RUNNER.read_text(encoding="utf-8"))

    def test_logged_capture_returns_the_wrapped_command_status(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            log = Path(directory) / "suite.log"
            for status in (0, 1, 2, 7):
                with self.subTest(status=status):
                    returned, stdout = run_capture(self.function, str(log), status)
                    self.assertEqual(status, returned)
                    self.assertIn("suite-output", stdout)
                    self.assertEqual("suite-output\n", log.read_text(encoding="utf-8"))

    def test_unlogged_capture_returns_the_wrapped_command_status(self) -> None:
        for status in (0, 1, 7):
            with self.subTest(status=status):
                returned, _ = run_capture(self.function, "", status)
                self.assertEqual(status, returned)


if __name__ == "__main__":
    unittest.main()
