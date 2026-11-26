"""`python -m lab.bootstrap_cli` — idempotent owner/portfolio creation at container start."""
import logging

from .bootstrap import bootstrap

if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO)
    bootstrap()
