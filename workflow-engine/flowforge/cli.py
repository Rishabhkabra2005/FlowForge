"""Command line interface.

Usage:
    python -m flowforge validate examples/deploy_workflow.json
    python -m flowforge run     examples/deploy_workflow.json --var environment=production
    python -m flowforge steps
    python -m flowforge serve   --port 8000
"""
from __future__ import annotations

import argparse
import json
import sys
import time

from flowforge import __version__
from flowforge.engine import WorkflowEngine
from flowforge.models import RunStatus, StepStatus, Workflow, WorkflowValidationError
from flowforge.registry import default_registry


def _load_workflow(path: str) -> Workflow:
    with open(path, "r", encoding="utf-8") as handle:
        data = json.load(handle)
    return Workflow.from_dict(data)


def _load_plugins(plugin_paths: list[str]) -> None:
    # custom step types come from plain python files, exec'd into a fresh namespace
    for path in plugin_paths or []:
        namespace: dict = {}
        with open(path, "r", encoding="utf-8") as handle:
            code = handle.read()
        exec(compile(code, path, "exec"), namespace)  # noqa: S102 - explicit plugin mechanism


def cmd_validate(args) -> int:
    try:
        workflow = _load_workflow(args.workflow)
    except WorkflowValidationError as exc:
        print(f"[ERROR] {args.workflow}: invalid definition")
        for error in exc.errors:
            print(f"  - {error}")
        return 1
    except (OSError, json.JSONDecodeError) as exc:
        print(f"[ERROR] could not read {args.workflow}: {exc}")
        return 1

    _load_plugins(args.plugin)
    errors = WorkflowEngine().validate(workflow)
    if errors:
        print(f"[ERROR] {args.workflow}: {len(errors)} error(s)")
        for error in errors:
            print(f"  - {error}")
        return 1
    print(f"[OK] {args.workflow} is valid: '{workflow.name}' v{workflow.version} with {len(workflow.steps)} step(s)")
    for st in workflow.steps:
        deps = f"  after: {', '.join(st.depends_on)}" if st.depends_on else "  no dependencies"
        print(f"   - {st.id:>16} [{st.type}] {deps}")
    return 0


def cmd_run(args) -> int:
    try:
        workflow = _load_workflow(args.workflow)
    except (OSError, json.JSONDecodeError, WorkflowValidationError) as exc:
        print(f"[ERROR] could not load workflow: {exc}")
        return 1

    _load_plugins(args.plugin)
    engine = WorkflowEngine()
    errors = engine.validate(workflow)
    if errors:
        print(f"[ERROR] workflow is invalid:")
        for error in errors:
            print(f"  - {error}")
        return 1

    for var in args.var or []:
        key, _, value = var.partition("=")
        workflow.variables[key] = value

    print(f">> running '{workflow.name}' v{workflow.version} ({len(workflow.steps)} steps)")
    result = engine.execute(workflow, log_fn=lambda msg, level="info", step_id=None: _live_log(msg, level, step_id))

    print()
    for step_result in result.steps.values():
        icon = {StepStatus.SUCCESS: "[OK]", StepStatus.FAILED: "[ERROR]", StepStatus.SKIPPED: "-"}.get(step_result.status, "?")
        detail = step_result.error or step_result.reason or ""
        duration = f"{step_result.duration_ms:.0f}ms" if step_result.duration_ms is not None else ""
        attempts = f" ({step_result.attempts} attempt(s))" if step_result.attempts > 1 else ""
        print(f"  {icon} {step_result.step_id:<20} {step_result.status.value:<8} {duration:<9}{attempts} {detail}")
    print()
    print(f"  run {result.run_id}: {result.status.value} in {result.duration_ms:.0f}ms")
    return 0 if result.status == RunStatus.SUCCESS else 1


def _live_log(message: str, level: str = "info", step_id=None) -> None:
    prefix = f"[{step_id}]" if step_id else "      "
    if level == "error":
        print(f"  {prefix} ERROR: {message}", file=sys.stderr)
    else:
        print(f"  {prefix} {message}")


def cmd_steps(_args) -> int:
    print(f"FlowForge v{__version__} - registered step types ({len(default_registry.names())}):")
    for name in default_registry.names():
        cls = default_registry.get(name)
        doc = (cls.__doc__ or "").strip().splitlines()
        summary = doc[0] if doc else ""
        print(f"  - {name:<12} {summary}")
    return 0


def cmd_serve(args) -> int:
    _load_plugins(args.plugin)
    try:
        from flowforge.server.app import start_server

        start_server(host=args.host, port=args.port)
        return 0
    except ImportError as exc:
        print(f"[ERROR] cannot start the server: {exc}")
        print("  install extras with:  pip install -r requirements.txt")
        return 1


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="flowforge", description="JSON-driven workflow execution engine")
    parser.add_argument("--version", action="version", version=f"flowforge {__version__}")
    sub = parser.add_subparsers(dest="command", required=True)

    p_validate = sub.add_parser("validate", help="validate a workflow JSON file")
    p_validate.add_argument("workflow", help="path to workflow JSON")
    p_validate.add_argument("--plugin", action="append", default=[], help="Python file with custom step types (repeatable)")
    p_validate.set_defaults(func=cmd_validate)

    p_run = sub.add_parser("run", help="execute a workflow")
    p_run.add_argument("workflow", help="path to workflow JSON")
    p_run.add_argument("--var", "-v", action="append", default=[], help="override a variable: key=value (repeatable)")
    p_run.add_argument("--plugin", action="append", default=[], help="Python file with custom step types (repeatable)")
    p_run.set_defaults(func=cmd_run)

    p_steps = sub.add_parser("steps", help="list registered step types")
    p_steps.set_defaults(func=cmd_steps)

    p_serve = sub.add_parser("serve", help="start the REST API + web dashboard")
    p_serve.add_argument("--host", default="127.0.0.1")
    p_serve.add_argument("--port", type=int, default=8000)
    p_serve.add_argument("--plugin", action="append", default=[], help="Python file with custom step types (repeatable)")
    p_serve.set_defaults(func=cmd_serve)

    return parser


def main(argv=None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    started = time.time()
    try:
        code = args.func(args)
    except KeyboardInterrupt:
        print("\ninterrupted")
        return 130
    return code


if __name__ == "__main__":
    sys.exit(main())