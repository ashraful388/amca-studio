"""Unit tests for AMCA Studio v1.0.0 Post-GA Stability and Operations Foundation.

Milestone: ZCODE-AMCA-STUDIO-V1.0.0-POST-GA-STABILITY-AND-OPERATIONS-FOUNDATION-01
"""

from pathlib import Path

from amca.operations_foundation import (
    BackupRestoreManager,
    CrashReportingManager,
    DiagnosticsManager,
    ReleaseChannel,
    UpdateReadinessManager,
    UpdateState,
)


def test_diagnostics_bundle_generation_and_secret_redaction():
    manager = DiagnosticsManager()
    custom_providers = [
        {"provider": "ollama", "base_url": "http://127.0.0.1:11434", "enabled": True},
        {"provider": "anthropic", "api_key": "sk-ant-api03-verysecretstring12345678", "enabled": False},
    ]
    report = manager.generate_diagnostic_report(providers_config=custom_providers)

    assert report.product_version == "0.9.0"
    assert report.telemetry_uploaded is False
    assert report.secrets_detected >= 1
    assert report.os_info["system"] != ""
    assert report.python_info["version"] != ""

    # Ensure no secrets leak in output
    for p in report.model_providers_summary:
        if "api_key" in p:
            assert p["api_key"] == "[REDACTED_SECRET]"


def test_crash_reporting_local_only_and_troubleshooting_advice():
    try:
        raise ConnectionRefusedError("Failed to connect to local Ollama daemon at port 11434")
    except Exception as e:
        crash = CrashReportingManager.capture_crash(e)

    assert crash.cloud_reported is False
    assert crash.exception_type == "ConnectionRefusedError"
    assert "Failed to connect to local Ollama daemon" in crash.exception_message
    assert len(crash.troubleshooting_advice) >= 3
    assert "ollama" in " ".join(crash.troubleshooting_advice).lower()


def test_backup_export_integrity(tmp_path: Path):
    backup_zip = tmp_path / "backups" / "ws_backup.zip"
    artifacts = [
        {"id": "art-1", "title": "Architecture Report", "hash": "sha256-abc"},
        {"id": "art-2", "title": "Model Spec", "hash": "sha256-def"},
    ]
    knowledge = [
        {"id": "know-1", "corpus": "Clinical Guidelines", "entries": 42},
    ]

    res = BackupRestoreManager.export_workspace_backup(
        workspace_id="ws-99",
        workspace_name="Research Workspace",
        artifacts=artifacts,
        knowledge_records=knowledge,
        output_zip=backup_zip,
    )

    assert backup_zip.exists()
    assert res["manifest"]["workspace_id"] == "ws-99"
    assert res["manifest"]["artifact_count"] == 2
    assert res["manifest"]["knowledge_record_count"] == 1
    assert len(res["sha256"]) == 64


def test_restore_safety_prevents_destructive_overwrite(tmp_path: Path):
    backup_zip = tmp_path / "ws_backup.zip"
    BackupRestoreManager.export_workspace_backup(
        workspace_id="ws-100",
        workspace_name="Safe Workspace",
        artifacts=[{"id": "art-1"}],
        knowledge_records=[],
        output_zip=backup_zip,
    )

    target_dir = tmp_path / "target_ws"
    target_dir.mkdir(parents=True, exist_ok=True)
    # Simulate existing pre-existing workspace
    (target_dir / "workspace_state.json").write_text("{}", encoding="utf-8")

    # Should refuse destructive overwrite by default
    refused = BackupRestoreManager.validate_and_restore_backup(
        backup_zip=backup_zip,
        target_dir=target_dir,
        allow_destructive_overwrite=False,
    )
    assert refused["restored"] is False
    assert refused["reason"] == "TARGET_EXISTS_OVERWRITE_FORBIDDEN"

    # Permitted with explicit override
    allowed = BackupRestoreManager.validate_and_restore_backup(
        backup_zip=backup_zip,
        target_dir=target_dir,
        allow_destructive_overwrite=True,
    )
    assert allowed["restored"] is True
    assert allowed["artifacts_restored"] == 1


def test_update_readiness_version_comparison_and_channels():
    assert UpdateReadinessManager.compare_versions("0.9.0", "1.0.0") == UpdateState.UPDATE_AVAILABLE
    assert UpdateReadinessManager.compare_versions("0.9.0", "0.9.0") == UpdateState.UP_TO_DATE
    assert UpdateReadinessManager.compare_versions("1.0.0", "0.9.0") == UpdateState.ROLLBACK_DETECTED

    eval_stable = UpdateReadinessManager.evaluate_channel_readiness(
        current_version="0.9.0",
        channel=ReleaseChannel.STABLE,
        channel_latest_version="0.9.0",
    )
    assert eval_stable["update_state"] == "UP_TO_DATE"
    assert eval_stable["automatic_download_enabled"] is False

    eval_beta = UpdateReadinessManager.evaluate_channel_readiness(
        current_version="0.9.0",
        channel=ReleaseChannel.BETA,
        channel_latest_version="1.0.0-beta.1",
    )
    assert eval_beta["update_state"] == "UPDATE_AVAILABLE"

