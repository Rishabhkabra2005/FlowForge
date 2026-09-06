"""FlowForge - a JSON-driven workflow execution engine.

Define workflows as data (JSON), execute them reliably: shell scripts,
REST API calls, Python snippets, conditionals, loops, parallelism and
automatic retries. The engine core is 100% Python standard library.
"""

__version__ = "1.0.0"

from flowforge.models import (
    RunResult,
    RunStatus,
    Step,
    StepResult,
    StepStatus,
    Workflow,
    WorkflowValidationError,
)
from flowforge.registry import StepRegistry, default_registry, step
from flowforge.steps.base import StepExecutor, StepOutput
from flowforge.engine import WorkflowEngine
from flowforge.expressions import ConditionError, InterpolationError, evaluate_condition, interpolate, resolve_path

__all__ = [
    "__version__",
    "RunResult",
    "RunStatus",
    "Step",
    "StepResult",
    "StepStatus",
    "Workflow",
    "WorkflowValidationError",
    "StepRegistry",
    "default_registry",
    "step",
    "StepExecutor",
    "StepOutput",
    "WorkflowEngine",
    "ConditionError",
    "InterpolationError",
    "evaluate_condition",
    "interpolate",
    "resolve_path",
]