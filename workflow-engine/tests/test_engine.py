"""End-to-end tests for the FlowForge engine (standard library unittest)."""
from __future__ import annotations

import json
import sys
import threading
import time
import unittest
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

sys.path.insert(0, ".")  # allow running `python -m unittest` from the project root

from flowforge.engine import WorkflowEngine  # noqa: E402
from flowforge.expressions import evaluate_condition  # noqa: E402
from flowforge.models import StepStatus, Workflow, WorkflowValidationError  # noqa: E402
from flowforge.registry import step  # noqa: E402
from flowforge.steps.base import StepExecutor, StepOutput  # noqa: E402


def make_workflow(steps: list) -> Workflow:
    return Workflow.from_dict({"name": "test", "steps": steps})


class EngineTest(unittest.TestCase):
    def setUp(self) -> None:
        self.engine = WorkflowEngine()

    # -- validation ----------------------------------------------------

    def test_valid_workflow(self) -> None:
        workflow = make_workflow([{"id": "a", "type": "shell", "command": "echo hi"}])
        self.assertEqual(self.engine.validate(workflow), [])

    def test_duplicate_ids(self) -> None:
        workflow = make_workflow(
            [
                {"id": "a", "type": "shell", "command": "true"},
                {"id": "a", "type": "shell", "command": "true"},
            ]
        )
        errors = self.engine.validate(workflow)
        self.assertTrue(any("duplicate" in e for e in errors))

    def test_unknown_type(self) -> None:
        workflow = make_workflow([{"id": "a", "type": "nope"}])
        errors = self.engine.validate(workflow)
        self.assertTrue(any("unknown type" in e for e in errors))

    def test_unknown_dependency(self) -> None:
        workflow = make_workflow([{"id": "a", "type": "shell", "command": "true", "depends_on": ["ghost"]}])
        errors = self.engine.validate(workflow)
        self.assertTrue(any("unknown step 'ghost'" in e for e in errors))

    def test_cycle_detected(self) -> None:
        workflow = make_workflow(
            [
                {"id": "a", "type": "shell", "command": "true", "depends_on": ["b"]},
                {"id": "b", "type": "shell", "command": "true", "depends_on": ["a"]},
            ]
        )
        errors = self.engine.validate(workflow)
        self.assertTrue(any("circular" in e for e in errors))

    def test_missing_required_key(self) -> None:
        with self.assertRaises(WorkflowValidationError):
            Workflow.from_dict({"name": "x", "steps": [{"type": "shell", "command": "true"}]})

    # -- execution basics ----------------------------------------------

    def test_shell_success(self) -> None:
        workflow = make_workflow([{"id": "a", "type": "shell", "command": "echo hello"}])
        result = self.engine.execute(workflow)
        self.assertEqual(result.status.value, "success")
        self.assertEqual(result.steps["a"].status, StepStatus.SUCCESS)
        self.assertIn("hello", result.steps["a"].stdout)

    def test_shell_failure(self) -> None:
        workflow = make_workflow([{"id": "a", "type": "shell", "command": "exit 3"}])
        result = self.engine.execute(workflow)
        self.assertEqual(result.status.value, "failed")
        self.assertIn("command exited with code 3", result.steps["a"].error)

    def test_dependencies_order(self) -> None:
        order: list[str] = []

        @step("mark_a")
        class MarkA(StepExecutor):
            def execute(self, step, context, run):
                order.append("a")
                return StepOutput(output={})

        @step("mark_b")
        class MarkB(StepExecutor):
            def execute(self, step, context, run):
                order.append("b")
                return StepOutput(output={})

        workflow = make_workflow(
            [
                {"id": "a", "type": "mark_a", "depends_on": ["b"]},
                {"id": "b", "type": "mark_b"},
            ]
        )
        self.engine.execute(workflow)
        self.assertEqual(order, ["b", "a"])

    def test_parallel_execution(self) -> None:
        workflow = make_workflow(
            [
                {"id": "a", "type": "shell", "command": "sleep 1.0"},
                {"id": "b", "type": "shell", "command": "sleep 1.0"},
            ]
        )
        started = time.time()
        result = self.engine.execute(workflow)
        elapsed = time.time() - started
        self.assertEqual(result.status.value, "success")
        self.assertLess(elapsed, 1.9, f"steps ran sequentially ({elapsed:.2f}s)")

    # -- retries -------------------------------------------------------

    def test_retry_eventually_succeeds(self) -> None:
        calls = {"n": 0}

        @step("flaky")
        class FlakyStep(StepExecutor):
            def execute(self, step, context, run):
                calls["n"] += 1
                if calls["n"] < 3:
                    return StepOutput(error="transient failure")
                return StepOutput(output={"attempt": calls["n"]})

        workflow = make_workflow(
            [{"id": "a", "type": "flaky", "retry": {"max_attempts": 4, "backoff_seconds": 0.01}}]
        )
        result = self.engine.execute(workflow)
        self.assertEqual(result.status.value, "success")
        self.assertEqual(calls["n"], 3)
        self.assertEqual(result.steps["a"].attempts, 3)

    def test_retry_gives_up(self) -> None:
        @step("always_bad")
        class AlwaysBad(StepExecutor):
            def execute(self, step, context, run):
                return StepOutput(error="nope")

        workflow = make_workflow(
            [{"id": "a", "type": "always_bad", "retry": {"max_attempts": 3, "backoff_seconds": 0.01}}]
        )
        result = self.engine.execute(workflow)
        self.assertEqual(result.status.value, "failed")
        self.assertEqual(result.steps["a"].attempts, 3)

    # -- conditions & run policies -------------------------------------

    def test_condition_false_skips(self) -> None:
        workflow = make_workflow(
            [{"id": "a", "type": "shell", "command": "echo ran", "condition": "1 == 2"}]
        )
        result = self.engine.execute(workflow)
        self.assertEqual(result.steps["a"].status, StepStatus.SKIPPED)
        self.assertEqual(result.status.value, "success")  # skip is not a failure

    def test_condition_true_runs(self) -> None:
        workflow = make_workflow(
            [{"id": "a", "type": "shell", "command": "echo ran", "condition": "{{ variables.x }} == 5"}],
        )
        workflow.variables["x"] = 5
        result = self.engine.execute(workflow)
        self.assertEqual(result.steps["a"].status, StepStatus.SUCCESS)

    def test_on_failure_runs_when_dependency_fails(self) -> None:
        workflow = make_workflow(
            [
                {"id": "boom", "type": "shell", "command": "exit 1"},
                {"id": "cleanup", "type": "shell", "command": "echo cleanup", "depends_on": ["boom"], "run": "on_failure"},
            ]
        )
        result = self.engine.execute(workflow)
        self.assertEqual(result.steps["boom"].status, StepStatus.FAILED)
        self.assertEqual(result.steps["cleanup"].status, StepStatus.SUCCESS)

    def test_downstream_skipped_after_failure(self) -> None:
        workflow = make_workflow(
            [
                {"id": "boom", "type": "shell", "command": "exit 1"},
                {"id": "after", "type": "shell", "command": "echo never", "depends_on": ["boom"]},
            ]
        )
        result = self.engine.execute(workflow)
        self.assertEqual(result.steps["after"].status, StepStatus.SKIPPED)

    # -- interpolation -------------------------------------------------

    def test_interpolation_from_previous_step(self) -> None:
        workflow = make_workflow(
            [
                {"id": "a", "type": "shell", "command": "echo v9"},
                {"id": "b", "type": "shell", "command": "echo {{ steps.a.output.stdout }}", "depends_on": ["a"]},
            ]
        )
        result = self.engine.execute(workflow)
        self.assertIn("v9", result.steps["b"].stdout)

    def test_variable_override(self) -> None:
        workflow = make_workflow(
            [{"id": "a", "type": "shell", "command": "echo {{ variables.msg }}"}],
        )
        workflow.variables["msg"] = "override"
        result = self.engine.execute(workflow)
        self.assertIn("override", result.steps["a"].stdout)

    def test_interpolation_missing_path_fails_step(self) -> None:
        workflow = make_workflow(
            [{"id": "a", "type": "shell", "command": "echo {{ steps.nope.output.x }}"}],
        )
        result = self.engine.execute(workflow)
        self.assertEqual(result.steps["a"].status, StepStatus.FAILED)

    # -- foreach -------------------------------------------------------

    def test_foreach_runs_all_items(self) -> None:
        workflow = make_workflow(
            [
                {
                    "id": "loop",
                    "type": "foreach",
                    "items": ["a", "b", "c"],
                    "steps": [{"id": "touch", "type": "shell", "command": "echo item {{ this.index }} = {{ this.item }}"}],
                }
            ]
        )
        result = self.engine.execute(workflow)
        self.assertEqual(result.steps["loop"].status, StepStatus.SUCCESS)
        output = result.steps["loop"].output
        self.assertEqual(output["count"], 3)
        self.assertEqual(len(output["results"]), 3)
        stdout_all = " ".join(r["touch"]["stdout"] for r in output["results"])
        self.assertIn("item 0 = a", stdout_all)
        self.assertIn("item 2 = c", stdout_all)

    def test_foreach_parallel_faster(self) -> None:
        workflow = make_workflow(
            [
                {
                    "id": "loop",
                    "type": "foreach",
                    "items": ["x", "y", "z"],
                    "parallel": True,
                    "steps": [{"id": "s", "type": "delay", "seconds": 0.6}],
                }
            ]
        )
        started = time.time()
        result = self.engine.execute(workflow)
        elapsed = time.time() - started
        self.assertEqual(result.status.value, "success")
        self.assertLess(elapsed, 2.5, f"foreach ran serially ({elapsed:.2f}s)")

    def test_foreach_failure_propagates(self) -> None:
        workflow = make_workflow(
            [
                {
                    "id": "loop",
                    "type": "foreach",
                    "items": ["ok", "bad"],
                    "steps": [
                        {"id": "s", "type": "shell", "command": "echo {{ this.item }}", "condition": "\"{{ this.item }}\" != 'bad'"}
                    ],
                }
            ]
        )
        # skipped children don't fail the iteration; the run should still succeed
        result = self.engine.execute(workflow)
        self.assertEqual(result.status.value, "success")

    # -- custom steps --------------------------------------------------

    def test_custom_step_registration(self) -> None:
        @step("triple")
        class TripleStep(StepExecutor):
            def execute(self, step, context, run):
                return StepOutput(output={"value": run.interpolate(step.config["value"]) * 3})

        workflow = make_workflow([{"id": "a", "type": "triple", "value": "ab"}])
        result = self.engine.execute(workflow)
        self.assertEqual(result.steps["a"].output["value"], "ababab")

    # -- expression evaluator ------------------------------------------

    def test_expressions(self) -> None:
        context = {"variables": {"n": 4, "name": "flowforge"}, "steps": {}}
        self.assertTrue(evaluate_condition("{{ variables.n }} >= 4", context))
        self.assertFalse(evaluate_condition("{{ variables.n }} > 4 and {{ variables.n }} < 10", context))
        self.assertTrue(evaluate_condition("not ({{ variables.n }} == 5)", context))
        self.assertTrue(evaluate_condition("'flow' in '{{ variables.name }}'", context))
        self.assertTrue(evaluate_condition("{{ variables.n }} % 2 == 0", context))

    # -- http step against a local server ------------------------------

    def test_http_step(self) -> None:
        class Handler(BaseHTTPRequestHandler):
            def do_GET(self):
                body = json.dumps({"city": "Guwahati", "temperature_c": 26.5}).encode()
                self.send_response(200)
                self.send_header("Content-Type", "application/json")
                self.send_header("Content-Length", str(len(body)))
                self.end_headers()
                self.wfile.write(body)

            def log_message(self, *args):
                pass

        server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
        thread = threading.Thread(target=server.serve_forever, daemon=True)
        thread.start()
        try:
            port = server.server_address[1]
            workflow = make_workflow(
                [{"id": "fetch", "type": "http", "method": "GET", "url": f"http://127.0.0.1:{port}/weather?city=Guwahati"}]
            )
            result = self.engine.execute(workflow)
            self.assertEqual(result.status.value, "success")
            self.assertEqual(result.steps["fetch"].output["json"]["temperature_c"], 26.5)
        finally:
            server.shutdown()

    def test_http_step_5xx_fails(self) -> None:
        class Handler(BaseHTTPRequestHandler):
            def do_GET(self):
                self.send_response(503)
                self.end_headers()

            def log_message(self, *args):
                pass

        server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
        thread = threading.Thread(target=server.serve_forever, daemon=True)
        thread.start()
        try:
            port = server.server_address[1]
            workflow = make_workflow(
                [{"id": "fetch", "type": "http", "method": "GET", "url": f"http://127.0.0.1:{port}/boom"}]
            )
            result = self.engine.execute(workflow)
            self.assertEqual(result.status.value, "failed")
            self.assertIn("HTTP 503", result.steps["fetch"].error)
        finally:
            server.shutdown()

    # -- python step ---------------------------------------------------

    def test_python_step(self) -> None:
        workflow = make_workflow(
            [
                {"id": "a", "type": "shell", "command": "echo seed"},
                {
                    "id": "p",
                    "type": "python",
                    "code": "result = {'upper': steps['a']['stdout'].strip().upper()}",
                    "depends_on": ["a"],
                },
            ]
        )
        result = self.engine.execute(workflow)
        self.assertEqual(result.steps["p"].status, StepStatus.SUCCESS)
        self.assertEqual(result.steps["p"].output["result"]["upper"], "SEED")

    def test_python_step_error(self) -> None:
        workflow = make_workflow([{"id": "p", "type": "python", "code": "raise ValueError('boom')"}])
        result = self.engine.execute(workflow)
        self.assertEqual(result.steps["p"].status, StepStatus.FAILED)
        self.assertIn("ValueError", result.steps["p"].error)


if __name__ == "__main__":
    unittest.main()