"""Unit tests for the plugin-owned bounded worker pool (advisor_core.worker)."""

from __future__ import annotations

import threading
import time
import contextvars
from concurrent.futures import Future

import unittest

from advisor_core.worker import BoundedWorkerPool


def _alive_threads(prefix: str) -> list[threading.Thread]:
    return [t for t in threading.enumerate() if t.name.startswith(prefix) and t is not threading.current_thread()]


class WorkerTests(unittest.TestCase):
    def test_submit_captures_context_at_admission(self):
        value = contextvars.ContextVar("worker-test-value", default="missing")
        value.set("captured")
        pool = BoundedWorkerPool(1, 1, thread_name_prefix="context-capture")
        try:
            future = pool.submit(value.get)
            value.set("changed")
            self.assertEqual(future.result(timeout=2), "captured")
        finally:
            self.assertTrue(pool.shutdown(wait=True, timeout=2))

    def test_submit_captures_distinct_context_for_each_job(self):
        value = contextvars.ContextVar("worker-test-distinct", default="missing")
        pool = BoundedWorkerPool(1, 2, thread_name_prefix="context-distinct")
        try:
            value.set("first")
            first = pool.submit(value.get)
            value.set("second")
            second = pool.submit(value.get)
            self.assertEqual(first.result(timeout=2), "first")
            self.assertEqual(second.result(timeout=2), "second")
        finally:
            self.assertTrue(pool.shutdown(wait=True, timeout=2))

    def test_submit_returns_future_with_done_result_and_callback(self):
        pool = BoundedWorkerPool(max_workers=1, max_queued=4, thread_name_prefix="tw-callback")
        try:
            seen: list[Future] = []
            future = pool.submit(lambda: 41 + 1)
            future.add_done_callback(seen.append)
            assert future.result(timeout=5) == 42
            assert future.done() is True
            assert seen == [future]
            assert future.exception() is None
        finally:
            pool.shutdown(wait=True, timeout=5)


    def test_exception_travels_through_future_result(self):
        pool = BoundedWorkerPool(max_workers=1, max_queued=4, thread_name_prefix="tw-error")
        try:
            def boom() -> None:
                raise ValueError("reviewer exploded")

            future = pool.submit(boom)
            with self.assertRaisesRegex(ValueError, "reviewer exploded"):
                future.result(timeout=5)
            assert future.done() and isinstance(future.exception(), ValueError)
        finally:
            pool.shutdown(wait=True, timeout=5)


    def test_threads_are_public_daemon_threads_bounded_by_constructor(self):
        prefix = "tw-bound"
        pool = BoundedWorkerPool(max_workers=2, max_queued=8, thread_name_prefix=prefix)
        try:
            threads = _alive_threads(prefix)
            assert len(threads) == 2
            assert all(thread.daemon for thread in threads)
            assert all(isinstance(thread, threading.Thread) for thread in threads)
            # Not a CPython executor; no private executor state is involved.
            import concurrent.futures

            assert not isinstance(pool, concurrent.futures.ThreadPoolExecutor)
            assert len(pool.threads) == 2
        finally:
            pool.shutdown(wait=True, timeout=5)


    def test_concurrency_is_capped_at_max_workers(self):
        prefix = "tw-cap"
        pool = BoundedWorkerPool(max_workers=2, max_queued=8, thread_name_prefix=prefix)
        try:
            running = threading.Semaphore(0)
            release = threading.Event()
            concurrent_peak = {"value": 0, "lock": threading.Lock()}
            live = {"count": 0}

            def work() -> int:
                with concurrent_peak["lock"]:
                    live["count"] += 1
                    concurrent_peak["value"] = max(concurrent_peak["value"], live["count"])
                running.release()
                release.wait(5)
                with concurrent_peak["lock"]:
                    live["count"] -= 1
                return 1

            futures = [pool.submit(work) for _ in range(5)]
            # Exactly two workers pick work up; the rest stay queued.
            assert running.acquire(timeout=5) and running.acquire(timeout=5)
            assert running.acquire(timeout=0.3) is False  # only two workers exist
            assert concurrent_peak["value"] <= 2
            release.set()
            assert [future.result(timeout=5) for future in futures] == [1] * 5
            assert concurrent_peak["value"] <= 2
        finally:
            pool.shutdown(wait=True, timeout=5)


    def test_queue_is_bounded_no_unbounded_submission(self):
        prefix = "tw-full"
        pool = BoundedWorkerPool(max_workers=1, max_queued=1, thread_name_prefix=prefix)
        try:
            release = threading.Event()
            started = threading.Event()
            def running():
                started.set()
                release.wait(5)
            pool.submit(running)
            self.assertTrue(started.wait(5))
            queued = pool.submit(lambda: 1)  # fills the one permitted pending slot
            with self.assertRaisesRegex(RuntimeError, "full"):
                pool.submit(lambda: 2)
            release.set()
            assert queued.result(timeout=5) == 1
        finally:
            pool.shutdown(wait=True, timeout=5)


    def test_cancel_queued_task_never_runs_and_does_not_release_physical_slot(self):
        prefix = "tw-cancel"
        pool = BoundedWorkerPool(max_workers=1, max_queued=4, thread_name_prefix=prefix)
        try:
            # "Physical slot": a bounded semaphore held by a stuck running task,
            # mirroring the service's process call slots.
            slots = threading.BoundedSemaphore(1)
            slot_events = {"acquired": 0, "released": 0, "lock": threading.Lock()}
            release = threading.Event()

            def stuck_with_slot() -> None:
                assert slots.acquire(timeout=5)
                with slot_events["lock"]:
                    slot_events["acquired"] += 1
                release.wait(5)
                with slot_events["lock"]:
                    slot_events["released"] += 1
                slots.release()

            running_future = pool.submit(stuck_with_slot)
            deadline = time.monotonic() + 5
            while time.monotonic() < deadline and slot_events["acquired"] == 0:
                time.sleep(0.01)

            def never_runs() -> None:
                with slot_events["lock"]:
                    slot_events["acquired"] += 1  # would wrongly count as slot use
                slots.release()

            cancelled_seen: list[Future] = []
            queued = pool.submit(never_runs)
            queued.add_done_callback(cancelled_seen.append)
            assert queued.cancel() is True
            assert queued.cancelled() and queued.done()
            assert cancelled_seen == [queued]
            with self.assertRaises(Exception):
                queued.result(timeout=1)
            # The unresolved holder still owns its slot; cancellation released nothing.
            with slot_events["lock"]:
                assert slot_events["acquired"] == 1
                assert slot_events["released"] == 0
            assert slots.acquire(timeout=0.1) is False  # slot still held by stuck task
            release.set()
            running_future.result(timeout=5)
            with slot_events["lock"]:
                assert slot_events["released"] == 1
            assert slots.acquire(timeout=1) is True  # slot returned only by its holder
            slots.release()
        finally:
            pool.shutdown(wait=True, timeout=5)


    def test_shutdown_cancel_futures_cancels_pending_not_running(self):
        prefix = "tw-shutdown"
        pool = BoundedWorkerPool(max_workers=1, max_queued=4, thread_name_prefix=prefix)
        started = threading.Event()
        release = threading.Event()
        ran = {"queued": False}

        def block_until_released() -> None:
            started.set()
            release.wait(5)

        running = pool.submit(block_until_released)
        queued = pool.submit(lambda: ran.__setitem__("queued", True))
        assert started.wait(5)  # worker is busy; queued is deterministically pending
        pool.shutdown(wait=False, cancel_futures=True)
        assert queued.cancelled() is True
        release.set()
        assert running.result(timeout=5) is None
        assert ran["queued"] is False


    def test_shutdown_wait_returns_bounded_false_when_transport_stuck(self):
        prefix = "tw-wait"
        pool = BoundedWorkerPool(max_workers=1, max_queued=2, thread_name_prefix=prefix)
        release = threading.Event()
        try:
            pool.submit(release.wait, 30)
            started = time.monotonic()
            finished = pool.shutdown(wait=True, timeout=0.2)
            elapsed = time.monotonic() - started
            assert finished is False
            assert 0.1 <= elapsed < 5
        finally:
            release.set()
            assert pool.shutdown(wait=True, timeout=5) is True


    def test_submit_after_shutdown_is_rejected(self):
        pool = BoundedWorkerPool(max_workers=1, max_queued=1, thread_name_prefix="tw-after")
        pool.shutdown(wait=True, timeout=5)
        with self.assertRaisesRegex(RuntimeError, "shut down"):
            pool.submit(lambda: 1)


    def test_invalid_constructor_bounds_rejected(self):
        with self.assertRaises(ValueError):
            BoundedWorkerPool(max_workers=0, max_queued=1)
        with self.assertRaises(ValueError):
            BoundedWorkerPool(max_workers=2, max_queued=0)


    def test_service_close_success_waits_for_owned_worker_threads(self):
        import tempfile
        from advisor_core import Service
        async def reviewer(packet, settings):
            return {'outcome': 'no_findings', 'findings': []}
        with tempfile.TemporaryDirectory() as folder:
            service = Service(folder, reviewer)
            self.assertTrue(service.close(timeout=2))
            self.assertFalse(any(thread.is_alive() for thread in service._executor.threads))

    def test_shutdown_success_requires_observed_thread_exit(self):
        for index in range(10):
            pool = BoundedWorkerPool(2, 1, thread_name_prefix=f'joined-{index}')
            self.assertTrue(pool.shutdown(wait=True, timeout=2))
            self.assertFalse(any(thread.is_alive() for thread in pool.threads))

    def test_nonblocking_shutdown_does_not_claim_stuck_thread_stopped(self):
        pool = BoundedWorkerPool(1, 1, thread_name_prefix='unresolved')
        started, release = threading.Event(), threading.Event()
        def running():
            started.set()
            release.wait(5)
        try:
            pool.submit(running)
            self.assertTrue(started.wait(2))
            self.assertFalse(pool.shutdown(wait=False))
            self.assertTrue(any(thread.is_alive() for thread in pool.threads))
        finally:
            release.set()
            self.assertTrue(pool.shutdown(wait=True, timeout=2))
            self.assertFalse(any(thread.is_alive() for thread in pool.threads))

    def test_cancel_callback_can_reenter_shutdown_without_deadlock(self):
        pool = BoundedWorkerPool(1, 2, thread_name_prefix='reenter')
        started, release, callback_done = threading.Event(), threading.Event(), threading.Event()
        def running():
            started.set()
            release.wait(5)
        pool.submit(running)
        self.assertTrue(started.wait(2))
        queued = pool.submit(lambda: None)
        queued.add_done_callback(lambda future: (pool.shutdown(wait=False), callback_done.set()))
        terminator = threading.Thread(target=lambda: pool.shutdown(wait=False, cancel_futures=True), daemon=True)
        terminator.start()
        try:
            self.assertTrue(callback_done.wait(1), 'Shutdown cancellation callback deadlocked')
        finally:
            release.set()
            terminator.join(timeout=2)
            pool.shutdown(wait=True, timeout=2)


if __name__ == "__main__":
    unittest.main(verbosity=2)
