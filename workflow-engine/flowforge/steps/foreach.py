"""Foreach step: run a sub-workflow for every item in a list.

Config:
  items    - a list, or an expression resolving to one (e.g. "variables.cities")
  parallel - boolean, run iterations concurrently (default false)
  steps    - list of step definitions executed per item

Inside child steps: {{ this.item }} is the current item, {{ this.index }}
is its 0-based index, and {{ variables.* }} still works.
"""
from __future__ import annotations

import time
from concurrent.futures import ThreadPoolExecutor

from flowforge.models import Step, StepResult, StepStatus, WorkflowValidationError
from flowforge.registry import step
from flowforge.steps.base import RunContext, StepExecutor, StepOutput


def _resolve_items(raw, run: RunContext):
    if isinstance(raw, list):
        return run.interpolate(raw)
    text = str(raw).strip()
    if text.startswith("{{") and text.endswith("}}"):
        return run.resolve(text[2:-2].strip())
    if text.startswith(("variables.", "steps.", "this.")):
        return run.resolve(text)
    return run.interpolate(raw)


@step("foreach")
class ForeachExecutor(StepExecutor):
    """Loop over a list and run child steps per item (optionally in parallel)."""

    def validate_config(self, config: dict) -> list[str]:
        errors = []
        if "items" not in config:
            errors.append("foreach step requires 'items'")
        child_steps = config.get("steps")
        if not isinstance(child_steps, list) or not child_steps:
            errors.append("foreach step requires a non-empty 'steps' list")
        else:
            for child in child_steps:
                if isinstance(child, dict) and child.get("type") == "foreach":
                    errors.append("nested foreach steps are not supported")
        return errors

    def execute(self, step: Step, context: dict, run: RunContext) -> StepOutput:
        try:
            items = _resolve_items(step.config["items"], run)
        except Exception as exc:
            return StepOutput(error=f"foreach: cannot resolve items: {exc}")
        if not isinstance(items, list):
            return StepOutput(error=f"foreach: 'items' resolved to {type(items).__name__}, expected a list")

        try:
            child_steps = [Step.from_dict(s) for s in step.config["steps"]]
        except WorkflowValidationError as exc:
            return StepOutput(error=f"foreach: invalid child step: {'; '.join(exc.errors)}")

        from flowforge.engine import WorkflowEngine  # lazy import avoids a cycle

        engine = getattr(run, "engine", None) or WorkflowEngine()
        parallel = bool(step.config.get("parallel", False))
        run.log(f"foreach: {len(items)} iteration(s), parallel={parallel}")
        started = time.time()

        def run_iteration(index_item):
            index, item = index_item
            iteration_ctx = {
                "variables": context.get("variables", {}),
                "steps": {},
                "this": {"item": item, "index": index},
            }
            iteration_run = run.fork(iteration_ctx)
            results, ok = engine._run_steps(child_steps, iteration_ctx, iteration_run, prefix=f"{step.id}.")
            entry = {"item": item, "index": index}
            entry.update({sid: res.to_dict() for sid, res in results.items()})
            return entry, ok

        if parallel:
            with ThreadPoolExecutor(max_workers=max(1, min(len(items), 8))) as pool:
                entries = list(pool.map(run_iteration, enumerate(items)))
        else:
            entries = [run_iteration(idx_item) for idx_item in enumerate(items)]

        failed = sum(0 if ok else 1 for _, ok in entries)
        elapsed_ms = (time.time() - started) * 1000
        output = {
            "count": len(items),
            "items": items,
            "failed_iterations": failed,
            "results": [entry for entry, _ in entries],
            "elapsed_ms": round(elapsed_ms, 1),
        }
        if failed:
            return StepOutput(output=output, error=f"{failed} of {len(items)} foreach iterations failed")
        return StepOutput(output=output)