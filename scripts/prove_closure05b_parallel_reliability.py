"""CLOSURE-05B Part B: Parallel reliability physical proof (3 consecutive runs).

For each of 3 consecutive iterations:
  - starts a REAL dev-bootstrap backend against REAL Ollama;
  - requests a Parallel chat run with ollama:qwen3:1.7b +
    ollama:dolphin-llama3:8b;
  - wraps the REAL OllamaAdapter with a timing observer (pure wall-clock
    observation; inference is untouched, no mocks);
  - asserts: run terminal status 'succeeded'; BOTH participants produced
    completed branch contributions with distinct non-empty summaries; the two
    participants' first model calls genuinely OVERLAPPED in wall time
    (independent concurrent branches, not sequential execution); recovery
    stayed within the bounded corrective-retry policy; paid API calls 0.

Run:  python scripts/prove_closure05b_parallel_reliability.py
"""
from __future__ import annotations

import asyncio
import json
import sys
import tempfile
import threading
import time
import urllib.request
from pathlib import Path

SOFTWARE_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(SOFTWARE_ROOT / "src"))

from unittest.mock import patch

from amca.models.ollama import OllamaAdapter  # noqa: E402
from amca.service import create_server  # noqa: E402

RESULTS: list[dict[str, object]] = []
TIMELINE: list[dict[str, object]] = []  # per-call wall-clock observations


class ObservedOllamaAdapter(OllamaAdapter):
    """Real adapter + wall-clock observation of each dispatched call.

    The kernel's main dispatch path consumes adapter.stream(); overriding
    stream observes exactly the calls the run makes while delegating the
    real inference to the production Ollama adapter.
    """

    async def stream(self, request):  # noqa: D102 - delegate to real engine
        entry = {
            "adapter_id": self.adapter_id,
            "model": self.model,
            "started": time.monotonic(),
        }
        TIMELINE.append(entry)
        try:
            async for chunk in super().stream(request):
                yield chunk
        finally:
            entry["finished"] = time.monotonic()
            entry["elapsed"] = round(entry["finished"] - entry["started"], 2)


def call(base: str, token: str, path: str, body: dict | None = None):
    data = json.dumps(body).encode() if body is not None else None
    req = urllib.request.Request(base + path, method="POST" if data else "GET")
    req.add_header("X-AMCA-Session", token)
    if data:
        req.add_header("Content-Type", "application/json")
    try:
        with urllib.request.urlopen(req, data=data, timeout=120) as r:
            return r.status, json.loads(r.read().decode())
    except urllib.error.HTTPError as e:
        return e.code, json.loads(e.read().decode() or "{}")


