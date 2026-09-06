"""FlowForge REST API + web dashboard.

Endpoints:
    POST /api/workflows/validate   validate a workflow JSON payload
    POST /api/runs                 submit a workflow for execution -> {run_id}
    GET  /api/runs/{run_id}        live run status, steps and logs
    GET  /api/runs                 run history
    GET  /api/steps                registered step types
    GET  /api/health               liveness
    GET  /                         dashboard
"""
from __future__ import annotations

import os
import threading
import time
import uvicorn
from fastapi import FastAPI, HTTPException
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, ConfigDict

from flowforge import __version__
from flowforge.engine import WorkflowEngine
from flowforge.models import RunStatus, Workflow, WorkflowValidationError
from flowforge.registry import default_registry
from flowforge.store import RunStore

app = FastAPI(title="FlowForge", version=__version__)
engine = WorkflowEngine()
store = RunStore(runs_dir=os.environ.get("FLOWFORGE_RUNS_DIR", "runs"))

STATIC_DIR = os.path.join(os.path.dirname(__file__), "static")


class WorkflowPayload(BaseModel):
    """Accepts either {"definition": {...}} or the workflow JSON itself."""

    model_config = ConfigDict(extra="allow")
    definition: dict | None = None

    def resolved(self) -> dict:
        if self.definition is not None:
            return self.definition
        return {k: v for k, v in self.model_dump().items() if v is not None}


def _validate_definition(definition: dict) -> tuple[Workflow, list[str]]:
    try:
        workflow = Workflow.from_dict(definition)
    except WorkflowValidationError as exc:
        return None, exc.errors
    errors = engine.validate(workflow)
    return workflow, errors


@app.get("/api/health")
def health() -> dict:
    return {"status": "ok", "version": __version__, "steps": len(default_registry.names())}


@app.get("/api/steps")
def list_steps() -> dict:
    return {"steps": default_registry.to_dict()}


@app.post("/api/workflows/validate")
def validate_workflow(payload: WorkflowPayload) -> dict:
    workflow, errors = _validate_definition(payload.resolved())
    if errors:
        return {"valid": False, "errors": errors}
    return {"valid": True, "errors": [], "workflow": workflow.to_dict()}


@app.post("/api/runs", status_code=201)
def submit_run(payload: WorkflowPayload) -> dict:
    workflow, errors = _validate_definition(payload.resolved())
    if errors:
        raise HTTPException(status_code=422, detail={"valid": False, "errors": errors})
    run_id = _run_in_background(workflow)
    return {"run_id": run_id, "status": "running", "workflow_name": workflow.name}


def _run_in_background(workflow: Workflow) -> str:
    from flowforge.models import RunResult, RunStatus, new_run_id

    run_id = new_run_id()
    # placeholder so the run is visible immediately; replaced on the first step event
    store.save(
        RunResult(
            run_id=run_id,
            workflow_name=workflow.name,
            workflow_version=workflow.version,
            status=RunStatus.RUNNING,
            started_at=time.time(),
        )
    )

    def run() -> None:
        engine.execute(
            workflow,
            run_id=run_id,
            on_step=store.save,
            on_complete=store.save,
        )

    threading.Thread(target=run, daemon=True).start()
    return run_id


@app.get("/api/runs")
def list_runs(limit: int = 30) -> dict:
    return {"runs": store.list(limit=limit)}


@app.get("/api/runs/{run_id}")
def get_run(run_id: str) -> dict:
    result = store.get(run_id)
    if result is None:
        raise HTTPException(status_code=404, detail=f"unknown run_id '{run_id}'")
    return result.to_dict()


app.mount("/static", StaticFiles(directory=STATIC_DIR), name="static")


@app.get("/", include_in_schema=False)
def dashboard() -> FileResponse:
    return FileResponse(os.path.join(STATIC_DIR, "index.html"))


def start_server(host: str = "127.0.0.1", port: int = 8000) -> None:
    print(f"FlowForge v{__version__} server: http://{host}:{port}")
    print(f"  dashboard:    http://{host}:{port}/")
    print(f"  api docs:     http://{host}:{port}/docs")
    print(f"  registered step types: {', '.join(default_registry.names())}")
    uvicorn.run(app, host=host, port=port)


if __name__ == "__main__":
    start_server()