"""Subprocess entry points for real multi-process Service tests.

Not a test module (no ``test_`` prefix). FIXTURE LABELING: the reviewers here
are synthetic controlled transports; no provider, host, or paid call is made.

Usage: ``python proc_helper.py MODE DATA_DIR SESSION [ARG]``
"""

import asyncio
import os
import sys
import threading
import time

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))

from advisor_core import ConfigurationConflict, Service  # noqa: E402

ROUTE = {"provider": "nous", "model": "local-controlled-transport"}


def own_blocked(data_dir, session, ready_path, swallow=False):
    """Own ``session`` with a review parked in a controlled transport."""
    release = threading.Event()

    async def reviewer(packet, settings):
        with open(ready_path, "w", encoding="utf-8") as handle:
            handle.write("entered")
        if swallow:
            task = asyncio.ensure_future(asyncio.to_thread(release.wait))
            while True:
                try:
                    await asyncio.shield(task)
                    break
                except asyncio.CancelledError:
                    continue
        else:
            await asyncio.Event().wait()
        return {"outcome": "no_findings", "findings": []}

    if swallow:
        Service.TRANSPORT_CANCEL_GRACE_SECONDS = 600
    service = Service(data_dir, reviewer)
    service.discover(session, "cli")
    service.configure(session, {"route": ROUTE, "deadline_seconds": 1}, 0)
    service.enable(session)
    service.capture(session, "turn-owned", "request", "answer", [])
    time.sleep(120)


def fork_orphan(data_dir, session, pid_path):
    """Own a session, fork a lingering child, then die without cleanup."""
    async def reviewer(packet, settings):
        return {"outcome": "no_findings", "findings": []}

    service = Service(data_dir, reviewer)
    service.discover(session, "cli")
    pid = os.fork()
    if pid == 0:
        time.sleep(60)
        os._exit(0)
    with open(pid_path, "w", encoding="utf-8") as handle:
        handle.write(str(pid))
    os._exit(0)  # owner dies; the forked child must not keep the flock alive


def crash_after_reserve(data_dir, session):
    """Die the instant the invocation reservation commits, before any call."""
    async def reviewer(packet, settings):
        os._exit(99)  # must never run: the process dies first

    service = Service(data_dir, reviewer)
    original = service._reserve_invocation

    def reserve_then_die(work, allowance):
        reserved = original(work, allowance)
        os._exit(0)

    service._reserve_invocation = reserve_then_die
    service.discover(session, "cli")
    service.configure(session, {"route": ROUTE}, 0)
    service.enable(session)
    service.capture(session, "turn-reserved", "request", "answer", [])
    time.sleep(30)


def configure_race(data_dir, session, go_at):
    """All racers open a brand-new store and configure at the same instant."""
    async def reviewer(packet, settings):
        return {"outcome": "no_findings", "findings": []}

    while time.time() < float(go_at):
        time.sleep(0.0005)
    service = Service(data_dir, reviewer)  # first-start initialization race
    service.discover(session, "cli")
    try:
        service.configure(session, {"route": ROUTE}, 0)
        print("ok", flush=True)
    except ConfigurationConflict:
        print("conflict", flush=True)
    os._exit(0)


if __name__ == "__main__":
    mode, data_dir, session = sys.argv[1:4]
    extra = sys.argv[4:]
    if mode == "own":
        own_blocked(data_dir, session, extra[0], swallow=False)
    elif mode == "own_swallow":
        own_blocked(data_dir, session, extra[0], swallow=True)
    elif mode == "fork_orphan":
        fork_orphan(data_dir, session, extra[0])
    elif mode == "crash_after_reserve":
        crash_after_reserve(data_dir, session)
    elif mode == "configure_race":
        configure_race(data_dir, session, extra[0])
    else:
        raise SystemExit(f"unknown mode {mode}")
