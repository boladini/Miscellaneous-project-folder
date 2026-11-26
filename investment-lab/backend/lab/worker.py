"""Background worker entrypoint: `python -m lab.worker`. Runs without any browser open."""
from __future__ import annotations

import logging
import signal
import sys

from .bootstrap import bootstrap
from .jobs import worker_loop


def main() -> None:
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s %(message)s")
    signal.signal(signal.SIGTERM, lambda *_: sys.exit(0))
    bootstrap()
    worker_loop()


if __name__ == "__main__":
    main()
