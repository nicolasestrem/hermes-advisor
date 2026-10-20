"""Plugin-owned, public-API daemon worker pool with bounded outstanding work.

No CPython executor internals or host monkeypatches. Idle workers sleep on a
bounded wait rather than a tight poll, and shutdown wakes them with explicit
sentinels. Shutdown reports success only after observing every owned thread
stopped; unresolved work keeps its worker and outstanding slot until that work
actually finishes. A shutdown requested from an owned worker never joins
itself or a peer that is also shutting down, and reports that success is
unconfirmed.
"""
import asyncio
from concurrent.futures import Future
import queue
import threading
import time
import contextvars

# Long enough to avoid a busy idle loop, short enough that a worker blocked in
# queue.get() still notices a state change if a wakeup is ever missed.
IDLE_POLL_SECONDS = 0.75
_WAKE = object()


class BoundedWorkerPool:
    def __init__(self, max_workers, max_queued, thread_name_prefix='advisor-worker'):
        if type(max_workers) is not int or max_workers < 1:
            raise ValueError('max_workers must be a positive integer')
        if type(max_queued) is not int or max_queued < 1:
            raise ValueError('max_queued must be a positive integer')
        self._capacity = max_workers + max_queued
        self._queue = queue.Queue(maxsize=self._capacity)
        self._lock = threading.RLock()
        self._closing = False
        self._outstanding = 0
        self._threads = tuple(threading.Thread(target=self._worker,
                              name=f'{thread_name_prefix}-{index}', daemon=True)
                              for index in range(max_workers))
        for thread in self._threads:
            thread.start()

    @property
    def threads(self):
        return self._threads

    def submit(self, fn, /, *args, **kwargs):
        # Admission and queue insertion share one lock with shutdown, closing
        # the race where a submission could appear after the final drain.
        with self._lock:
            if self._closing:
                raise RuntimeError('cannot submit to a shut down worker pool')
            if self._outstanding >= self._capacity:
                raise RuntimeError('worker pool queue is full')
            future = Future()
            context = contextvars.copy_context()
            self._outstanding += 1
            try:
                self._queue.put_nowait((future, lambda: context.run(fn, *args, **kwargs)))
            except queue.Full:
                self._outstanding -= 1
                raise RuntimeError('worker pool queue is full') from None
            return future

    def shutdown(self, *, wait=True, timeout=None, cancel_futures=False):
        """Stop admission and wake idle workers.

        Returns True only when every owned thread is observed stopped at return
        time. Called from an owned worker it returns False without joining:
        neither the caller nor a peer worker that is also shutting down can be
        safely joined from inside the pool.
        """
        with self._lock:
            self._closing = True
        if cancel_futures:
            while True:
                try:
                    item = self._queue.get_nowait()
                except queue.Empty:
                    break
                if item is _WAKE:
                    self._queue.task_done()
                    continue
                future, _fn = item
                with self._lock:
                    self._outstanding -= 1
                self._queue.task_done()
                # Future callbacks may re-enter shutdown. Never run them while
                # holding a pool lock or any join/wait lock.
                future.cancel()
        # Idle workers wait on a long poll; explicit tokens wake them now.
        self._wake_workers()
        if wait:
            current = threading.current_thread()
            if current in self._threads:
                # Joining self is impossible. Joining peer workers is also
                # unsafe: two workers shutting down together would wait on
                # each other forever. The caller's loop exits after this call
                # returns, so no success can be observed from in here.
                return False
            deadline = None if timeout is None else time.monotonic() + max(0, timeout)
            for thread in self._threads:
                remaining = None if deadline is None else max(0, deadline - time.monotonic())
                thread.join(timeout=remaining)
        return not any(thread.is_alive() for thread in self._threads)

    def _wake_workers(self):
        """Best-effort non-blocking wakeup tokens; work already queued wins."""
        for _thread in self._threads:
            try:
                self._queue.put_nowait(_WAKE)
            except queue.Full:
                return

    def _drained_while_closing(self):
        with self._lock:
            return self._closing and self._outstanding == 0

    def _worker(self):
        while True:
            try:
                item = self._queue.get(timeout=IDLE_POLL_SECONDS)
            except queue.Empty:
                if self._drained_while_closing():
                    return
                continue
            if item is _WAKE:
                self._queue.task_done()
                if self._drained_while_closing():
                    return
                continue
            future, fn = item
            drained = False
            try:
                if future.set_running_or_notify_cancel():
                    try:
                        result = fn()
                    except BaseException as exc:
                        future.set_exception(exc)
                    else:
                        future.set_result(result)
            finally:
                with self._lock:
                    self._outstanding -= 1
                    drained = self._closing and self._outstanding == 0
                self._queue.task_done()
            if drained:
                # The last outstanding item finished; release peers blocked in
                # their poll immediately instead of waiting out the interval.
                self._wake_workers()
                return


def drain_detached_loop(loop, task, on_done, name='advisor-transport-drain'):
    """Keep an abandoned transport's event loop alive on one daemon thread.

    The transport swallowed cancellation, so its termination is unconfirmed.
    Closing the loop beneath it would make that unobservable (and strand any
    thread it is awaiting), so the loop keeps running here until the task
    really finishes. ``on_done`` runs exactly once, after the loop is closed,
    and only then may the caller free the slot the transport still occupies.
    A transport that never finishes keeps this daemon thread (and its slot)
    forever; that is the honest, bounded outcome: one thread per held slot.
    """
    async def wait_quietly():
        await asyncio.wait({task})
        if not task.cancelled():
            task.exception()  # mark retrieved; late results are never published

    def drain():
        try:
            asyncio.set_event_loop(loop)
            loop.run_until_complete(wait_quietly())
        except BaseException:
            pass
        finally:
            try:
                loop.close()
            except Exception:
                pass
            asyncio.set_event_loop(None)
            on_done()

    thread = threading.Thread(target=drain, name=name, daemon=True)
    thread.start()
    return thread
