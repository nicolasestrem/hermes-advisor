"""Portable-preview regressions for the bounded worker pool lifecycle.

These tests pin the public preview behavior: idle workers sleep on a
half-second-or-longer interval, shutdown wakes them with an explicit sentinel,
and a shutdown requested by a worker itself never joins itself or a peer that
is also shutting down. A worker-initiated shutdown reports False because the
calling worker is still running; only an outside caller can observe every
owned thread stopped.
"""

from __future__ import annotations

import threading
import time
import unittest

from advisor_core.worker import BoundedWorkerPool

LONG_WAIT_FLOOR = 0.5
LONG_WAIT_CEILING = 1.0
# A sentinel wakeup must beat the old, longer idle wait by a clear margin.
WAKE_BOUND = 0.65


def _alive_threads(prefix: str) -> list[threading.Thread]:
    return [thread for thread in threading.enumerate()
            if thread.name.startswith(prefix) and thread is not threading.current_thread()]


class _RecordingQueue:
    """Queue proxy that records every blocking-wait timeout workers request."""

    def __init__(self, inner):
        self._inner = inner
        self._guard = threading.Lock()
        self.timeouts: list[float | None] = []

    def get(self, block=True, timeout=None):
        with self._guard:
            self.timeouts.append(timeout)
        return self._inner.get(block=block, timeout=timeout)

    def __getattr__(self, name):
        return getattr(self._inner, name)


class PreviewWorkerLifecycleTests(unittest.TestCase):
    def test_idle_workers_use_long_poll_and_shutdown_wakes_them_explicitly(self):
        prefix = "preview-idle-wake"
        pool = BoundedWorkerPool(max_workers=3, max_queued=2, thread_name_prefix=prefix)
        try:
            recorder = _RecordingQueue(pool._queue)
            pool._queue = recorder  # type: ignore[assignment]
            deadline = time.monotonic() + 2
            while len(recorder.timeouts) < 3 and time.monotonic() < deadline:
                time.sleep(0.01)
            self.assertGreaterEqual(len(recorder.timeouts), 3,
                                    "idle workers never asked the queue for work")
            waits = [timeout for timeout in recorder.timeouts if timeout is not None]
            self.assertEqual(len(waits), len(recorder.timeouts),
                             "an idle worker blocked forever instead of using a bounded wait")
            self.assertTrue(all(LONG_WAIT_FLOOR <= timeout <= LONG_WAIT_CEILING for timeout in waits),
                            f"idle polling was not replaced with 0.5-1s waits: {waits}")

            began = time.monotonic()
            self.assertTrue(pool.shutdown(wait=True, timeout=2))
            elapsed = time.monotonic() - began
            self.assertLess(elapsed, WAKE_BOUND,
                            "idle workers waited for the poll interval instead of a shutdown wakeup")
        finally:
            pool.shutdown(wait=True, timeout=2)
        self.assertEqual(_alive_threads(prefix), [])

    def test_background_shutdown_wakes_idle_workers_without_join(self):
        prefix = "preview-idle-nowait"
        pool = BoundedWorkerPool(max_workers=3, max_queued=1, thread_name_prefix=prefix)
        time.sleep(0.1)  # let the fresh threads enter their wait
        began = time.monotonic()
        pool.shutdown(wait=False)
        deadline = time.monotonic() + 1
        while _alive_threads(prefix) and time.monotonic() < deadline:
            time.sleep(0.01)
        elapsed = time.monotonic() - began
        self.assertEqual(_alive_threads(prefix), [],
                         "idle workers were not woken by the shutdown sentinel")
        self.assertLess(elapsed, WAKE_BOUND)

    def test_two_workers_calling_shutdown_do_not_join_each_other_to_death(self):
        prefix = "preview-mutual-stop"
        pool = BoundedWorkerPool(max_workers=2, max_queued=2, thread_name_prefix=prefix)
        barrier = threading.Barrier(2)

        def stop_from_worker():
            barrier.wait(timeout=2)
            # No timeout: a self/peer join here would hang forever.
            return pool.shutdown(wait=True)

        try:
            first = pool.submit(stop_from_worker)
            second = pool.submit(stop_from_worker)
            results = [first.result(timeout=3), second.result(timeout=3)]
            self.assertEqual(results, [False, False],
                             "a worker-initiated shutdown claimed every thread had stopped")
            self.assertTrue(pool.shutdown(wait=True, timeout=2),
                            "shutdown was not confirmed after the caller's work returned")
        finally:
            pool.shutdown(wait=True, timeout=2)
        self.assertEqual(_alive_threads(prefix), [])

    def test_worker_initiated_shutdown_reports_false_until_outside_caller_confirms(self):
        prefix = "preview-self-stop"
        pool = BoundedWorkerPool(max_workers=1, max_queued=1, thread_name_prefix=prefix)
        observed: list[bool] = []

        def stop_from_worker():
            observed.append(pool.shutdown(wait=True, timeout=1))
            return "returned"

        future = pool.submit(stop_from_worker)
        self.assertEqual(future.result(timeout=2), "returned",
                         "worker-initiated shutdown deadlocked")
        self.assertEqual(observed, [False])
        self.assertTrue(pool.shutdown(wait=True, timeout=2))
        self.assertEqual(_alive_threads(prefix), [])

    def test_cancel_shutdown_drains_wakeup_tokens_without_treating_them_as_work(self):
        prefix = "preview-cancel-wake"
        pool = BoundedWorkerPool(max_workers=2, max_queued=2, thread_name_prefix=prefix)
        try:
            pool.shutdown(wait=False)
            self.assertTrue(pool.shutdown(wait=True, timeout=2, cancel_futures=True),
                            "cancel shutdown did not reach a stopped pool")
        finally:
            pool.shutdown(wait=True, timeout=2)
        self.assertEqual(_alive_threads(prefix), [])

    def test_shutdown_timeout_keeps_unresolved_work_and_queue_slot(self):
        prefix = "preview-unresolved"
        pool = BoundedWorkerPool(max_workers=1, max_queued=2, thread_name_prefix=prefix)
        started = threading.Event()
        release = threading.Event()
        ran: list[str] = []

        def blocked_work():
            started.set()
            release.wait(5)
            return "blocked"

        try:
            running = pool.submit(blocked_work)
            self.assertTrue(started.wait(2))
            queued = pool.submit(lambda: ran.append("queued") or "queued")
            self.assertFalse(pool.shutdown(wait=True, timeout=0.2))
            self.assertTrue(any(thread.is_alive() for thread in pool.threads),
                            "shutdown falsely reported a stuck worker stopped")
            release.set()
            self.assertEqual(running.result(timeout=2), "blocked")
            self.assertEqual(queued.result(timeout=2), "queued",
                             "shutdown discarded work that was already admitted")
            self.assertEqual(ran, ["queued"])
            self.assertTrue(pool.shutdown(wait=True, timeout=2))
        finally:
            release.set()
            pool.shutdown(wait=True, timeout=2)
        self.assertEqual(_alive_threads(prefix), [])


if __name__ == "__main__":
    unittest.main(verbosity=2)
