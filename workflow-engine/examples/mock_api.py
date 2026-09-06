"""Mock external API - simulates the systems a workflow integrates with.

Behaves like a real service: latency, state, and (optionally) random
failures so you can demonstrate FlowForge's automatic retries live.

Start it before the demo:
    python examples/mock_api.py --port 8001 --fail-rate 0.35

Endpoints:
    POST /api/deploy                 simulate a deployment (flaky if fail-rate > 0)
    GET  /api/deployments/{id}       poll deployment status
    GET  /api/weather?city=...       fake weather readings
    POST /api/notify                 pretend to post to a channel
    POST /api/chaos                  adjust the failure rate at runtime
    GET  /api/health
"""
from __future__ import annotations

import argparse
import random
import threading
import time
import uuid

import uvicorn
from fastapi import FastAPI, HTTPException
from pydantic import BaseModel

app = FastAPI(title="FlowForge Mock External API")

_lock = threading.Lock()
fail_rate = 0.0
deployments: dict[str, dict] = {}


class DeployRequest(BaseModel):
    environment: str = "staging"
    version: str = "unknown"
    commit: str = "unknown"


class NotifyRequest(BaseModel):
    message: str
    channel: str = "#general"


class ChaosRequest(BaseModel):
    rate: float = 0.0


@app.get("/api/health")
def health() -> dict:
    return {"status": "ok", "fail_rate": fail_rate, "deployments": len(deployments)}


@app.post("/api/chaos")
def chaos(req: ChaosRequest) -> dict:
    global fail_rate
    with _lock:
        fail_rate = max(0.0, min(1.0, req.rate))
    return {"fail_rate": fail_rate}


@app.post("/api/deploy")
def deploy(req: DeployRequest) -> dict:
    time.sleep(1.0)  # simulate work
    with _lock:
        if fail_rate > 0 and random.random() < fail_rate:
            raise HTTPException(status_code=500, detail="deployment service unavailable (injected failure)")
        deployment_id = uuid.uuid4().hex[:10]
        deployments[deployment_id] = {
            "deployment_id": deployment_id,
            "environment": req.environment,
            "version": req.version,
            "commit": req.commit,
            "status": "deploying",
            "created_at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
        }
    return deployments[deployment_id]


@app.get("/api/deployments/{deployment_id}")
def deployment_status(deployment_id: str) -> dict:
    time.sleep(0.5)
    dep = deployments.get(deployment_id)
    if dep is None:
        raise HTTPException(status_code=404, detail="deployment not found")
    dep["status"] = "healthy"
    return dep


@app.get("/api/weather")
def weather(city: str = "Guwahati") -> dict:
    time.sleep(0.4)
    return {
        "city": city,
        "temperature_c": round(random.uniform(18, 34), 1),
        "humidity_pct": random.randint(40, 95),
        "condition": random.choice(["sunny", "cloudy", "light rain", "clear"]),
        "observed_at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
    }


@app.post("/api/notify")
def notify(req: NotifyRequest) -> dict:
    time.sleep(0.3)
    print(f"[mock-notify -> {req.channel}] {req.message}")
    return {"status": "sent", "channel": req.channel, "message": req.message}


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="FlowForge mock external API")
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--port", type=int, default=8001)
    parser.add_argument("--fail-rate", type=float, default=0.0, help="probability that /api/deploy fails (0..1)")
    args = parser.parse_args()
    fail_rate = args.fail_rate
    print(f"Mock external API: http://{args.host}:{args.port} (fail rate {fail_rate})")
    uvicorn.run(app, host=args.host, port=args.port)