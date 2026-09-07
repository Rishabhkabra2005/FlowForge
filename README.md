# FlowForge — JSON-Driven Workflow Execution Engine

Define workflows as data (a single JSON document) and execute them reliably:
shell scripts, REST API calls, Python snippets, conditionals, loops,
parallelism and automatic retries. Built for the Nutanix Hackathon 2026
(Project Area 4: Workflow Execution Engine).

The engine core is 100% Python standard library — no frameworks. FastAPI and
uvicorn power only the optional REST API and web dashboard.

---

## Quickstart

```bash
# 1. create the environment (once)
python -m venv .venv
.venv/Scripts/python -m pip install -r requirements.txt     # Windows Git Bash
# or:  .venv/bin/pip install -r requirements.txt            # macOS / Linux

# 2. start the mock external API (simulates the systems you integrate with)
.venv/Scripts/python examples/mock_api.py --port 8001 --fail-rate 0.3

# 3. in a second terminal - validate and run a workflow
.venv/Scripts/python -m flowforge validate examples/deploy_workflow.json
.venv/Scripts/python -m flowforge run     examples/deploy_workflow.json

# 4. start the REST API + live web dashboard
.venv/Scripts/python -m flowforge serve --port 8000
#    open http://127.0.0.1:8000/   -> dashboard
#    open http://127.0.0.1:8000/docs -> interactive API docs
```

Run the test suite:

```bash
.venv/Scripts/python -m unittest discover -s tests -v
```

---

## What it does

A workflow is one JSON document describing an ordered set of steps. The
engine reads it, builds a dependency graph, and runs steps as fast as the
dependencies allow — with built-in resilience.

```json
{
  "name": "CI-CD Deploy Pipeline",
  "variables": { "environment": "staging", "app": "flowforge-demo" },
  "steps": [
    { "id": "checkout", "type": "shell", "command": "git pull && echo ok" },
    { "id": "unit_tests", "type": "shell", "command": "pytest", "depends_on": ["checkout"] },
    { "id": "deploy", "type": "http", "method": "POST",
      "url": "http://127.0.0.1:8001/api/deploy",
      "body": { "environment": "{{ variables.environment }}",
                "version": "{{ steps.checkout.output.stdout }}" },
      "depends_on": ["unit_tests"],
      "retry": { "max_attempts": 3, "backoff_seconds": 1, "backoff_factor": 2 } },
    { "id": "notify", "type": "http", "method": "POST",
      "url": "http://127.0.0.1:8001/api/notify",
      "body": { "message": "deployed {{ variables.app }}" },
      "depends_on": ["deploy"] }
  ]
}
```

### Built-in step types

| type        | purpose                                            | key config                        |
|-------------|----------------------------------------------------|-----------------------------------|
| `shell`     | run any shell command                              | `command`, `env`                  |
| `http`      | call any REST API (GET/POST/PUT/PATCH/DELETE...)   | `url`, `method`, `headers`, `body`|
| `python`    | inline Python code with access to the context      | `code`, `imports`                 |
| `condition` | evaluate an expression, record the decision        | `expression`                      |
| `foreach`   | loop over a list, run child steps per item         | `items`, `parallel`, `steps`      |
| `delay`     | wait N seconds                                     | `seconds`                         |

### Engine features

- **Dependency graph (DAG)** — `depends_on`; cycle detection; topological scheduling.
- **Parallel execution** — independent steps run concurrently (`max_workers`).
- **Variable interpolation** — `{{ variables.x }}`, `{{ steps.<id>.output.<key> }}`,
  `{{ steps.<id>.attempts }}`, `{{ this.item }}` / `{{ this.index }}` inside loops.
- **Conditions** — per-step `condition`; steps that evaluate to false are skipped
  (recorded, not failed). Quote string placeholders: `"{{ x }}" == 'a'`.
- **Run policies** — `run: on_success` (default) | `on_failure` | `always`.
- **Retries** — `retry: {max_attempts, backoff_seconds, backoff_factor, max_backoff_seconds}`
  with exponential backoff.
- **Timeouts** — per-step `timeout` (seconds) and whole-workflow `timeout`.
- **Extensibility** — register custom step types with one class + one decorator.
- **Auditable** — every run is persisted as a JSON artifact (`runs/<run_id>.json`)
  with per-step results, durations, attempts and a full log.

### Extending the engine (custom step types)

```python
# my_steps.py
from flowforge.registry import step
from flowforge.steps.base import StepExecutor, StepOutput

@step("slack")
class SlackStep(StepExecutor):
    def validate_config(self, config):
        return [] if "message" in config else ["slack step requires 'message'"]
    def execute(self, step, context, run):
        msg = run.interpolate(step.config["message"])
        return StepOutput(output={"sent": msg})
```

```bash
.venv/Scripts/python -m flowforge run my_workflow.json --plugin my_steps.py
```

See `examples/custom_steps.py` and `examples/custom_workflow.json`.

---

## REST API

| method | path                     | description                                  |
|--------|--------------------------|----------------------------------------------|
| POST   | `/api/workflows/validate`| validate a workflow JSON (returns errors)    |
| POST   | `/api/runs`              | submit a workflow, get `run_id` (async)      |
| GET    | `/api/runs/{run_id}`     | live status, per-step results, logs          |
| GET    | `/api/runs`              | run history                                  |
| GET    | `/api/steps`             | registered step types                        |
| GET    | `/`                      | web dashboard                                |

```bash
curl -X POST http://127.0.0.1:8000/api/runs \
     -H "Content-Type: application/json" \
     -d @examples/data_pipeline.json
# -> {"run_id": "...", "status": "running", ...}

curl http://127.0.0.1:8000/api/runs/<run_id>
```

---

## Example workflows

| file                              | shows                                                   |
|-----------------------------------|---------------------------------------------------------|
| `examples/deploy_workflow.json`   | shell + REST mix, parallel tests/lint, condition, retry, success **and** failure notification (`run: on_failure`) |
| `examples/data_pipeline.json`     | `foreach` in parallel (4 cities), Python analysis, REST report |
| `examples/retry_demo.json`        | automatic retries with backoff against a flaky endpoint  |
| `examples/custom_workflow.json`   | custom step types via `--plugin`                         |
| `examples/mock_api.py`            | fake external system (deploy/weather/notify) with injected failures |

---

## Project layout

```
flowforge/
  engine.py          DAG scheduler, batches, retries, conditions
  expressions.py     {{ var }} interpolation + safe condition evaluator
  models.py          Workflow / Step / StepResult dataclasses
  registry.py        step registry + @step decorator (extensibility)
  store.py           run history (in-memory + JSON artifacts)
  cli.py             validate / run / serve / steps
  steps/             shell, http, python, condition, delay, foreach
  server/            FastAPI app + web dashboard
examples/            demo workflows, custom steps, mock API
tests/               engine test suite (unittest)
```

## Roadmap (beyond the hackathon)

- Webhook / cron triggers and a visual DAG editor
- Step "sandboxing" (containers) for shell steps
- Distributed execution (multiple workers, queues)
- Native integrations: Slack, Jira, Nutanix Prism APIs
- Idempotency keys and cached step results
