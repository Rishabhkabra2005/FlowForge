"""Condition step: evaluate an expression and record the outcome as data.

Real branching happens via the per-step `condition` field (steps whose
condition is false are skipped). This step exists so decisions are also
visible as first-class, auditable steps in the run record.
"""
from __future__ import annotations

from flowforge.expressions import evaluate_condition
from flowforge.registry import step
from flowforge.steps.base import RunContext, StepExecutor, StepOutput


@step("condition")
class ConditionExecutor(StepExecutor):
    """Evaluate an expression. Config: expression (string). Output: {result: bool}."""

    def validate_config(self, config: dict) -> list[str]:
        return [] if config.get("expression") else ["condition step requires 'expression'"]

    def execute(self, step, context: dict, run: RunContext) -> StepOutput:
        expression = run.interpolate(step.config["expression"])
        run.log(f"condition: {expression}")
        try:
            result = evaluate_condition(expression, context)
        except Exception as exc:  # surface eval problems as step failure
            return StepOutput(output={"expression": expression}, error=str(exc))
        return StepOutput(
            output={"expression": expression, "result": result},
            stdout=f"condition evaluated to {str(result).lower()}",
        )