"""Shell step: run any shell command, capture output, honor timeouts."""
from __future__ import annotations

import os
import shutil
import subprocess
import time

from flowforge.registry import step
from flowforge.steps.base import RunContext, StepExecutor, StepOutput


def _pick_shell():
    """On Windows cmd.exe does not understand POSIX syntax (mkdir -p, single
    quotes...). Prefer Git Bash when present so the same workflow runs
    identically on every OS."""
    if os.name == "nt":
        bash = shutil.which("bash")
        if bash:
            return bash
    return None


@step("shell")
class ShellExecutor(StepExecutor):
    """Run a shell command. Config: command (string), env (optional dict)."""

    def validate_config(self, config: dict) -> list[str]:
        errors = []
        command = config.get("command")
        if not isinstance(command, str) or not command.strip():
            errors.append("shell step requires a non-empty 'command' string")
        return errors

    def execute(self, step, context: dict, run: RunContext) -> StepOutput:
        command = run.interpolate(step.config["command"])
        env = dict(os.environ)
        for key, value in (step.env or {}).items():
            env[str(key)] = str(run.interpolate(value))
        run.log(f"$ {command}")
        started = time.time()
        try:
            bash = _pick_shell()
            if bash:  # explicit bash on Windows: POSIX syntax works everywhere
                proc = subprocess.run(
                    [bash, "-c", command],
                    capture_output=True,
                    text=True,
                    timeout=step.timeout,
                    env=env,
                )
            else:
                proc = subprocess.run(
                    command,
                    shell=True,
                    capture_output=True,
                    text=True,
                    timeout=step.timeout,
                    env=env,
                )
            elapsed_ms = (time.time() - started) * 1000
            stdout = proc.stdout or ""
            stderr = proc.stderr or ""
            error = None if proc.returncode == 0 else f"command exited with code {proc.returncode}"
            return StepOutput(
                output={
                    "command": command,
                    "exit_code": proc.returncode,
                    "stdout": stdout,
                    "stderr": stderr,
                    "elapsed_ms": round(elapsed_ms, 1),
                },
                stdout=stdout,
                stderr=stderr,
                exit_code=proc.returncode,
                error=error,
            )
        except subprocess.TimeoutExpired as exc:
            return StepOutput(
                output={"command": command},
                stderr=str(exc),
                error=f"command timed out after {step.timeout}s",
            )
        except OSError as exc:
            return StepOutput(output={"command": command}, error=f"failed to start command: {exc}")