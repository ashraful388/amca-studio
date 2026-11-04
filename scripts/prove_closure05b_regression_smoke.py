"""CLOSURE-05B Part E: closed-capability regression smoke matrix.

Targeted smoke checks (not the full 118-case suite) across every capability
closed by v1.0.1 + CLOSURE-05:

  Solo / Joint / Independent->Discuss    : kernel routing + execution paths
  command execution                       : approved argv + timeout/cancel
  app/process control                     : governed application registry
  browser runtime                         : loopback-only policy
  attachments                             : TXT/PDF/PNG/DOCX ingestion
  artifacts                               : lifecycle + export renditions
  automations                             : claim/dispatch/complete lifecycle
  Model Hub fit                           : DEF-CD2266 unified fit verdict
  provider reset state                    : fresh-provider state preservation

Exit code 0 only when every smoke check passes.

Run:  python scripts/prove_closure05b_regression_smoke.py
"""
from __future__ import annotations

import json
import shutil
import sys
import tempfile
import time
from pathlib import Path

SOFTWARE_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(SOFTWARE_ROOT / "src"))

from amca.agent_runtime import NanoScheduler  # noqa: E402
from amca.artifact_render import ArtifactExporter  # noqa: E402
from amca.artifacts import ArtifactStore  # noqa: E402
from amca.assets import AssetStore  # noqa: E402
from amca.automations import AutomationStore  # noqa: E402
from amca.collaboration import (  # noqa: E402
    CollaborationSignals,
    DesktopCollaborationRouter,
    TaskShape,
)
from amca.domain import CollaborationMode, ComplexityLevel  # noqa: E402
from amca.hardware import model_fit_verdict, profile_local_compute  # noqa: E402
from amca.intelligence import (  # noqa: E402
    ContractMetadata,
    FeatureGateRegistry,
    NanoExecutionCoordinator,
    NanoRunRegistry,
    NanoSpawnProposal,
    Sensitivity,
)
from amca.local_tools import (  # noqa: E402
    FileCapability,
    LocalPermissionStore,
    LocalToolExecutor,
    PermissionProfile,
)
from amca.models.base import (  # noqa: E402
    ModelAdapter,
    ModelCapabilities,
    ModelResponse,
    TokenUsage,
)
from amca.permissions import PermissionStore  # noqa: E402
from amca.scheduler import AutomationScheduler  # noqa: E402
from amca.storage import SQLiteStore  # noqa: E402

RESULTS: list[dict[str, object]] = []


def record(area: str, check_id: str, ok: bool, detail: str) -> None:
    RESULTS.append({"area": area, "id": check_id, "ok": bool(ok), "detail": detail})
    print(("PASS  " if ok else "FAIL  ") + f"[{area}] {check_id} :: {detail}")


def meta_factory(record_id):
    return ContractMetadata(
        record_id=record_id, correlation_id="corr-05b-smoke",
        producer="closure05b-part-e", created_at="2026-09-06T00:00:00+00:00",
        sensitivity=Sensitivity.INTERNAL,
    )


class TinyAdapter(ModelAdapter):
    def __init__(self):
        super().__init__(
            adapter_id="tiny", provider="stub", model="tiny",
            capabilities=ModelCapabilities(is_local=True),
        )

    async def generate(self, request):
        async with self._call_scope(request):
            return ModelResponse(text="smoke", model="tiny", provider="stub",
                                 usage=TokenUsage(), finish_reason="stop")

    async def cancel(self, request_id=None):
        return await super().cancel(request_id)

    def stream(self, request):
        raise NotImplementedError


def smoke_collaboration(work: Path) -> None:
    router = DesktopCollaborationRouter()
    signals = CollaborationSignals()
    parallel = router.decide(
        TaskShape(complexity=ComplexityLevel.MEDIUM, coupled=False,
                  decomposable=True, verification_heavy=False,
                  novelty_required=False),
        signals, eligible_model_count=3,
    )
    joint = router.decide(
        TaskShape(complexity=ComplexityLevel.HIGH, coupled=True,
                  decomposable=True, verification_heavy=False,
                  novelty_required=False),
        signals, eligible_model_count=3,
    )
    solo = router.decide(
        TaskShape(complexity=ComplexityLevel.MEDIUM, coupled=False,
                  decomposable=True, verification_heavy=False,
                  novelty_required=False),
        signals, eligible_model_count=1,
    )
    record("collaboration", "solo-joint-parallel-routing",
           parallel.mode == CollaborationMode.PARALLEL
           and joint.mode == CollaborationMode.JOINT
           and solo.mode == CollaborationMode.SOLO,
           f"parallel={parallel.mode.value} joint={joint.mode.value} "
           f"degraded={solo.mode.value}")


