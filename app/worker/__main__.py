"""``python -m app.worker [--once] [--worker-id ID]``: reap, run a job, pump webhooks."""

from __future__ import annotations

import argparse
import os
import signal
import socket
import threading

from app.core.config import get_settings
from app.core.db import session_scope
from app.core.logging import configure_logging, get_logger
from app.worker.reaper import reap_stale
from app.worker.runner import run_one

log = get_logger("app.worker")


def run_iteration(worker_id: str) -> bool:
    """One loop step. True when a job was run."""
    from app.reporting.webhooks import dispatch_pending

    with session_scope() as session:
        reap_stale(session)
    job_id = run_one(worker_id)
    with session_scope() as session:
        dispatch_pending(session)
    return job_id is not None


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="python -m app.worker")
    parser.add_argument("--once", action="store_true", help="run one iteration and exit")
    parser.add_argument("--worker-id", help="name stored in jobs.locked_by (default host:pid)")
    args = parser.parse_args(argv)
    configure_logging("worker")
    worker_id = args.worker_id or f"{socket.gethostname()}:{os.getpid()}"

    stop = threading.Event()
    if not args.once:
        # Finish the current job, then exit: compose sends SIGTERM on `down`.
        for sig in (signal.SIGTERM, signal.SIGINT):
            signal.signal(sig, lambda *_: stop.set())

    while not stop.is_set():
        try:
            ran = run_iteration(worker_id)
        except Exception:
            log.exception("worker.iteration_failed")
            if args.once:
                return 1
            ran = False
        if args.once:
            return 0
        if not ran:  # idle: sleep; after a job go straight to the next one
            stop.wait(get_settings().worker_poll_interval_s)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
