"""``python -m app.scheduler [--once]``: plan scheduled scans every interval."""

from __future__ import annotations

import argparse
import signal
import threading

from app.core import clock
from app.core.config import get_settings
from app.core.db import session_scope
from app.core.logging import configure_logging, get_logger
from app.scheduler.service import tick

log = get_logger("app.scheduler")


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="python -m app.scheduler")
    parser.add_argument("--once", action="store_true", help="run a single tick and exit")
    args = parser.parse_args(argv)
    configure_logging("scheduler")

    stop = threading.Event()
    if not args.once:
        for sig in (signal.SIGTERM, signal.SIGINT):
            signal.signal(sig, lambda *_: stop.set())

    while True:
        try:
            with session_scope() as session:
                planned = tick(session, clock.now())
            log.info("scheduler.tick", extra={"planned": len(planned)})
        except Exception:
            log.exception("scheduler.tick_failed")
            if args.once:
                return 1
        if args.once or stop.wait(get_settings().scheduler_interval_s):
            return 0


if __name__ == "__main__":
    raise SystemExit(main())
