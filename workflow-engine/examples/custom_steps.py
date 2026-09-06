"""Custom step types - proof that FlowForge is extensible.

Load them into any command with --plugin examples/custom_steps.py

    python -m flowforge run examples/custom_workflow.json --plugin examples/custom_steps.py

A custom step is just a class with validate_config() and execute().
That is the whole plugin contract. No framework hooks, no inheritance
games - one file, one decorator.
"""
from flowforge.registry import step
from flowforge.steps.base import StepExecutor, StepOutput


@step("shout")
class ShoutStep(StepExecutor):
    """Uppercase a message (toy step - shows the plugin pattern)."""

    def validate_config(self, config: dict) -> list[str]:
        return [] if config.get("message") else ["shout step requires a 'message'"]

    def execute(self, step, context, run) -> StepOutput:
        message = run.interpolate(step.config["message"])
        run.log(f"shouting: {message}")
        return StepOutput(output={"message": message.upper(), "length": len(message)})


@step("summarize_json")
class SummarizeJsonStep(StepExecutor):
    """Load a JSON file and expose its size and key count."""

    def validate_config(self, config: dict) -> list[str]:
        return [] if config.get("path") else ["summarize_json step requires a 'path'"]

    def execute(self, step, context, run) -> StepOutput:
        import json
        import os

        path = run.interpolate(step.config["path"])
        try:
            with open(path, "r", encoding="utf-8") as handle:
                data = json.load(handle)
        except OSError as exc:
            return StepOutput(error=f"cannot read {path}: {exc}")
        run.log(f"summarized {path}")
        return StepOutput(
            output={
                "path": path,
                "size_bytes": os.path.getsize(path),
                "top_level_keys": list(data) if isinstance(data, dict) else len(data),
            }
        )