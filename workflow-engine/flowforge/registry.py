"""Step registry - the extension point of the engine.

Registering a new step type is intentionally small:

    from flowforge.registry import step
    from flowforge.steps.base import StepExecutor, StepOutput

    @step("slack")
    class SlackStep(StepExecutor):
        def validate_config(self, config):
            return [] if "message" in config else ["slack step requires 'message'"]
        def execute(self, step, context, run):
            msg = run.interpolate(step.config["message"])
            return StepOutput(output={"sent": msg})

Everything registered lands in the default registry, which is what the
engine and the server use.
"""
from __future__ import annotations

from typing import TYPE_CHECKING, Optional, Type

if TYPE_CHECKING:  # steps package imports this at load time - avoid a cycle
    from flowforge.steps.base import StepExecutor


class StepRegistry:
    def __init__(self) -> None:
        self._executors: dict[str, Type[StepExecutor]] = {}

    def register(self, name: str, executor_cls: Type[StepExecutor]) -> Type[StepExecutor]:
        self._executors[name] = executor_cls
        return executor_cls

    def get(self, name: str) -> Optional[Type[StepExecutor]]:
        return self._executors.get(name)

    def names(self) -> list[str]:
        return sorted(self._executors)

    def to_dict(self) -> dict:
        out = {}
        for name, cls in self._executors.items():
            out[name] = (cls.__doc__ or "").strip().splitlines()[0] if cls.__doc__ else ""
        return out


default_registry = StepRegistry()


def step(name: Optional[str] = None):
    """Decorator: register a StepExecutor subclass under a step type name."""

    def decorator(cls: Type[StepExecutor]) -> Type[StepExecutor]:
        default_registry.register(name or cls.__name__.lower(), cls)
        return cls

    return decorator