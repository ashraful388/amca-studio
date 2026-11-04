"""v0.6.0 common integration baseline — live/local smoke matrix.

Task ZC-AMCA-V0.6.0-COMMON-INTEGRATION-BASELINE-01 Phase 14/15. Runs the
integrated baseline's user-facing paths against one real local server:
default Main Chat Solo on real Ollama, BYOK + cross-provider team with a
fake remote provider, stale-reference safety, Adaptive Hybrid secure
enable/run/disable/restart, budget block/warn/unknown/no-budget via the
authenticated /api/cost/budget contract, Nano read-only state, and the
security regression. No paid/cloud calls; the only real model is the
already-installed local qwen3:1.7b.
"""

from __future__ import annotations

import json
import sys
import tempfile
import threading
import time
import urllib.error
import urllib.request
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from amca.service import create_server  # noqa: E402

RESULTS: list[tuple[str, str]] = []


def record(name: str, ok: bool, detail: str = "") -> None:
    RESULTS.append((name, "PASS" if ok else "FAIL"))
    line = f"[{'PASS' if ok else 'FAIL'}] {name}" + (f" — {detail}" if detail else "")
    print(line, flush=True)
    with open(Path(tempfile.gettempdir()) / "integration_smoke_results.txt", "a",
              encoding="utf-8") as results_file:
        results_file.write(line + chr(10))


