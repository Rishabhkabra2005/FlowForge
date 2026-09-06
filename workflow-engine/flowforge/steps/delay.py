"""Delay step: sleep for a number of seconds (handy for pacing pipelines)."""
from __future__ import annotations

import time

from flowforge.registry import step
from flowforge.steps.base import RunContext, StepExecutor, StepOutput


@step("delay")
class DelayExecutor(StepExecutor):
    """Wait N seconds. Config: seconds (number or expression)."""

    def validate_config(self, config: dict) -> list[str]:
        return [] if "seconds" in config else ["delay step requires 'seconds'"]

    def execute(self, step, context: dict, run: RunContext) -> StepOutput:
        seconds = float(run.interpolate(step.config["seconds"]))
        run.log(f"delaying {seconds}s")
        time.sleep(max(0.0, seconds))
        return StepOutput(output={"slept_seconds": seconds})