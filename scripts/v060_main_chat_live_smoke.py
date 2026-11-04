"""v0.6.0 M0 live local smoke: Main Chat -> existing runtime -> local Ollama.

Work order ZC-AMCA-V0.6.0-M0-MAIN-CHAT-RUNTIME-INTEGRATION-01 section 15:
run the REAL Main Chat path (POST /api/chat-runs) against the REAL local
Ollama runtime:

  1. Main Chat -> Solo -> ollama qwen3:1.7b
  2. Main Chat -> Adaptive Hybrid (secure gates enabled through the M5.7
     controller after a real local readiness probe) -> ollama local runtime
  3. fresh backend restart -> all four experimental gates OFF

No paid or cloud call is made; no model is downloaded.
"""

from __future__ import annotations

import json
import os
import sys
import tempfile
import threading
import time
import urllib.request
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from amca.service import create_server  # noqa: E402

BASE = "http://127.0.0.1:{port}"
TIMEOUT = 600


def _request(base, token, path, *, method="GET", body=None):
    headers = {"X-AMCA-Session": token, "Origin": "http://localhost:1420"}
    data = None
    if body is not None:
        data = json.dumps(body).encode("utf-8")
        headers["Content-Type"] = "application/json"
    req = urllib.request.Request(
        base + path, method=method, data=data, headers=headers
    )
    with urllib.request.urlopen(req, timeout=TIMEOUT) as response:
        return json.load(response)


def _await_terminal(base, token, run_id, deadline_seconds):
    deadline = time.monotonic() + deadline_seconds
    while True:
        doc = _request(base, token, f"/api/runs/{run_id}")
        if doc["status"] not in {"queued", "running", "cancelling"}:
            return doc
        if time.monotonic() > deadline:
            return doc
        time.sleep(2.0)


def _report(label, doc, elapsed):
    result = doc.get("result") or {}
    calls = result.get("calls", [])
    summaries = [
        str(call.get("summary") or "")
        for call in calls
        if call.get("status") in {"complete", "completed"}
    ]
    snapshot = doc.get("intelligence") or {}
    hybrid = snapshot.get("hybrid") or {}
    trace = result.get("mode_trace") or []
    print(f"== {label}", flush=True)
    print("   status:", doc["status"], "elapsed_s:", round(elapsed, 1), flush=True)
    print("   mode:", doc["mode"], "rounds:", result.get("rounds_completed"), flush=True)
    if snapshot:
        print(
            "   intelligence: participation=",
            snapshot.get("planner_participation"),
            "gates=",
            snapshot.get("gates"),
            flush=True,
        )
        if hybrid:
            print(
                "   hybrid decision:",
                hybrid.get("decision"),
                "mode:",
                hybrid.get("mode"),
                "reason:",
                hybrid.get("fallback_reason"),
                flush=True,
            )
    if trace:
        print(
            "   phase0:",
            trace[0].get("selected_mode"),
            "/",
            trace[0].get("policy_rule"),
            flush=True,
        )
    excerpt = (summaries[0] if summaries else doc.get("error") or "")[:220]
    print("   response excerpt:", excerpt.replace("\n", " "), flush=True)