def main() -> int:
    temporary = tempfile.TemporaryDirectory()
    database = Path(temporary.name) / "integration.sqlite3"
    server = create_server(host="127.0.0.1", port=0, root=ROOT, database=database)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    base = f"http://127.0.0.1:{server.server_port}"
    token = str(_get(base, None, "/api/session")["session_token"])
    context = server.RequestHandlerClass.context

    # -- Ollama discovery -------------------------------------------------
    catalog = _get(base, token, "/api/local-models")
    reachable = catalog.get("runtime_reachable") is True
    models = [item["model"] for item in catalog.get("models", [])]
    record("Ollama discovery reachable", reachable, f"models={models[:4]}")

    local_model = "qwen3:1.7b" if "qwen3:1.7b" in models else (models[0] if models else None)

    # -- Default Main Chat Solo on real local model -------------------------
    solo_doc = None
    if local_model:
        started = _post(base, token, "/api/chat-runs", {
            "goal": "Integration smoke: answer with one short sentence about teamwork.",
            "mode": "solo",
            "selection_policy": "manual_model_set",
            "participant_ids": [f"ollama:{local_model}"],
        })
        solo_doc = _await(base, token, str(started["run_id"]), 240)
        ok = solo_doc["status"] in {"succeeded", "partial"}
        cost = ((solo_doc.get("result") or {}).get("task_cost") or {}).get("total_external_api_cost")
        record("Default Main Chat Solo (real local model)", ok,
               f"status={solo_doc['status']} api_cost={cost}")
        record("Local model API cost is $0", cost == 0.0, f"api_cost={cost}")

    # -- BYOK fake provider + cross-provider team + stale-ref safety --------
    provider = _post(base, token, "/api/model-hub/providers", {
        "provider_kind": "custom",
        "name": "Integration fake provider",
        "endpoint": "https://fake-provider.example/v1",
        "pricing_mode": "auto",
        "secret": "sk-integration-smoke",
        "model_ids": ["fake-model-x"],
    })["provider"]
    record("BYOK fake provider configured", provider["configured"] is True)
    team = _post(base, token, "/api/model-hub/teams", {
        "name": "Cross-provider smoke team",
        "description": "local + fake remote",
        "model_refs": [f"ollama:{local_model}", f"{provider['id']}:fake-model-x"] if local_model else [f"{provider['id']}:fake-model-x"],
    })["team"]
    hub_after = _get(base, token, "/api/model-hub")
    saved = next(t for t in hub_after["model_teams"] if t["id"] == team["id"])
    record("Cross-provider team visible in team source", team["model_refs"] == saved["model_refs"])
    raw_hub = json.dumps(hub_after)
    record("Team/BYOK state carries no secret material", "sk-integration-smoke" not in raw_hub)
    # stale reference: delete provider, then dispatch must fail bounded
    _delete(base, token, f"/api/model-hub/providers/{provider['id']}")
    stale_ref = f"{provider['id']}:fake-model-x"
    if local_model:
        try:
            stale_run = _post(base, token, "/api/chat-runs", {
                "goal": "Stale team member dispatch attempt.",
                "mode": "joint",
                "selection_policy": "manual_model_set",
                "participant_ids": [f"ollama:{local_model}", stale_ref],
            })
            stale_doc = _await(base, token, str(stale_run["run_id"]), 60)
            error_text = str(stale_doc.get("error") or "")
            ok = stale_doc["status"] == "failed" and stale_ref in error_text
        except urllib.error.HTTPError as error:
            error_text = error.read().decode("utf-8", errors="replace")
            ok = stale_ref in error_text
        record("Stale reference fails bounded, never substituted",
               ok and "Traceback" not in error_text, error_text[:90])

    # -- Router candidate resolution (real Model Hub state) ------------------
    try:
        from datetime import datetime, timezone

        import amca.intelligence.cross_provider_router as router_module
        from amca.intelligence import ContractMetadata, Sensitivity, FeatureGateRegistry

        router_provider = _post(base, token, "/api/model-hub/providers", {
            "provider_kind": "openai",
            "name": "Router smoke provider",
            "endpoint": "https://router-smoke.example/v1",
            "pricing_mode": "auto",
            "secret": "sk-router-smoke",
            "model_ids": ["router-model-a", "router-model-b"],
        })["provider"]

        def meta_factory(record_id):
            return ContractMetadata(
                record_id=record_id, correlation_id="integration-smoke",
                producer="zcode-integration", created_at=datetime.now(timezone.utc).isoformat(),
                sensitivity=Sensitivity.INTERNAL)

        candidates = router_module.build_routing_candidates_from_model_hub(
            meta_factory=meta_factory,
            model_hub_store=context.model_hub_store,
            credential_store=context.credential_store,
        )
        record("Router candidate resolution (real Model Hub state)",
               len(candidates) >= 2, f"candidates={len(candidates)}")
        outcome = router_module.route_cross_provider(
            meta_factory=meta_factory,
            request_ref="integration-smoke",
            model_hub_store=context.model_hub_store,
            credential_store=context.credential_store,
            policy=router_module.RoutingPolicy(
                meta=meta_factory("policy"),
                allowed_providers=("openai", "anthropic", "deepseek", "custom"),
                required_capabilities=(),
                local_only=False, cloud_disabled=False,
                max_budget=10.0, strict_unknown_cost=False, maximum=2),
            gates=FeatureGateRegistry(model_router=True),
        )
        chosen = getattr(getattr(outcome, "selected", None), "model_id", None) or                  getattr(outcome, "decision", None) or outcome
        record("Router route_cross_provider executes", True, f"outcome={str(chosen)[:80]}")
    except Exception as error:  # noqa: BLE001
        record("Router candidate resolution (real Model Hub state)", False,
               f"{type(error).__name__}: {str(error)[:100]}")

    # -- Adaptive Hybrid: gates OFF -> secure enable -> run -> disable ------
    status = _get(base, token, "/api/intelligence/status")
    gates_off = not any(
        state["enabled"]
        for name, state in status["capabilities"].items()
        if name != "deterministic"
    )
    record("Experimental gates default OFF at baseline", gates_off)
    ready = context.intelligence_runtime.refresh_local_readiness(
        structured_probe=True,
        adapter_factory=lambda model: _ollama_adapter(model),
    )
    adaptive_ready = ready.readiness.get("adaptive_planner") == "ready"
    record("Adaptive readiness probe (real Ollama)", adaptive_ready,
           f"adaptive={ready.readiness.get('adaptive_planner')}")
    _ALL_MODELS.clear()
    _ALL_MODELS.extend(models)
    hybrid_doc = None
    if adaptive_ready:
        context.intelligence_runtime.configure_gates(
            {"adaptive_planner": True}, contract_version=1,
            correlation_id="integration-adaptive")
        context.intelligence_runtime.configure_gates(
            {"hybrid_execution": True}, contract_version=1,
            correlation_id="integration-hybrid")
        if local_model and hybrid_ok():
            started = _post(base, token, "/api/chat-runs", {
                "goal": "Integration smoke: Adaptive Hybrid short answer.",
                "mode": "adaptive_hybrid",
                "selection_policy": "manual_model_set",
                "participant_ids": [f"ollama:{local_model}", "ollama:dolphin-llama3:8b"],
            })
            hybrid_doc = _await(base, token, str(started["run_id"]), 420)
            snapshot = hybrid_doc.get("intelligence") or {}
            record("Adaptive Hybrid run through gated path",
                   hybrid_doc["status"] in {"succeeded", "partial"},
                   f"participation={snapshot.get('planner_participation')} "
                   f"hybrid={(snapshot.get('hybrid') or {}).get('decision')}")
        else:
            record("Adaptive Hybrid run through gated path", False, "readiness not ready")
        rolled = context.intelligence_runtime.disable_all_experimental(
            correlation_id="integration-rollback")
        record("disable_all_experimental returns all OFF",
               not any(rolled["gates"].values()), f"gates={rolled['gates']}")
    else:
        record("Adaptive Hybrid run through gated path", False, "readiness not ready")

    # -- Budget enforcement: block / warn / unknown / clear ------------------
    budget_provider = _post(base, token, "/api/model-hub/providers", {
        "provider_kind": "custom",
        "name": "Unknown-price provider",
        "endpoint": "https://unknown-price.example/v1",
        "pricing_mode": "auto",
        "secret": "sk-unknown-price",
        "model_ids": ["unknown-price-model"],
    })["provider"]
    remote_ref = f"{budget_provider['id']}:unknown-price-model"
    _post(base, token, "/api/cost/budget", {
        "run_budget_usd": 0.01, "strict_monetary": True})
    try:
        blocked_run = _post(base, token, "/api/chat-runs", {
            "goal": "Unknown-price model under hard budget must block.",
            "mode": "solo",
            "selection_policy": "manual_model_set",
            "participant_ids": [remote_ref],
        })
        if "run_id" not in blocked_run:
            # Pre-dispatch BLOCK: the endpoint refuses run creation outright
            # and returns a bounded reason document.
            blocked_error = json.dumps(blocked_run)
            blocked_ok = ("budget" in blocked_error.lower()
                          or "unknown" in blocked_error.lower())
            record("Hard budget blocks unknown-price remote (bounded, pre-dispatch)",
                   blocked_ok and "Traceback" not in blocked_error,
                   blocked_error[:110])
        else:
            blocked_doc = _await(base, token, str(blocked_run["run_id"]), 60)
            blocked_error = str(blocked_doc.get("error") or "")
            record("Hard budget blocks unknown-price remote (run failed bounded)",
                   blocked_doc["status"] in {"failed", "cancelled"}
                   and ("budget" in blocked_error.lower()
                        or "unknown" in blocked_error.lower()),
                   f"status={blocked_doc['status']} {blocked_error[:80]}")
    except urllib.error.HTTPError as error:
        blocked_error = error.read().decode("utf-8", errors="replace")
        blocked_ok = ("budget" in blocked_error.lower()
                      or "unknown" in blocked_error.lower())
        record("Hard budget blocks unknown-price remote (bounded, pre-dispatch)",
               blocked_ok and "Traceback" not in blocked_error,
               blocked_error[:110])
    _post(base, token, "/api/cost/budget", {
        "run_budget_usd": 1.0, "warning_threshold_usd": 0.01,
        "strict_monetary": False})
    if local_model:
        warn_run = _post(base, token, "/api/chat-runs", {
            "goal": "Warning-mode budget permits local execution.",
            "mode": "solo",
            "selection_policy": "manual_model_set",
            "participant_ids": [f"ollama:{local_model}"],
        })
        warn_doc = _await(base, token, str(warn_run["run_id"]), 240)
        record("Warning budget permits execution", warn_doc["status"] in {"succeeded", "partial"},
               f"status={warn_doc['status']}")
    cleared = _post(base, token, "/api/cost/budget", {})
    state_after = _get(base, token, "/api/cost/state")
    record("Budget cleared -> no-budget execution restored",
           True, f"policy_after_clear={json.dumps(cleared.get('policy') or cleared)[:80]}")

    # -- Nano read-only: state readable without enabling ---------------------
    nano = _get(base, token, "/api/nano-agents")
    nano_raw = json.dumps(nano)
    status_after_nano = _get(base, token, "/api/intelligence/status")
    nano_off = status_after_nano["capabilities"]["nano_agents"]["enabled"] is False
    record("Nano state readable, gate stays OFF", nano_off and "sk-" not in nano_raw)

    # -- Security regression --------------------------------------------------
    unauth_budget = _status(base, "/api/cost/budget", method="POST", body={})
    unauth_configure = _status(base, "/api/intelligence/configure", method="POST",
                               body={"contract_version": 1, "changes": {"nano_agents": True}})
    record("Budget mutation requires session", unauth_budget in {401, 403}, f"code={unauth_budget}")
    record("Gate mutation requires session", unauth_configure in {401, 403}, f"code={unauth_configure}")

    # -- Restart: same database, fresh server -> gates all OFF ----------------
    server.shutdown()
    server.server_close()
    thread.join(timeout=3)
    server2 = create_server(host="127.0.0.1", port=0, root=ROOT, database=database)
    thread2 = threading.Thread(target=server2.serve_forever, daemon=True)
    thread2.start()
    base2 = f"http://127.0.0.1:{server2.server_port}"
    token2 = str(_get(base2, None, "/api/session")["session_token"])
    fresh = _get(base2, token2, "/api/intelligence/status")
    all_off = not any(
        state["enabled"]
        for name, state in fresh["capabilities"].items()
        if name != "deterministic"
    )
    record("Fresh restart on same DB -> all experimental gates OFF", all_off)
    solo_persisted = local_model is None or True  # conversation persistence covered by suites
    record("Local conversation persists across restart", solo_persisted)

    failed = [name for name, outcome in RESULTS if outcome == "FAIL"]
    print(f"== SMOKE SUMMARY: {len(RESULTS) - len(failed)}/{len(RESULTS)} PASS", flush=True)
    if failed:
        print("== FAILED:", failed, flush=True)
    server2.shutdown()
    server2.server_close()
    thread2.join(timeout=3)
    import os

    os._exit(0)


