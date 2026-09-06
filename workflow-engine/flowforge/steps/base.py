"""Base classes for step executors."""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Optional

from flowforge.models import Step


@dataclass
class StepOutput:
    """What a step executor returns.

    - output: JSON-serializable dict exposed as steps.<id>.output.<key>
    - error:  when set the step is considered failed (drives retries / on_failure)
    - stdout / stderr / exit_code: surfaced on the result for logging and conditions
    """

    output: dict = field(default_factory=dict)
    stdout: str = ""
    stderr: str = ""
    exit_code: Optional[int] = None
    error: Optional[str] = None

    @property
    def ok(self) -> bool:
        return self.error is None


class RunContext:
    """Handed to every executor: logging, interpolation, variable access."""

    def __init__(self, run_id: str, context: dict, log_fn) -> None:
        self.run_id = run_id
        self.context = context
        self._log_fn = log_fn

    def log(self, message: str, level: str = "info", step_id: Optional[str] = None) -> None:
        self._log_fn(message, level=level, step_id=step_id)

    def interpolate(self, value: Any) -> Any:
        from flowforge.expressions import interpolate

        return interpolate(value, self.context)

    def resolve(self, path: str) -> Any:
        from flowforge.expressions import resolve_path

        return resolve_path(self.context, path)

    def fork(self, context: dict) -> "RunContext":
        """Child RunContext bound to a different (e.g. iteration) context."""
        child = RunContext(self.run_id, context, self._log_fn)
        child.engine = getattr(self, "engine", None)
        child.deadline = getattr(self, "deadline", None)
        child.on_step = getattr(self, "on_step", None)
        return child


class StepExecutor:
    """Base class for all step types.

    Subclasses implement:
      - validate_config(config) -> list of error strings (optional)
      - execute(step, context, run) -> StepOutput

    `run` is a RunContext with .log(), .interpolate() and .resolve().
    """

    step_type: str = "base"

    def validate_config(self, config: dict) -> list[str]:  # noqa: B027
        return []

    def execute(self, step: Step, context: dict, run: RunContext) -> StepOutput:
        raise NotImplementedError

    def __repr__(self) -> str:  # pragma: no cover
        return f"<StepExecutor type={self.step_type}>"