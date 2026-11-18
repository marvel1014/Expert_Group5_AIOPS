"""命令行工具：seed / demo / evaluate / replay。"""

from __future__ import annotations

import argparse
import json
import sys
from typing import Any

from app.core.logging import setup_logging
from app.db import init_db, session_scope


def _print(data: Any) -> None:
    print(json.dumps(data, ensure_ascii=False, indent=2, default=str))


def cmd_seed(_: argparse.Namespace) -> int:
    init_db()
    from app.evaluation.scenarios import load_scenarios_from_dir
    from app.topology.service import load_topology

    load_topology(force=True)
    with session_scope() as db:
        count = load_scenarios_from_dir(db)
    overview = {
        "seeded": True,
        "scenarios": count,
        "topology_nodes": len(load_topology().nodes),
    }
    _print(overview)
    return 0


def cmd_demo(args: argparse.Namespace) -> int:
    init_db()
    from app.diagnosis.service import run_diagnosis_sync
    from app.models import Incident
    from app.reporting.service import generate_report
    from app.simulation import inject_burst

    with session_scope() as db:
        injected = inject_burst(
            db,
            host=args.host,
            service=args.service,
            count=args.count,
            rule=args.rule,
            severity=args.severity,
        )
    if not injected["incident_ids"]:
        _print({"injected": injected, "error": "未生成故障事件"})
        return 1

    incident_id = injected["incident_ids"][0]
    with session_scope() as db:
        incident = db.get(Incident, incident_id)
        assert incident is not None
        diagnosis = run_diagnosis_sync(db, incident)
        diagnosis_data = {
            "id": diagnosis.id,
            "status": diagnosis.status,
            "source": diagnosis.source,
            "root_cause_category": diagnosis.root_cause_category,
            "root_cause_node": diagnosis.root_cause_node,
            "confidence": diagnosis.confidence,
            "needs_review": diagnosis.needs_review,
        }
        report = generate_report(db, incident_id, diagnosis.id)
        report_data = {
            "id": report.id,
            "version": report.version,
            "risk_level": report.risk_level,
        }
    _print(
        {
            "injected": injected,
            "incident_id": incident_id,
            "diagnosis": diagnosis_data,
            "report": report_data,
        }
    )
    return 0


def cmd_evaluate(args: argparse.Namespace) -> int:
    init_db()
    from app.evaluation.scenarios import load_scenarios_from_dir
    from app.evaluation.service import run_evaluation

    with session_scope() as db:
        load_scenarios_from_dir(db)
        evaluation = run_evaluation(
            db,
            scenario_ids=args.scenario or None,
            seed=args.seed,
        )
        result = {
            "evaluation_id": evaluation.id,
            "status": evaluation.status,
            "metrics": evaluation.metrics,
        }
    _print(result)
    return 0 if evaluation.status == "completed" else 1


def cmd_replay(args: argparse.Namespace) -> int:
    init_db()
    from app.evaluation.scenarios import load_scenarios_from_dir
    from app.evaluation.service import replay_scenario

    with session_scope() as db:
        load_scenarios_from_dir(db)
        batch = replay_scenario(
            db,
            args.scenario_id,
            compression=args.compression,
            seed=args.seed,
        )
        _print(
            {
                "batch_id": batch.id,
                "scenario_id": batch.scenario_id,
                "status": batch.status,
                "stats": batch.stats,
            }
        )
    return 0 if batch.status == "completed" else 1


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="app.cli", description="AIOps 平台命令行工具"
    )
    sub = parser.add_subparsers(dest="command", required=True)

    seed = sub.add_parser("seed", help="建库并载入拓扑与场景库")
    seed.set_defaults(func=cmd_seed)

    demo = sub.add_parser("demo", help="注入一次演示故障并生成诊断与报告")
    demo.add_argument("--host", default="db-host-01")
    demo.add_argument("--service", default="mysql-primary")
    demo.add_argument("--count", type=int, default=30)
    demo.add_argument("--rule", default="MySQL connection pool exhausted")
    demo.add_argument("--severity", default="critical")
    demo.set_defaults(func=cmd_demo)

    evaluate = sub.add_parser("evaluate", help="回放场景库并量化诊断准确率")
    evaluate.add_argument("--scenario", action="append", help="只评估指定场景 id")
    evaluate.add_argument("--seed", type=int, default=42)
    evaluate.set_defaults(func=cmd_evaluate)

    replay = sub.add_parser("replay", help="回放单个故障场景")
    replay.add_argument("scenario_id")
    replay.add_argument("--compression", type=float, default=10.0)
    replay.add_argument("--seed", type=int, default=42)
    replay.set_defaults(func=cmd_replay)

    return parser


def main(argv: list[str] | None = None) -> int:
    setup_logging()
    parser = build_parser()
    args = parser.parse_args(argv)
    return int(args.func(args))


if __name__ == "__main__":
    sys.exit(main())
