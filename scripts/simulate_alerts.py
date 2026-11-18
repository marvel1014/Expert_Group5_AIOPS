#!/usr/bin/env python3
"""模拟告警源：向本地数据库注入带错误堆栈的告警/日志，或回放故障场景。

用法：
    python scripts/simulate_alerts.py --count 30 --host db-host-01 --service mysql-primary \
        --rule "MySQL connection pool exhausted"
    python scripts/simulate_alerts.py --scenario db-pool-exhausted --compression 10
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO_ROOT / "backend"))
sys.path.insert(0, str(REPO_ROOT / ".deps"))

from app.db import init_db, session_scope  # noqa: E402


def main() -> int:
    parser = argparse.ArgumentParser(description="AIOps 模拟告警源")
    parser.add_argument("--scenario", help="故障场景 id（优先于 burst 模式）")
    parser.add_argument("--compression", type=float, default=10.0)
    parser.add_argument("--count", type=int, default=30)
    parser.add_argument("--host", default="order-host-01")
    parser.add_argument("--service", default="order-service")
    parser.add_argument("--rule", default="CPU usage high")
    parser.add_argument("--severity", default="high")
    parser.add_argument("--spread-seconds", type=int, default=60)
    parser.add_argument("--seed", type=int, default=42)
    args = parser.parse_args()

    init_db()

    if args.scenario:
        from app.evaluation.scenarios import load_scenarios_from_dir
        from app.simulation import inject_scenario

        with session_scope() as db:
            load_scenarios_from_dir(db)
        with session_scope() as db:
            result = inject_scenario(
                db,
                args.scenario,
                compression=args.compression,
                seed=args.seed,
            )
    else:
        from app.simulation import inject_burst

        with session_scope() as db:
            result = inject_burst(
                db,
                host=args.host,
                service=args.service,
                count=args.count,
                rule=args.rule,
                severity=args.severity,
                spread_seconds=args.spread_seconds,
            )
    print(json.dumps(result, ensure_ascii=False, indent=2, default=str))
    return 0


if __name__ == "__main__":
    sys.exit(main())