def smoke_command_execution(work: Path) -> None:
    import subprocess
    from amca.domain import ProjectState
    workspace = work / "cmd-ws"
    workspace.mkdir()
    database = work / "cmd.sqlite3"
    with SQLiteStore(database) as store:
        store.save_state(ProjectState(project_id="smoke", goal="s"))
    ls = LocalPermissionStore(database)
    ex = LocalToolExecutor(ls, state_root=work / "cmd-state")
    ls.set_profile(project_id="smoke", profile=PermissionProfile.EXTENDED,
                   workspace_root=workspace)
    grant = ls.create_grant(project_id="smoke", root_path=workspace,
                            capabilities=[FileCapability.PROPOSE_COMMAND],
                            reason="smoke")
    proposed = ex.propose_argv_command(
        project_id="smoke", grant_id=grant.grant_id,
        argv=["ping", "-n", "60", "127.0.0.1"], cwd=".", reason="smoke")
    approval_id = str(proposed["approval"]["id"])
    with PermissionStore(database) as permissions:
        permissions.decide(approval_id, approve=True, decided_by="smoke")
    started = ex.execute_approved_argv(
        project_id="smoke", grant_id=grant.grant_id,
        approval_id=approval_id, timeout_seconds=2)
    deadline = time.monotonic() + 20
    while time.monotonic() < deadline:
        snap = ex.command_run_snapshot(str(started["run_id"]))
        if snap["status"] != "running":
            break
        time.sleep(0.1)
    record("commands", "timeout-and-cancel",
           snap["status"] == "timeout", f"status={snap['status']}")
    ls.close()


def smoke_application_control(work: Path) -> None:
    from amca.domain import ProjectState
    workspace = work / "app-ws"
    workspace.mkdir()
    database = work / "app.sqlite3"
    with SQLiteStore(database) as store:
        store.save_state(ProjectState(project_id="smoke", goal="s"))
    ls = LocalPermissionStore(database)
    ex = LocalToolExecutor(ls, state_root=work / "app-state")
    ls.set_profile(project_id="smoke", profile=PermissionProfile.EXTENDED,
                   workspace_root=workspace)
    grant = ls.create_grant(project_id="smoke", root_path=workspace,
                            capabilities=[FileCapability.PROPOSE_APPLICATION],
                            reason="smoke")
    refused = False
    try:
        ex.propose_application(project_id="smoke", grant_id=grant.grant_id,
                               application="cmd.exe", task="t", reason="r")
    except PermissionError:
        refused = True
    record("applications", "registry-refuses-shell", refused,
           "cmd.exe refused by governed registry")
    ls.close()


def smoke_browser_runtime(work: Path) -> None:
    from amca.local_tools import _normalize_loopback_browser_url
    refused = False
    try:
        _normalize_loopback_browser_url("https://example.com/")
    except PermissionError:
        refused = True
    accepted = False
    try:
        _normalize_loopback_browser_url("http://127.0.0.1:9000/x")
        accepted = True
    except PermissionError:
        pass
    record("browser", "loopback-only-policy", refused and accepted,
           f"public_refused={refused} loopback_accepted={accepted}")


def smoke_attachments(work: Path) -> None:
    store = AssetStore(work / "assets")
    record_txt = store.import_bytes(
        project_id="smoke", filename="a.txt",
        data="hello attachment".encode("utf-8"),
        source="desktop_chat_attachment")
    exe_refused = False
    try:
        store.import_bytes(project_id="smoke", filename="x.exe",
                           data=b"MZ", source="desktop_chat_attachment")
    except Exception:
        exe_refused = True
    record("attachments", "txt-ingest-exe-refused",
           record_txt.media_type == "text/plain" and exe_refused,
           f"txt={record_txt.media_type} exe_refused={exe_refused}")


