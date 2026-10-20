"""Portable-preview regressions for guarded POSIX ownership locks.

The store must import on platforms without ``fcntl`` so the plugin and core can
load, while every actual attempt to use an ownership lock must fail closed with
an explicit ``StoreError``. No emulated lock and no silent "busy" result.
"""

from __future__ import annotations

import os
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest

from advisor_core import store as store_module
from advisor_core.store import SQLiteStore, StoreError
from tests._scratch_root import SCRATCH

ROOT = Path(__file__).resolve().parents[2]
SCRATCH.mkdir(parents=True, exist_ok=True)

_BLOCK_FCNTL = """
import sys

class _BlockFcntl:
    def find_spec(self, fullname, path=None, target=None):
        if fullname == 'fcntl':
            raise ImportError('fcntl is unavailable on this simulated platform')

sys.meta_path.insert(0, _BlockFcntl())
sys.path.insert(0, sys.path[0] or '.')
"""


def _run_without_fcntl(script: str) -> subprocess.CompletedProcess[str]:
    env = os.environ.copy()
    env.pop("PYTHONPATH", None)
    env.pop("PYTHONHOME", None)
    return subprocess.run(
        [sys.executable, "-c", _BLOCK_FCNTL + script],
        cwd=str(ROOT),
        env=env,
        capture_output=True,
        text=True,
        timeout=60,
    )


class PreviewStorePlatformTests(unittest.TestCase):
    def test_core_and_plugin_import_without_fcntl(self):
        result = _run_without_fcntl(
            """
import advisor_core
import advisor_core.store as store
import advisor_core.service
import advisor_host
import dashboard.plugin_api
assert store.fcntl is None, store.fcntl
print('IMPORT_OK')
"""
        )
        self.assertEqual(result.returncode, 0,
                         f"import failed without fcntl\nstdout={result.stdout}\nstderr={result.stderr}")
        self.assertIn("IMPORT_OK", result.stdout)

    def test_lock_use_without_fcntl_fails_closed_with_store_error(self):
        result = _run_without_fcntl(
            """
import pathlib
import tempfile
from advisor_core.store import SQLiteStore, StoreError

with tempfile.TemporaryDirectory() as data_dir:
    store = SQLiteStore(data_dir)
    try:
        value = store.acquire('preview-session')
    except StoreError as exc:
        message = str(exc)
        assert 'POSIX' in message or 'platform' in message.lower(), message
        print('STORE_ERROR=' + message)
    else:
        print('UNEXPECTED=' + type(value).__name__)
        raise SystemExit(3)
    lock_files = list((pathlib.Path(data_dir) / 'locks').iterdir())
    print('LOCK_FILES=' + str(len(lock_files)))
    assert not lock_files, lock_files
print('LOCK_FAIL_CLOSED_OK')
"""
        )
        self.assertEqual(result.returncode, 0,
                         f"non-POSIX lock use did not fail closed\nstdout={result.stdout}\nstderr={result.stderr}")
        self.assertIn("STORE_ERROR=", result.stdout)
        self.assertIn("LOCK_FILES=0", result.stdout)
        self.assertIn("LOCK_FAIL_CLOSED_OK", result.stdout)

    @unittest.skipIf(store_module.fcntl is None, "POSIX flock is unavailable here")
    def test_posix_locking_still_excludes_a_second_owner(self):
        with tempfile.TemporaryDirectory(dir=SCRATCH) as data_dir:
            first = SQLiteStore(data_dir)
            second = SQLiteStore(data_dir)
            lock = first.acquire("preview-session")
            self.assertIsNotNone(lock)
            assert lock is not None
            try:
                self.assertIsNone(second.acquire("preview-session"),
                                  "a second owner acquired an already-held session lock")
            finally:
                lock.release()
            replacement = second.acquire("preview-session")
            self.assertIsNotNone(replacement)
            assert replacement is not None
            replacement.release()

    def test_store_error_public_interface_is_unchanged(self):
        self.assertEqual(StoreError.__name__, "StoreError")
        self.assertTrue(issubclass(StoreError, RuntimeError))
        self.assertEqual(StoreError.__module__, "advisor_core.store")


if __name__ == "__main__":
    unittest.main(verbosity=2)
