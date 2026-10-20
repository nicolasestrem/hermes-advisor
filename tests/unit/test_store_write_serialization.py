"""Real-SQLite tests for bounded per-store write serialization."""

from __future__ import annotations

import concurrent.futures
import sqlite3
import tempfile
import threading
import time
import unittest
from pathlib import Path

from advisor_core.store import SQLiteStore, StoreError


class StoreWriteSerializationTests(unittest.TestCase):
    def test_same_store_writes_do_not_open_competing_connections(self):
        first_in_connect = threading.Event()
        allow_first_connect = threading.Event()
        second_in_connect = threading.Event()
        second_started = threading.Event()
        connection_count = 0
        count_lock = threading.Lock()
        errors: list[BaseException] = []

        class ControlledConnectStore(SQLiteStore):
            def __init__(self, data_dir):
                self.observe_connects = False
                super().__init__(data_dir)
                self.observe_connects = True

            def _connect(self, *args, **kwargs):
                nonlocal connection_count
                if self.observe_connects:
                    with count_lock:
                        connection_count += 1
                        current = connection_count
                    if current == 1:
                        first_in_connect.set()
                        if not allow_first_connect.wait(3):
                            raise AssertionError("test did not release first connection")
                    else:
                        second_in_connect.set()
                return super()._connect(*args, **kwargs)

        with tempfile.TemporaryDirectory() as data_dir:
            store = ControlledConnectStore(data_dir)

            def write():
                try:
                    store.transaction(lambda connection: connection.execute("SELECT 1").fetchone())
                except BaseException as exc:
                    errors.append(exc)

            first = threading.Thread(target=write, name="first-store-writer")
            second = threading.Thread(
                target=lambda: (second_started.set(), write()), name="second-store-writer"
            )
            first.start()
            self.assertTrue(first_in_connect.wait(2), "first writer did not reach connection setup")
            second.start()
            try:
                self.assertTrue(second_started.wait(2), "second writer did not start")
                self.assertFalse(
                    second_in_connect.wait(0.2),
                    "a second same-store writer opened a competing connection before the first finished",
                )
            finally:
                allow_first_connect.set()
                first.join(3)
                second.join(3)

            self.assertFalse(first.is_alive(), "first writer did not finish")
            self.assertFalse(second.is_alive(), "second writer did not finish")
            self.assertEqual(errors, [])
            self.assertEqual(connection_count, 2)

    def test_thirty_concurrent_writes_complete_without_lock_failures(self):
        writer_count = 30
        with tempfile.TemporaryDirectory() as data_dir:
            store = SQLiteStore(data_dir)
            start = threading.Barrier(writer_count + 1)

            def write_one():
                start.wait(timeout=3)
                store.transaction(
                    lambda connection: connection.execute(
                        "UPDATE profile_config SET revision=revision+1 WHERE singleton=1"
                    )
                )

            with concurrent.futures.ThreadPoolExecutor(max_workers=writer_count) as pool:
                futures = [pool.submit(write_one) for _ in range(writer_count)]
                start.wait(timeout=3)
                for future in futures:
                    future.result(timeout=5)

            revision = store.read(
                lambda connection: connection.execute(
                    "SELECT revision FROM profile_config WHERE singleton=1"
                ).fetchone()[0]
            )
            self.assertEqual(revision, writer_count)

    def test_connection_setup_failure_releases_store_gate(self):
        class OneShotConnectFailureStore(SQLiteStore):
            def __init__(self, data_dir):
                self.fail_next_connect = False
                super().__init__(data_dir)

            def _connect(self, *args, **kwargs):
                if self.fail_next_connect:
                    self.fail_next_connect = False
                    raise StoreError("controlled connection setup failure")
                return super()._connect(*args, **kwargs)

        with tempfile.TemporaryDirectory() as data_dir:
            store = OneShotConnectFailureStore(data_dir)
            store.fail_next_connect = True
            callback_calls = []
            with self.assertRaisesRegex(StoreError, "controlled connection setup failure"):
                store.transaction(lambda connection: callback_calls.append("called"))
            self.assertEqual(callback_calls, [])
            self.assertEqual(
                store.transaction(lambda connection: tuple(
                    connection.execute("SELECT 1").fetchone()
                )),
                (1,),
            )

    def test_external_writer_timeout_fails_closed_and_releases_store_gate(self):
        with tempfile.TemporaryDirectory() as data_dir:
            store = SQLiteStore(data_dir)
            blocker = sqlite3.connect(store.path, isolation_level=None)
            callback_calls = []
            try:
                blocker.execute("BEGIN IMMEDIATE")
                began = time.monotonic()
                with self.assertRaises(StoreError) as raised:
                    store.transaction(lambda connection: callback_calls.append("called"))
                elapsed = time.monotonic() - began
            finally:
                blocker.execute("ROLLBACK")
                blocker.close()

            cause = raised.exception.__cause__
            self.assertIsInstance(cause, sqlite3.OperationalError)
            self.assertIn("locked", str(cause).lower())
            self.assertEqual(getattr(cause, "sqlite_errorcode", None), sqlite3.SQLITE_BUSY)
            self.assertEqual(getattr(cause, "sqlite_errorname", None), "SQLITE_BUSY")
            self.assertEqual(callback_calls, [])
            self.assertLess(elapsed, 1.5, "external writer contention exceeded the one-second budget")

            result = store.transaction(
                lambda connection: connection.execute(
                    "UPDATE profile_config SET revision=revision+1 WHERE singleton=1"
                )
            )
            self.assertIsNotNone(result)
            self.assertEqual(
                store.read(lambda connection: connection.execute(
                    "SELECT revision FROM profile_config WHERE singleton=1"
                ).fetchone()[0]),
                1,
            )

    def test_reentrant_write_callback_times_out_without_wedging_store(self):
        with tempfile.TemporaryDirectory() as data_dir:
            store = SQLiteStore(data_dir)
            began = time.monotonic()

            def nested_write(connection):
                with self.assertRaises(StoreError) as raised:
                    store.transaction(lambda nested: nested.execute("SELECT 1"))
                self.assertIsInstance(raised.exception.__cause__, TimeoutError)

            store.transaction(nested_write)
            elapsed = time.monotonic() - began
            self.assertLess(elapsed, 2.0, "reentrant write callback hung instead of respecting the deadline")
            store.transaction(lambda connection: connection.execute("SELECT 1").fetchone())