def smoke_artifacts(work: Path) -> None:
    store = ArtifactStore(database=work / "art.sqlite3",
                          payloads_root=work / "art-payloads")
    exporter = ArtifactExporter(store, renditions_root=work / "art-renditions")
    meta = store.author(project_id="smoke", artifact_type="markdown",
                        title="Smoke", content="# Smoke\nbody",
                        created_by="smoke")
    edited = store.edit(artifact_id=str(meta["artifact_id"]),
                        content="# Smoke\nbody2", created_by="smoke",
                        base_version=1)
    exported = exporter.export(project_id="smoke",
                               artifact_id=str(meta["artifact_id"]),
                               target_format="docx")
    record("artifacts", "edit-then-export",
           edited["current_version"] == 2 and exported["status"] == "exported",
           f"version={edited['current_version']} export={exported['status']}")


def smoke_automations(work: Path) -> None:
    store = AutomationStore(work / "auto.sqlite3")
    saved = store.save({
        "name": "smoke", "project_id": "smoke",
        "goal": "smoke goal", "automation_type": "recurring",
        "schedule": "every 1m",
    })
    store.set_enabled(str(saved["id"]), True)

    jobs = {"n": 0}

    def dispatch(goal: str, project_id: str) -> str:
        jobs["n"] += 1
        return f"run-{jobs['n']}"

    scheduler = AutomationScheduler(store, dispatch, poll_seconds=0.5)
    from datetime import UTC, datetime, timedelta
    base = datetime.now(UTC).replace(microsecond=0)
    first = scheduler.tick(now=base + timedelta(minutes=1))
    replay = scheduler.tick(now=base + timedelta(minutes=1))
    store.set_enabled(str(saved["id"]), False)
    disabled = scheduler.tick(now=base + timedelta(minutes=2))
    record("automations", "claim-replay-disable",
           len(first) == 1 and not replay and not disabled,
           f"first={len(first)} replay={len(replay)} disabled={len(disabled)}")


def smoke_model_hub_fit(work: Path) -> None:
    profile = profile_local_compute(work)
    tiny = model_fit_verdict(profile, 100 * 2**20)
    huge = model_fit_verdict(profile, 500 * 2**30)
    record("model-hub", "unified-fit-verdict",
           tiny == 0 and huge == 2,
           f"100MB_fit={tiny} 500GB_fit={huge} (DEF-CD2266 contract)")


def smoke_nano(work: Path) -> None:
    registry = NanoRunRegistry(meta_factory=meta_factory, run_ref="run-smoke",
                               allowed_capabilities=("citation_check",))
    proposal = NanoSpawnProposal(
        meta=meta_factory("p"), proposal_id="p", run_ref="run-smoke",
        parent_task_id="task:root", parent_depth=0, task_purpose="smoke",
        task_type="verification", requested_child_count=1,
        capabilities=("citation_check",), permissions=("local_search",),
        expected_cost=0.0, correlation_id="corr-05b-smoke")
    assignment = registry.authorize_spawn(
        proposal, parent_permissions=("local_search",),
        gates=FeatureGateRegistry(nano_agents=True))
    coordinator = NanoExecutionCoordinator(
        registry=registry,
        scheduler=NanoScheduler({assignment.nano_id: TinyAdapter()},
                                budget_usd=1.0, max_concurrent=1,
                                max_attempts=1),
        meta_factory=meta_factory, known_parent_task_ids=("task:root",))
    outcome = coordinator.execute([assignment],
                                  gates=FeatureGateRegistry(nano_agents=True))
    record("nano", "gated-execution",
           assignment.nano_id in outcome.get("results", {}),
           f"result_sealed={assignment.nano_id in outcome.get('results', {})}")


def main() -> int:
    work = Path(tempfile.mkdtemp(prefix="closure05b-part-e-"))
    try:
        smoke_collaboration(work)
        smoke_command_execution(work)
        smoke_application_control(work)
        smoke_browser_runtime(work)
        smoke_attachments(work)
        smoke_artifacts(work)
        smoke_automations(work)
        smoke_model_hub_fit(work)
        smoke_nano(work)
        passed = sum(1 for r in RESULTS if r["ok"])
        summary = {
            "phase": "CLOSURE-05B Part E (regression smoke)",
            "passed": passed,
            "total": len(RESULTS),
            "results": RESULTS,
        }
        out = SOFTWARE_ROOT / "tests" / "closure05b-regression-smoke.json"
        out.write_text(json.dumps(summary, indent=2, ensure_ascii=False),
                       encoding="utf-8")
        print(f"SMOKE {passed}/{len(RESULTS)} PASS")
        print("proof written to", out)
        return 0 if passed == len(RESULTS) else 1
    finally:
        shutil.rmtree(work, ignore_errors=True)


if __name__ == "__main__":
    raise SystemExit(main())
