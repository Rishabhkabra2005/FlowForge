"""Run history: kept in memory, and every run is also written to disk as a
JSON artifact under runs/ so each execution stays auditable."""
from __future__ import annotations

import json
import os
import threading
from typing import Optional

from flowforge.models import RunResult, RunStatus, StepResult, StepStatus


class RunStore:
    def __init__(self, runs_dir: str = "runs") -> None:
        self.runs_dir = runs_dir
        self._runs: dict[str, RunResult] = {}
        self._lock = threading.Lock()
        os.makedirs(runs_dir, exist_ok=True)
        self._load_existing()

    def _load_existing(self) -> None:
        for filename in sorted(os.listdir(self.runs_dir)):
            if not filename.endswith(".json"):
                continue
            path = os.path.join(self.runs_dir, filename)
            try:
                with open(path, "r", encoding="utf-8") as handle:
                    data = json.load(handle)
                result = RunResult(
                    run_id=data["run_id"],
                    workflow_name=data["workflow_name"],
                    workflow_version=data.get("workflow_version", "1.0.0"),
                    status=RunStatus(data["status"]),
                    steps={
                        sid: StepResult(
                            step_id=s.get("step_id", sid),
                            status=StepStatus(s["status"]),
                            output=s.get("output") or {},
                            stdout=s.get("stdout") or "",
                            stderr=s.get("stderr") or "",
                            error=s.get("error"),
                            attempts=s.get("attempts", 1),
                            reason=s.get("reason"),
                        )
                        for sid, s in data.get("steps", {}).items()
                    },
                    logs=data.get("logs") or [],
                )
                self._runs[result.run_id] = result
            except Exception:  # a corrupt artifact should not kill the server
                continue

    def save(self, result: RunResult) -> None:
        with self._lock:
            self._runs[result.run_id] = result
            path = os.path.join(self.runs_dir, f"{result.run_id}.json")
            with open(path, "w", encoding="utf-8") as handle:
                json.dump(result.to_dict(), handle, indent=2)

    def get(self, run_id: str) -> Optional[RunResult]:
        with self._lock:
            return self._runs.get(run_id)

    def list(self, limit: int = 50) -> list[dict]:
        with self._lock:
            ordered = sorted(self._runs.values(), key=lambda r: r.started_at or 0, reverse=True)
            return [r.to_dict() for r in ordered[:limit]]