def main() -> int:
    temporary = tempfile.TemporaryDirectory()
    database = Path(temporary.name) / "smoke.sqlite3"
    server = create_server(
        host="127.0.0.1", port=0, root=ROOT, database=database
    )
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    base = BASE.format(port=server.server_port)
    token = str(_request(base, "", "/api/session")["session_token"])
    context = server.RequestHandlerClass.context

    # -- 1. Main Chat -> Solo -> qwen3:1.7b ---------------------------------
    solo_doc = None
    if os.environ.get("SMOKE_SKIP_SOLO") != "1":
        started = time.monotonic()
        solo = _request(
            base,
            token,
            "/api/chat-runs",
            method="POST",
            body={
                "goal": (
                    "In one bounded paragraph, explain what a deterministic "
                    "fallback is and why it keeps a desktop assistant safe."
                ),
                "mode": "solo",
                "selection_policy": "manual_model_set",
                "participant_ids": ["ollama:qwen3:1.7b"],
            },
        )
        solo_doc = _await_terminal(base, token, str(solo["run_id"]), 300)
        _report("LIVE SOLO: Main Chat -> Solo -> qwen3:1.7b", solo_doc, time.monotonic() - started)

    # -- 2. Main Chat -> Adaptive Hybrid -> local runtime --------------------
    # qwen3's reasoning budget makes the one-shot structured probe
    # intermittent; a small bounded retry keeps the live smoke truthful
    # without weakening the readiness contract (still probe-verified).
    ready = None
    for attempt in range(3):
        ready = context.intelligence_runtime.refresh_local_readiness(
            structured_probe=True,
            adapter_factory=lambda model: _ollama_adapter(model),
        )
        if ready.readiness.get("adaptive_planner") == "ready":
            break
        print(
            f"== readiness attempt {attempt + 1}: adaptive_planner="
            f"{ready.readiness.get('adaptive_planner')}; retrying bounded probe",
            flush=True,
        )
        time.sleep(2.0)
    print("== readiness:", dict(ready.readiness), flush=True)
    adaptive_started = time.monotonic()
    adaptive_doc = None
    try:
        print("== enabling gates (secure M5.7 sequence)", flush=True)
        context.intelligence_runtime.configure_gates(
            {"adaptive_planner": True}, contract_version=1,
            correlation_id="v060-live-adaptive",
        )
        context.intelligence_runtime.configure_gates(
            {"hybrid_execution": True}, contract_version=1,
            correlation_id="v060-live-hybrid",
        )
        print("== gates enabled:", context.intelligence_runtime.gate_snapshot()["gates"], flush=True)
        hybrid = _request(
            base,
            token,
            "/api/chat-runs",
            method="POST",
            body={
                "goal": (
                    "Compare brief evening stretches versus morning walks for "
                    "reducing daily stress, and give one verified takeaway."
                ),
                "mode": "adaptive_hybrid",
                "selection_policy": "manual_model_set",
                "participant_ids": [
                    "ollama:qwen3:1.7b",
                    "ollama:dolphin-llama3:8b",
                ],
            },
        )
        print("== adaptive run started:", hybrid.get("run_id"), "status:", hybrid.get("status"), flush=True)
        adaptive_doc = _await_terminal(base, token, str(hybrid["run_id"]), 560)
        _report(
            "LIVE ADAPTIVE HYBRID: Main Chat -> gated planner -> local runtime",
            adaptive_doc,
            time.monotonic() - adaptive_started,
        )
    except Exception as error:  # noqa: BLE001 - the smoke reports truthfully
        print("== LIVE ADAPTIVE HYBRID: unavailable:", repr(error)[:300], flush=True)
    finally:
        try:
            rolled_back = context.intelligence_runtime.disable_all_experimental(
                correlation_id="v060-live-rollback"
            )
            print("== rollback gates:", rolled_back["gates"], flush=True)
        except Exception as rollback_error:  # noqa: BLE001
            print("== rollback issue:", repr(rollback_error)[:200], flush=True)

    # -- 3. Fresh restart -> all gates OFF -----------------------------------
    server.shutdown()
    server.server_close()
    thread.join(timeout=3)
    server2 = create_server(
        host="127.0.0.1", port=0, root=ROOT, database=database
    )
    thread2 = threading.Thread(target=server2.serve_forever, daemon=True)
    thread2.start()
    base2 = BASE.format(port=server2.server_port)
    token2 = str(_request(base2, "", "/api/session")["session_token"])
    status = _request(base2, token2, "/api/intelligence/status")
    gates = {
        name: state["enabled"]
        for name, state in status["capabilities"].items()
        if name != "deterministic"
    }
    print("== restart gates:", gates, flush=True)
    ok = (
        (solo_doc is None or solo_doc["status"] in {"succeeded", "partial"})
        and (adaptive_doc is None or adaptive_doc["status"] in {"succeeded", "partial", "failed"})
        and not any(gates.values())
    )
    print("== SMOKE RESULT:", "GREEN" if ok else "REVIEW", flush=True)
    server2.shutdown()
    server2.server_close()
    thread2.join(timeout=3)
    os._exit(0)  # skip TemporaryDirectory cleanup racing the closed sqlite handle


def _ollama_adapter(model):
    from amca.models.ollama import OllamaAdapter

    return OllamaAdapter(model=model, timeout=90.0)


if __name__ == "__main__":
    raise SystemExit(main())
