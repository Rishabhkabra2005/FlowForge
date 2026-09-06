"""HTTP step: call any REST API. Uses only the Python standard library."""
from __future__ import annotations

import json
import time
import urllib.error
import urllib.parse
import urllib.request

from flowforge.registry import step
from flowforge.steps.base import RunContext, StepExecutor, StepOutput

METHODS = ("GET", "POST", "PUT", "PATCH", "DELETE", "HEAD", "OPTIONS")


@step("http")
class HttpExecutor(StepExecutor):
    """Call a REST API. Config: url, method, headers, body (JSON-serializable)."""

    def validate_config(self, config: dict) -> list[str]:
        errors = []
        if not config.get("url"):
            errors.append("http step requires a 'url'")
        method = str(config.get("method", "GET")).upper()
        if method not in METHODS:
            errors.append(f"http step: unsupported method '{method}' (use {', '.join(METHODS)})")
        return errors

    def execute(self, step, context: dict, run: RunContext) -> StepOutput:
        method = str(step.config.get("method", "GET")).upper()
        url = run.interpolate(step.config["url"])
        headers = {str(k): str(run.interpolate(v)) for k, v in (step.config.get("headers") or {}).items()}
        body = run.interpolate(step.config.get("body"))

        data = None
        if body is not None:
            if isinstance(body, (dict, list)):
                data = json.dumps(body).encode("utf-8")
            else:
                data = str(body).encode("utf-8")
            headers.setdefault("Content-Type", "application/json")
        headers.setdefault("User-Agent", "FlowForge/1.0")

        run.log(f"{method} {url}")
        started = time.time()
        request = urllib.request.Request(url, data=data, method=method, headers=headers)
        try:
            with urllib.request.urlopen(request, timeout=step.timeout or 30) as response:
                raw = response.read().decode("utf-8", errors="replace")
                status_code = response.getcode()
                response_headers = {k: v for k, v in response.headers.items()}
        except urllib.error.HTTPError as exc:
            # 4xx/5xx are still responses, treat them as data
            raw = exc.read().decode("utf-8", errors="replace")
            status_code = exc.code
            response_headers = {k: v for k, v in exc.headers.items()}
        except Exception as exc:  # network errors, timeouts, DNS...
            elapsed_ms = (time.time() - started) * 1000
            return StepOutput(
                output={"url": url, "method": method, "elapsed_ms": round(elapsed_ms, 1)},
                error=f"{type(exc).__name__}: {exc}",
            )

        elapsed_ms = (time.time() - started) * 1000
        parsed_json = None
        if raw.strip():
            try:
                parsed_json = json.loads(raw)
            except json.JSONDecodeError:
                parsed_json = None
        ok = 200 <= status_code < 300
        return StepOutput(
            output={
                "url": url,
                "method": method,
                "status_code": status_code,
                "body": raw,
                "json": parsed_json,
                "headers": response_headers,
                "elapsed_ms": round(elapsed_ms, 1),
            },
            stdout=raw,
            exit_code=status_code,
            error=None if ok else f"HTTP {status_code}: {raw[:200]}",
        )