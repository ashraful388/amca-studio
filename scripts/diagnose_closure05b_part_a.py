"""CLOSURE-05B Part A: physical diagnosis of the Parallel two-model run.

Starts a REAL dev-bootstrap AMCA backend against REAL Ollama, requests a
Parallel chat run with ollama:qwen3:1.7b + ollama:dolphin-llama3:8b, and
records the exact gate/failure path: per-model inventory fit, chat-run
request outcome, run terminal state, and per-call errors.

No mocks. Paid API calls: 0.

Run:  python scripts/diagnose_closure05b_part_a.py
"""
from __future__ import annotations

import json
import sys
import tempfile
import threading
import time
import urllib.request
from pathlib import Path

SOFTWARE_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(SOFTWARE_ROOT / "src"))

from amca.service import create_server  # noqa: E402

RESULTS: dict[str, object] = {}


def call(base: str, token: str, path: str, body: dict | None = None):
    url = base + path
    data = json.dumps(body).encode() if body is not None else None
    req = urllib.request.Request(url, method="POST" if data else "GET")
    req.add_header("X-AMCA-Session", token)
    if data:
        req.add_header("Content-Type", "application/json")
    try:
        with urllib.request.urlopen(req, data=data, timeout=120) as r:
            return r.status, json.loads(r.read().decode())
    except urllib.error.HTTPError as e:
        return e.code, json.loads(e.read().decode() or "{}")


def main() -> int:
    temporary = tempfile.TemporaryDirectory()
    database = Path(temporary.name) / "diag.sqlite3"
    server = create_server(
        host="127.0.0.1", port=0,
        root=SOFTWARE_ROOT, database=database,
    )
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    base = f"http://127.0.0.1:{server.server_port}"
    try:
        with urllib.request.urlopen(base + "/api/session", timeout=10) as r:
            token = str(json.load(r)["session_token"])

        # 1) real inventory: what fits does the backend actually publish?
        status, inventory = call(base, token, "/api/local-models")
        models = inventory.get("models", [])
        listing = [
            {"model": m.get("model"), "fit": m.get("fit"), "size_gb": round((m.get("size_bytes") or 0) / 2**30, 2)}
            for m in models
        ]
        RESULTS["inventory"] = listing
        print("INVENTORY:", json.dumps(listing, indent=1))

        # 2) hardware profile the gate uses
        context = server.amca_context
        profile = context.local_compute_profile() if hasattr(context, "local_compute_profile") else None
        try:
            from amca.hardware import profile_local_compute
            prof = profile_local_compute(SOFTWARE_ROOT)
            RESULTS["hardware"] = {
                "total_ram_gb": round((prof.total_ram_bytes or 0) / 2**30, 1),
                "available_ram_gb": round((prof.available_ram_bytes or 0) / 2**30, 1),
                "gpus": [g.to_dict() for g in (prof.gpus or ())],
                "tier": prof.recommended_local_tier,
            }
            print("HARDWARE:", json.dumps(RESULTS["hardware"], indent=1))
        except Exception as e:
            RESULTS["hardware_error"] = str(e)
            print("hardware profile error:", e)

        # 3) real parallel two-model run
        body = {
            "goal": "Write one paragraph describing the AlexNet architecture.",
            "mode": "parallel",
            "selection_policy": "manual_model_set",
            "participant_ids": ["ollama:qwen3:1.7b", "ollama:dolphin-llama3:8b"],
        }
        status, started = call(base, token, "/api/chat-runs", body)
        RESULTS["chat_run_request"] = {"status": status, "body": started}
        print("CHAT-RUN:", status, json.dumps(started)[:400])

        run_id = started.get("run_id")
        if run_id:
            deadline = time.monotonic() + 600
            current = started
            while time.monotonic() < deadline:
                status, current = call(base, token, f"/api/runs/{run_id}")
                if current.get("status") not in {"queued", "running", "cancelling"}:
                    break
                time.sleep(2)
            RESULTS["terminal"] = {
                "status": current.get("status"),
                "stop_reason": current.get("stop_reason"),
                "calls": [
                    {
                        "adapter_id": c.get("adapter_id"),
                        "status": c.get("status"),
                        "error": c.get("error"),
                        "elapsed": c.get("elapsed_seconds"),
                        "summary": str(c.get("summary"))[:80],
                    }
                    for c in (current.get("calls") or [])
                ],
            }
            print("TERMINAL:", json.dumps(RESULTS["terminal"], indent=1))
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=2)
        temporary.cleanup()
        out = SOFTWARE_ROOT / "tests" / "closure05b-part-a-diagnosis.json"
        out.write_text(json.dumps(RESULTS, indent=2, ensure_ascii=False), encoding="utf-8")
        print("diagnosis written to", out)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
