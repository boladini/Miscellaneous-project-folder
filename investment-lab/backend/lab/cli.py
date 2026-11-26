"""Operator CLI: `python -m lab.cli <command>`.

  bootstrap                      create owner + 5 portfolios (idempotent)
  ingest                         fetch market data through the latest finalized session
  research                       run a research job now (enqueue + execute)
  daily [YYYY-MM-DD]             run the daily cycle for a session (default: latest finalized)
  monthly [YYYY-MM]              run the monthly learning review
  tick                           scheduler tick + run due jobs once
  backtest <strategy_version_id> reproduce a backtest from stored rules + current dataset
  smoke-live                     credentialed smoke test of configured external providers
"""
from __future__ import annotations

import json
import sys
from datetime import date, datetime, timezone

from . import market_calendar as cal
from .bootstrap import bootstrap
from .db import session_scope


def main(argv: list[str]) -> int:
    cmd = argv[0] if argv else "help"
    if cmd == "bootstrap":
        print(bootstrap())
    elif cmd == "ingest":
        from .services.market_data import ingest

        bootstrap()
        with session_scope() as s:
            print(json.dumps(ingest(s), default=str, indent=1))
    elif cmd == "research":
        from .jobs import enqueue, run_job

        uid = bootstrap()
        with session_scope() as s:
            job, _ = enqueue(s, uid, "research", f"research:cli:{datetime.now(timezone.utc):%Y%m%dT%H%M}", {}, trigger="manual")
            jid = job.id
        run_job(jid)
        with session_scope() as s:
            from .models import Job

            print(json.dumps(s.get(Job, jid).result or s.get(Job, jid).error, default=str, indent=1))
    elif cmd == "daily":
        from .services.engine import run_daily
        from .services.market_data import ingest

        uid = bootstrap()
        d = date.fromisoformat(argv[1]) if len(argv) > 1 else cal.latest_finalized_session(datetime.now(timezone.utc))
        with session_scope() as s:
            ingest(s)
        with session_scope() as s:
            print(json.dumps(run_daily(s, uid, d), default=str, indent=1))
    elif cmd == "monthly":
        from .agents.learning import run_monthly

        uid = bootstrap()
        with session_scope() as s:
            print(json.dumps(run_monthly(s, uid, argv[1] if len(argv) > 1 else None), default=str, indent=1))
    elif cmd == "tick":
        from .jobs import claim, run_job, schedule_tick

        bootstrap()
        with session_scope() as s:
            print(schedule_tick(s))
        while True:
            with session_scope() as s:
                j = claim(s)
            if not j:
                break
            print("running", j.schedule_key)
            run_job(j.id)
    elif cmd == "backtest":
        from .services.backtests import manual_backtest
        from .models import StrategyVersion

        with session_scope() as s:
            v = s.get(StrategyVersion, argv[1])
            print(json.dumps(manual_backtest(s, v.user_id, v.id), default=str))
    elif cmd == "smoke-live":
        from .smoke import run_smoke

        print(json.dumps(run_smoke(), default=str, indent=1))
    else:
        print(__doc__)
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
