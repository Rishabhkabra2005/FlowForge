"""Python step: run inline Python code against the workflow context.

The code runs with `steps`, `variables`, and `run` in scope. Set `result`
to a JSON-serializable value to expose it as steps.<id>.output.
"""
from __future__ import annotations

import time
import traceback

from flowforge.registry import step
from flowforge.steps.base import RunContext, StepExecutor, StepOutput


@step("python")
class PythonExecutor(StepExecutor):
    """Run inline Python. Config: code (string), optional imports (list of module names)."""

    def validate_config(self, config: dict) -> list[str]:
        errors = []
        if not isinstance(config.get("code"), str) or not config["code"].strip():
            errors.append("python step requires non-empty 'code'")
        return errors

    def execute(self, step, context: dict, run: RunContext) -> StepOutput:
        code = run.interpolate(step.config["code"])
        imports = run.interpolate(step.config.get("imports") or [])

        namespace: dict = {
            "steps": context.get("steps", {}),
            "variables": context.get("variables", {}),
            "this": context.get("this"),
            "run": run,
            "result": None,
            "__name__": "flowforge_python_step",
        }
        for module in imports:
            namespace.setdefault(module.split(".")[0], __import__(module))

        run.log("python step started")
        started = time.time()
        try:
            exec(compile(code, f"<step:{step.id}>", "exec"), namespace)  # noqa: S102 - by design
        except Exception as exc:  # any user-code error fails the step
            traceback.print_exc()
            return StepOutput(
                output={},
                error=f"python step raised {type(exc).__name__}: {exc}",
                stderr=traceback.format_exc(),
            )
        elapsed_ms = (time.time() - started) * 1000
        return StepOutput(
            output={"result": namespace.get("result"), "elapsed_ms": round(elapsed_ms, 1)},
            stdout=str(namespace.get("result")),
        )