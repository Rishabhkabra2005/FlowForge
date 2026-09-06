"""Data models for the workflow engine. Plain dataclasses, no dependencies."""
from __future__ import annotations

import time
import uuid
from dataclasses import dataclass, field
from enum import Enum
from typing import Any, Optional


class StepStatus(str, Enum):
    PENDING = "pending"
    RUNNING = "running"
    SUCCESS = "success"
    FAILED = "failed"
    SKIPPED = "skipped"


class RunStatus(str, Enum):
    RUNNING = "running"
    SUCCESS = "success"
    FAILED = "failed"


RUN_POLICIES = ("on_success", "on_failure", "always")


class WorkflowValidationError(Exception):
    """Invalid workflow definition. Carries one message per problem."""

    def __init__(self, errors: list[str]):
        self.errors = errors
        super().__init__("; ".join(errors))


@dataclass
class RetryPolicy:
    max_attempts: int = 1
    backoff_seconds: float = 1.0
    backoff_factor: float = 2.0
    max_backoff_seconds: float = 60.0

    @classmethod
    def from_dict(cls, data: Optional[dict]) -> "RetryPolicy":
        data = data or {}
        return cls(
            max_attempts=max(1, int(data.get("max_attempts", 1))),
            backoff_seconds=max(0.0, float(data.get("backoff_seconds", 1.0))),
            backoff_factor=max(1.0, float(data.get("backoff_factor", 2.0))),
            max_backoff_seconds=max(0.0, float(data.get("max_backoff_seconds", 60.0))),
        )

    def next_backoff(self, next_attempt: int) -> float:
        # delay before the given (1-based) attempt: base * factor^(n-1), capped
        backoff = self.backoff_seconds * (self.backoff_factor ** (next_attempt - 1))
        return min(backoff, self.max_backoff_seconds)

    def to_dict(self) -> dict:
        return {
            "max_attempts": self.max_attempts,
            "backoff_seconds": self.backoff_seconds,
            "backoff_factor": self.backoff_factor,
            "max_backoff_seconds": self.max_backoff_seconds,
        }


@dataclass
class Step:
    """One step in a workflow."""

    id: str
    type: str
    name: str = ""
    config: dict = field(default_factory=dict)
    depends_on: list = field(default_factory=list)
    run: str = "on_success"  # on_success | on_failure | always
    condition: Optional[str] = None
    retry: RetryPolicy = field(default_factory=RetryPolicy)
    timeout: Optional[float] = None
    env: dict = field(default_factory=dict)

    _ENGINE_KEYS = {"id", "type", "name", "depends_on", "run", "condition", "retry", "timeout", "env", "config", "params"}

    @classmethod
    def from_dict(cls, data: dict) -> "Step":
        # step config can be nested under "config"/"params" or flattened at the
        # top level (command, url, method, code, ...). Accept both.
        config = dict(data.get("config") or data.get("params") or {})
        if not config:
            config = {k: v for k, v in data.items() if k not in cls._ENGINE_KEYS}
        return cls(
            id=str(data["id"]),
            type=str(data["type"]),
            name=str(data.get("name") or data["id"]),
            config=config,
            depends_on=list(data.get("depends_on") or []),
            run=str(data.get("run", "on_success")),
            condition=data.get("condition"),
            retry=RetryPolicy.from_dict(data.get("retry")),
            timeout=data.get("timeout"),
            env=dict(data.get("env") or {}),
        )


@dataclass
class StepResult:
    step_id: str
    status: StepStatus = StepStatus.PENDING
    output: dict = field(default_factory=dict)
    stdout: str = ""
    stderr: str = ""
    error: Optional[str] = None
    attempts: int = 0
    started_at: Optional[float] = None
    finished_at: Optional[float] = None
    duration_ms: Optional[float] = None
    reason: Optional[str] = None

    def to_dict(self) -> dict:
        return {
            "step_id": self.step_id,
            "status": self.status.value,
            "output": self.output,
            "stdout": self.stdout,
            "stderr": self.stderr,
            "error": self.error,
            "attempts": self.attempts,
            "started_at": _iso(self.started_at),
            "finished_at": _iso(self.finished_at),
            "duration_ms": round(self.duration_ms, 1) if self.duration_ms is not None else None,
            "reason": self.reason,
        }


@dataclass
class Workflow:
    name: str
    steps: list
    description: str = ""
    version: str = "1.0.0"
    variables: dict = field(default_factory=dict)
    timeout: Optional[float] = None
    max_workers: int = 8

    @classmethod
    def from_dict(cls, data: dict) -> "Workflow":
        required = ("name", "steps")
        missing = [k for k in required if k not in data]
        if missing:
            raise WorkflowValidationError([f"missing required top-level key: {k}" for k in missing])
        if not isinstance(data["steps"], list) or not data["steps"]:
            raise WorkflowValidationError(["'steps' must be a non-empty list"])
        try:
            steps = [Step.from_dict(s) for s in data["steps"]]
        except KeyError as exc:
            raise WorkflowValidationError([f"step is missing required key: {exc}"]) from exc
        return cls(
            name=str(data["name"]),
            description=str(data.get("description", "")),
            version=str(data.get("version", "1.0.0")),
            variables=dict(data.get("variables") or {}),
            timeout=data.get("timeout"),
            max_workers=int(data.get("max_workers", 8)),
            steps=steps,
        )

    def to_dict(self) -> dict:
        return {
            "name": self.name,
            "description": self.description,
            "version": self.version,
            "variables": self.variables,
            "timeout": self.timeout,
            "max_workers": self.max_workers,
            "steps": [
                {
                    "id": s.id,
                    "type": s.type,
                    "name": s.name,
                    "config": s.config,
                    "depends_on": s.depends_on,
                    "run": s.run,
                    "condition": s.condition,
                    "retry": s.retry.to_dict(),
                    "timeout": s.timeout,
                    "env": s.env,
                }
                for s in self.steps
            ],
        }


@dataclass
class RunResult:
    run_id: str
    workflow_name: str
    workflow_version: str = "1.0.0"
    status: RunStatus = RunStatus.RUNNING
    steps: dict = field(default_factory=dict)
    logs: list = field(default_factory=list)
    started_at: Optional[float] = None
    finished_at: Optional[float] = None
    duration_ms: Optional[float] = None

    def to_dict(self) -> dict:
        return {
            "run_id": self.run_id,
            "workflow_name": self.workflow_name,
            "workflow_version": self.workflow_version,
            "status": self.status.value if isinstance(self.status, RunStatus) else str(self.status),
            "steps": {sid: (r.to_dict() if hasattr(r, "to_dict") else r) for sid, r in self.steps.items()},
            "logs": self.logs,
            "started_at": _iso(self.started_at),
            "finished_at": _iso(self.finished_at),
            "duration_ms": round(self.duration_ms, 1) if self.duration_ms is not None else None,
        }


def _iso(ts: Optional[float]) -> Optional[str]:
    if ts is None:
        return None
    return time.strftime("%Y-%m-%dT%H:%M:%S", time.localtime(ts)) + f".{int((ts % 1) * 1000):03d}Z"


def new_run_id() -> str:
    return uuid.uuid4().hex[:12]