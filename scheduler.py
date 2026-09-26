from __future__ import annotations

import argparse
from datetime import datetime, timezone
import sys

from kaban.projects import ProjectConfigError, ProjectRegistry
from kaban.scheduler.engine import SchedulerEngine
from kaban.scheduler.health import SchedulerHealthError, check_heartbeat
from kaban.scheduler.runner import run_forever
from kaban.scheduler.store import SchedulerStateError


def _aware_iso(value: str | None) -> datetime:
    if value is None:
        return datetime.now(timezone.utc)
    parsed = datetime.fromisoformat(value)
    if parsed.tzinfo is None:
        raise ValueError("--now должен содержать timezone offset, например +00:00")
    return parsed.astimezone(timezone.utc)


def _projects(registry, project_id: str | None):
    return (registry.get(project_id),) if project_id else registry.registered()


def _print_list(registry, project_id: str | None) -> None:
    for project in _projects(registry, project_id):
        auto = project.automation
        if not auto.jobs:
            state = "enabled" if auto.enabled else "disabled"
            print(f"{project.id}\t-\t-\t-\t{project.timezone}\t{state}\t(no jobs)")
            continue
        for job in auto.jobs:
            enabled = auto.enabled and job.enabled
            print(
                f"{project.id}\t{job.id}\t{job.handler}\t{job.cron}\t{project.timezone}\t"
                f"{'enabled' if enabled else 'disabled'}"
            )


def _print_status(registry, engine, project_id: str | None) -> None:
    for project in _projects(registry, project_id):
        if not project.automation.jobs:
            print(f"{project.id}\t-\tnever-run\t(no jobs)")
            continue
        for job in project.automation.jobs:
            try:
                state = engine.store.load_state(project.id, job.id)
                result = state.last_result or "never-run"
                slot = state.slot_id or "-"
                attempt = state.attempt
                retry = state.next_retry_at or "-"
                print(f"{project.id}\t{job.id}\t{result}\tslot={slot}\tattempt={attempt}\tnext_retry={retry}")
            except SchedulerStateError as exc:
                print(f"{project.id}\t{job.id}\tcorrupt\t{exc}")


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="KABAN universal Project scheduler")
    sub = parser.add_subparsers(dest="command", required=True)
    for name in ("list", "status"):
        p = sub.add_parser(name)
        p.add_argument("--project")
    p = sub.add_parser("tick")
    p.add_argument("--project")
    p.add_argument("--now")
    p = sub.add_parser("run")
    p.add_argument("--project")
    p.add_argument("--poll-seconds", type=float, default=30.0)
    p = sub.add_parser("run-now")
    p.add_argument("--project", required=True)
    p.add_argument("--job", required=True)
    p.add_argument("--now")
    p = sub.add_parser("health")
    p.add_argument("--max-age-seconds", type=int, default=120)
    return parser


def main(argv: list[str] | None = None, *, registry=None, engine=None) -> int:
    parser = _build_parser()
    try:
        args = parser.parse_args(argv)
        if args.command == "health":
            try:
                heartbeat = check_heartbeat(max_age_seconds=args.max_age_seconds)
            except SchedulerHealthError as exc:
                print(f"UNHEALTHY: {exc}", file=sys.stderr)
                return 1
            print(f"HEALTHY\tupdated_at={heartbeat.updated_at.isoformat()}\tpid={heartbeat.pid}")
            return 0
        registry = registry or ProjectRegistry()
        if args.command == "list":
            _print_list(registry, args.project)
            return 0
        engine = engine or SchedulerEngine(registry)
        if args.command == "status":
            _print_status(registry, engine, args.project)
            return 0
        if args.command == "tick":
            now = _aware_iso(args.now)
            for record in engine.tick(now_utc=now, project_id=args.project):
                print(f"{record.project_id}\t{record.job_id}\t{record.result}\tattempt={record.attempt}\t{record.message}")
            return 0
        if args.command == "run":
            run_forever(engine, project_id=args.project, poll_seconds=args.poll_seconds)
            return 0
        if args.command == "run-now":
            now = _aware_iso(args.now)
            record = engine.run_now(args.project, args.job, now_utc=now)
            print(f"{record.project_id}\t{record.job_id}\t{record.result}\tattempt={record.attempt}\t{record.message}")
            return 0
        raise ValueError("Неизвестная scheduler command")
    except (ProjectConfigError, SchedulerStateError, ValueError) as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