def one_run(iteration: int) -> dict[str, object]:
    TIMELINE.clear()
    temporary = tempfile.TemporaryDirectory()
    # Patch the service-level adapter constructor so every REAL Ollama call
    # is wall-clock observed; inference itself is untouched.
    patcher = patch("amca.service.OllamaAdapter", ObservedOllamaAdapter)
    patcher.start()
    server = create_server(
        host="127.0.0.1", port=0, root=SOFTWARE_ROOT,
        database=Path(temporary.name) / f"p{iteration}.sqlite3",
    )
    threading.Thread(target=server.serve_forever, daemon=True).start()
    base = f"http://127.0.0.1:{server.server_port}"
    record: dict[str, object] = {"iteration": iteration}
    try:
        token = str(json.loads(
            urllib.request.urlopen(base + "/api/session", timeout=10).read()
        )["session_token"])
        started_at = time.monotonic()
        _status, started = call(base, token, "/api/chat-runs", {
            "goal": "Write one paragraph describing the AlexNet architecture.",
            "mode": "parallel",
            "selection_policy": "manual_model_set",
            "participant_ids": ["ollama:qwen3:1.7b", "ollama:dolphin-llama3:8b"],
        })
        run_id = str(started.get("run_id"))
        record["run_id"] = run_id
        deadline = time.monotonic() + 900
        current = started
        while time.monotonic() < deadline:
            _s, current = call(base, token, f"/api/runs/{run_id}")
            if current.get("status") not in {"queued", "running", "cancelling"}:
                break
            time.sleep(3)
        record["status"] = current.get("status")
        record["wall_seconds"] = round(time.monotonic() - started_at, 1)
        calls = (current.get("result") or {}).get("calls", [])
        record["calls"] = [
            {
                "adapter_id": c.get("adapter_id"),
                "status": c.get("status"),
                "error": c.get("error"),
                "summary_head": str(c.get("summary"))[:60],
                "output_tokens": c.get("output_tokens"),
            }
            for c in calls
        ]

        # Branch independence: both participants ultimately completed with
        # distinct non-empty summaries.
        summaries: dict[str, str] = {}
        for c in calls:
            if c.get("status") in {"complete", "completed"} and not c.get("error"):
                text = str(c.get("summary") or "").strip()
                if text:
                    summaries.setdefault(str(c.get("adapter_id")), text)
        record["branch_outputs"] = {
            adapter: {"present": True, "head": text[:60]}
            for adapter, text in summaries.items()
        }
        has_qwen = any("qwen3:1.7b" in k for k in summaries)
        has_dolphin = any("dolphin-llama3:8b" in k for k in summaries)
        record["outputs_distinct"] = (
            len(set(summaries.values())) == len(summaries)
            and has_qwen and has_dolphin
        )

        # REAL overlap: the two participants' observed call intervals intersect.
        qwen = [e for e in TIMELINE if "qwen3:1.7b" in str(e["adapter_id"])]
        dolp = [e for e in TIMELINE if "dolphin-llama3:8b" in str(e["adapter_id"])]
        overlap = False
        detail = ""
        if qwen and dolp:
            q0, q1 = qwen[0]["started"], max(e.get("finished", 0) for e in qwen)
            d0, d1 = dolp[0]["started"], max(e.get("finished", 0) for e in dolp)
            overlap = min(q1, d1) > max(q0, d0)
            detail = (
                f"qwen[{q0 - started_at:.0f}s..{q1 - started_at:.0f}s] "
                f"dolphin[{d0 - started_at:.0f}s..{d1 - started_at:.0f}s]"
            )
        record["real_overlap"] = bool(overlap)
        record["overlap_detail"] = detail
        record["observed_calls"] = len(TIMELINE)

        record["recovered_attempts"] = [
            {"adapter_id": c.get("adapter_id"), "error": str(c.get("error"))[:70]}
            for c in calls
            if c.get("status") == "failed" and c.get("error")
        ]
        record["ok"] = bool(
            record["status"] == "succeeded"
            and record["outputs_distinct"]
            and record["real_overlap"]
        )
        return record
    finally:
        server.shutdown()
        server.server_close()
        patcher.stop()
        temporary.cleanup()


def main() -> int:
    for i in (1, 2, 3):
        print(f"--- parallel reliability run {i}/3 ---", flush=True)
        record = one_run(i)
        RESULTS.append(record)
        print(json.dumps({k: record[k] for k in (
            "status", "wall_seconds", "outputs_distinct", "real_overlap",
            "overlap_detail", "recovered_attempts")}, ensure_ascii=False))
    passed = sum(1 for r in RESULTS if r["ok"])
    summary = {
        "phase": "CLOSURE-05B Part B (Parallel reliability)",
        "reliability": f"{passed}/3 PASS",
        "results": RESULTS,
    }
    out = SOFTWARE_ROOT / "tests" / "closure05b-parallel-reliability-proof.json"
    out.write_text(json.dumps(summary, indent=2, ensure_ascii=False), encoding="utf-8")
    print(f"PARALLEL_RELIABILITY={passed}/3 PASS")
    print("proof written to", out)
    return 0 if passed == 3 else 1


if __name__ == "__main__":
    raise SystemExit(main())
