"""Schedules and executes workflow steps.

How it works:
- steps form a dependency graph via depends_on; steps whose dependencies are
  all done run together in one batch (ThreadPoolExecutor, max_workers)
- a per-step `condition` that evaluates to false skips the step (recorded)
- a retry policy retries failures with exponential backoff
- run policy: on_success (default) / on_failure / always
- every config string supports {{ path }} interpolation against the run
  context (variables.<name>, steps.<id>.output.<key>, ...)
- composite steps (foreach) reuse _run_steps, so nested workflows get the
  exact same scheduling, retries and conditions
"""
from __future__ import annotations

import time
from concurrent.futures import ThreadPoolExecutor
from typing import Callable, Optional

from flowforge.expressions import ConditionError, evaluate_condition
from flowforge.models import (
    RUN_POLICIES,
    RunResult,
    RunStatus,
    Step,
    StepResult,
    StepStatus,
    Workflow,
    WorkflowValidationError,
    _iso,
    new_run_id,
)
from flowforge.registry import StepRegistry, default_registry
from flowforge.steps.base import RunContext


class WorkflowEngine:
    def __init__(self, registry: Optional[StepRegistry] = None, max_workers: int = 8):
        self.registry = registry or default_registry
        self.max_workers = max_workers

    # ---- validation ---------------------------------------------------

    def validate(self, workflow: Workflow) -> list[str]:
        errors: list[str] = []

        ids = [s.id for s in workflow.steps]
        seen = set()
        for sid in ids:
            if sid in seen:
                errors.append(f"duplicate step id: '{sid}'")
            seen.add(sid)

        for st in workflow.steps:
            if st.run not in RUN_POLICIES:
                errors.append(f"step '{st.id}': invalid run policy '{st.run}' (use {', '.join(RUN_POLICIES)})")
            executor_cls = self.registry.get(st.type)
            if executor_cls is None:
                errors.append(f"step '{st.id}': unknown type '{st.type}' (registered: {', '.join(self.registry.names())})")
                continue
            for msg in executor_cls().validate_config(st.config):
                errors.append(f"step '{st.id}': {msg}")
            # foreach children are steps too, validate them recursively
            if st.type == "foreach":
                for child in st.config.get("steps") or []:
                    try:
                        child_step = Step.from_dict(child)
                    except (KeyError, WorkflowValidationError) as exc:
                        errors.append(f"step '{st.id}': invalid foreach child: {exc}")
                        continue
                    for msg in self._validate_single(child_step, prefix=f"{st.id}."):
                        errors.append(msg)

        known = set(ids)
        for st in workflow.steps:
            for dep in st.depends_on:
                if dep not in known:
                    errors.append(f"step '{st.id}' depends on unknown step '{dep}'")

        try:
            self._topo_sort(workflow.steps)
        except WorkflowValidationError as exc:
            errors.extend(exc.errors)
        return errors

    def _validate_single(self, st: Step, prefix: str = "") -> list[str]:
        errors: list[str] = []
        if st.run not in RUN_POLICIES:
            errors.append(f"step '{prefix}{st.id}': invalid run policy '{st.run}'")
        executor_cls = self.registry.get(st.type)
        if executor_cls is None:
            errors.append(f"step '{prefix}{st.id}': unknown type '{st.type}'")
        else:
            for msg in executor_cls().validate_config(st.config):
                errors.append(f"step '{prefix}{st.id}': {msg}")
        return errors

    @staticmethod
    def _topo_sort(steps: list) -> list:
        by_id = {s.id: s for s in steps}
        order: list = []
        state: dict[str, int] = {}  # 0 = visiting, 1 = done

        def visit(sid: str) -> None:
            if state.get(sid) == 1:
                return
            if state.get(sid) == 0:
                raise WorkflowValidationError([f"circular dependency detected involving step '{sid}'"])
            state[sid] = 0
            for dep in by_id[sid].depends_on:
                if dep in by_id:
                    visit(dep)
            state[sid] = 1
            order.append(by_id[sid])

        for sid in by_id:
            visit(sid)
        return order

    # ---- execution ----------------------------------------------------

    def execute(
        self,
        workflow: Workflow,
        run_id: Optional[str] = None,
        log_fn: Optional[Callable] = None,
        on_complete: Optional[Callable] = None,
        on_step: Optional[Callable] = None,
        max_workers: Optional[int] = None,
    ) -> RunResult:
        run_id = run_id or new_run_id()
        started = time.time()
        result = RunResult(
            run_id=run_id,
            workflow_name=workflow.name,
            workflow_version=workflow.version,
            started_at=started,
        )

        def default_log(message: str, level: str = "info", step_id: Optional[str] = None) -> None:
            result.logs.append({"ts": _iso(time.time()), "level": level, "step_id": step_id, "message": message})

        log = log_fn or default_log
        log(f"run {run_id} started: '{workflow.name}' v{workflow.version}")
        if workflow.variables:
            log(f"variables: {list(workflow.variables)}")

        context = {"variables": workflow.variables, "steps": {}}
        ctx = RunContext(run_id, context, log)
        ctx.engine = self
        ctx.deadline = started + workflow.timeout if workflow.timeout else None
        # on_step fires after every step completes; callers pass the RunResult
        # and mutate it in place for live updates.
        ctx.on_step = (lambda: on_step(result)) if on_step else None

        workers = max_workers or workflow.max_workers or self.max_workers
        steps_results, ok = self._run_steps(workflow.steps, context, ctx, max_workers=workers)

        result.steps = steps_results
        result.status = RunStatus.SUCCESS if ok else RunStatus.FAILED
        result.finished_at = time.time()
        result.duration_ms = (result.finished_at - started) * 1000
        log(f"run finished: {result.status.value} in {result.duration_ms:.0f}ms")
        if on_complete:
            on_complete(result)
        return result

    def _run_steps(
        self,
        steps: list,
        context: dict,
        ctx: RunContext,
        max_workers: Optional[int] = None,
        prefix: str = "",
    ) -> tuple[dict, bool]:
        """Run a set of steps to completion. Returns (results_by_id, all_ok)."""
        by_id = {s.id: s for s in steps}
        results: dict[str, StepResult] = {}
        done: dict[str, StepResult] = {}
        pending = set(by_id)
        failed = False
        workers = max_workers or self.max_workers

        with ThreadPoolExecutor(max_workers=workers) as pool:
            while pending:
                if ctx.deadline is not None and time.time() > ctx.deadline:
                    for sid in list(pending):
                        results[sid] = StepResult(sid, status=StepStatus.SKIPPED, reason="workflow timeout exceeded")
                        ctx.log(f"step '{prefix}{sid}' skipped: workflow timeout exceeded", step_id=f"{prefix}{sid}")
                    break

                ready, skipped = self._compute_ready(by_id, done, pending)
                for sid, reason in skipped.items():
                    results[sid] = StepResult(sid, status=StepStatus.SKIPPED, reason=reason)
                    ctx.log(f"step '{prefix}{sid}' skipped: {reason}", level="warn", step_id=f"{prefix}{sid}")
                    pending.discard(sid)

                if not ready:
                    # nothing left to do but nothing succeeded either
                    for sid in list(pending):
                        results[sid] = StepResult(sid, status=StepStatus.SKIPPED, reason="dependencies never satisfied")
                        ctx.log(f"step '{prefix}{sid}' skipped: dependencies never satisfied", level="warn", step_id=f"{prefix}{sid}")
                    break

                futures = [pool.submit(self._execute_one, by_id[sid], context, ctx, prefix) for sid in ready]
                for future in futures:
                    step_result = future.result()
                    results[step_result.step_id] = step_result
                    done[step_result.step_id] = step_result
                    pending.discard(step_result.step_id)
                    if step_result.status == StepStatus.FAILED:
                        failed = True
                    if ctx.on_step:
                        ctx.on_step()

        return results, not failed

    def _compute_ready(self, by_id: dict, done: dict, pending: set) -> tuple[list, dict]:
        ready: list[str] = []
        skipped: dict[str, str] = {}
        for sid in pending:
            st = by_id[sid]
            if any(dep not in done for dep in st.depends_on):
                continue  # a dependency is still running
            dep_results = [done[dep] for dep in st.depends_on]
            if st.run == "always":
                ready.append(sid)
                continue
            if st.run == "on_failure":
                if any(r.status == StepStatus.FAILED for r in dep_results):
                    ready.append(sid)
                else:
                    skipped[sid] = "no dependency failed"
                continue
            # on_success (default)
            if all(r.status == StepStatus.SUCCESS for r in dep_results):
                ready.append(sid)
            else:
                bad = [dep for dep, r in zip(st.depends_on, dep_results) if r.status != StepStatus.SUCCESS]
                skipped[sid] = f"dependency not successful: {', '.join(bad)}"
        return ready, skipped

    def _execute_one(self, st: Step, context: dict, ctx: RunContext, prefix: str = "") -> StepResult:
        full_id = f"{prefix}{st.id}"
        result = StepResult(step_id=st.id, status=StepStatus.RUNNING, started_at=time.time())

        if st.condition:
            try:
                passes = evaluate_condition(st.condition, context)
            except ConditionError as exc:
                result.status = StepStatus.FAILED
                result.error = f"condition error: {exc}"
                result.finished_at = time.time()
                result.duration_ms = (result.finished_at - result.started_at) * 1000
                ctx.log(f"step '{full_id}' failed: {result.error}", level="error", step_id=full_id)
                return result
            if not passes:
                result.status = StepStatus.SKIPPED
                result.reason = f"condition false: {st.condition}"
                result.finished_at = time.time()
                result.duration_ms = 0.0
                ctx.log(f"step '{full_id}' skipped: condition false ({st.condition})", level="warn", step_id=full_id)
                return result

        executor_cls = self.registry.get(st.type)
        if executor_cls is None:  # shouldn't happen, validation catches it
            result.status = StepStatus.FAILED
            result.error = f"unknown step type '{st.type}'"
            result.finished_at = time.time()
            return result

        executor = executor_cls()
        ctx.log(f"step '{full_id}' started (type={st.type})", step_id=full_id)
        attempt = 0
        while True:
            attempt += 1
            result.attempts = attempt
            attempt_started = time.time()
            try:
                out = executor.execute(st, context, ctx)
                elapsed = (time.time() - attempt_started) * 1000
                result.output = out.output
                result.stdout = out.stdout
                result.stderr = out.stderr
                if out.ok:
                    result.status = StepStatus.SUCCESS
                    result.error = None
                    result.finished_at = time.time()
                    result.duration_ms = elapsed
                    ctx.log(f"step '{full_id}' succeeded in {elapsed:.0f}ms (attempt {attempt})", step_id=full_id)
                    break
                result.error = out.error
                ctx.log(f"step '{full_id}' attempt {attempt} failed: {out.error}", level="error", step_id=full_id)
            except Exception as exc:  # executor bug: treat as step failure
                result.error = f"{type(exc).__name__}: {exc}"
                result.finished_at = time.time()
                result.duration_ms = (result.finished_at - attempt_started) * 1000
                ctx.log(f"step '{full_id}' crashed: {result.error}", level="error", step_id=full_id)

            if attempt < st.retry.max_attempts:
                backoff = st.retry.next_backoff(attempt + 1)
                ctx.log(f"step '{full_id}' retrying in {backoff:.1f}s (attempt {attempt + 1} of {st.retry.max_attempts})", step_id=full_id)
                time.sleep(backoff)
                continue
            result.status = StepStatus.FAILED
            result.finished_at = time.time()
            result.duration_ms = (result.finished_at - result.started_at) * 1000
            ctx.log(f"step '{full_id}' failed after {attempt} attempt(s)", level="error", step_id=full_id)
            break

        # publish the result so later steps can read steps.<id>.*
        context["steps"][st.id] = result.to_dict()
        return result