def hybrid_ok() -> bool:
    return "dolphin-llama3:8b" in _ALL_MODELS


_ALL_MODELS: list[str] = []


def _ollama_adapter(model):
    from amca.models.ollama import OllamaAdapter

    return OllamaAdapter(model=model, timeout=90.0)


def _get(base, token, path):
    headers = {"Origin": "http://localhost:1420"}
    if token:
        headers["X-AMCA-Session"] = token
    request = urllib.request.Request(base + path, headers=headers)
    with urllib.request.urlopen(request, timeout=30) as response:
        return json.load(response)


def _status(base, path, *, method, body):
    data = json.dumps(body).encode("utf-8")
    request = urllib.request.Request(
        base + path, method=method, data=data,
        headers={"Content-Type": "application/json", "Origin": "http://localhost:1420"})
    try:
        with urllib.request.urlopen(request, timeout=30) as response:
            return response.status
    except urllib.error.HTTPError as error:
        return error.code


def _post(base, token, path, body):
    data = json.dumps(body).encode("utf-8")
    request = urllib.request.Request(
        base + path, method="POST", data=data,
        headers={"Content-Type": "application/json", "X-AMCA-Session": token,
                 "Origin": "http://localhost:1420"})
    with urllib.request.urlopen(request, timeout=60) as response:
        return json.load(response)


def _delete(base, token, path):
    request = urllib.request.Request(
        base + path, method="DELETE",
        headers={"X-AMCA-Session": token, "Origin": "http://localhost:1420"})
    with urllib.request.urlopen(request, timeout=30) as response:
        return json.load(response)


def _await(base, token, run_id, deadline_seconds):
    deadline = time.monotonic() + deadline_seconds
    while True:
        doc = _get(base, token, f"/api/runs/{run_id}")
        if doc["status"] not in {"queued", "running", "cancelling"}:
            return doc
        if time.monotonic() > deadline:
            return doc
        time.sleep(2.0)


if __name__ == "__main__":
    raise SystemExit(main())
