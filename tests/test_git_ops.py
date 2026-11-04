"""X1 Git write operations and multi-repo worktree tests."""

from __future__ import annotations

import subprocess
import tempfile
import time
import unittest
from pathlib import Path

from amca.local_tools import (
    FileCapability,
    LocalPermissionStore,
    LocalToolExecutor,
    PermissionProfile,
)
from amca.permissions import PermissionAction, PermissionStore
from amca.domain import ProjectState
from amca.security import SensitiveDataError
from amca.storage import SQLiteStore
import hashlib
import json


class GitOpsTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temporary = tempfile.TemporaryDirectory()
        self.root = Path(self.temporary.name)
        self.database = self.root / "state.sqlite3"
        self.project_id = "git-ops-project"
        with SQLiteStore(self.database) as store:
            store.save_state(ProjectState(project_id=self.project_id, goal="git"))
        self.store = LocalPermissionStore(self.database)
        self.store.set_profile(
            project_id=self.project_id,
            profile=PermissionProfile.EXTENDED,
            workspace_root=self.root,
        )
        self.executor = LocalToolExecutor(
            self.store, state_root=self.root / "tool-state"
        )

    def tearDown(self) -> None:
        # Give detached git processes a moment to release handles on Windows.
        time.sleep(0.2)
        self.store.close()
        self.temporary.cleanup()

    def _init_repo(self, name: str) -> Path:
        repo = self.root / name
        repo.mkdir(parents=True, exist_ok=True)
        (repo / "README.md").write_text(f"# {name}\n", encoding="utf-8")
        env_args = [
            "git", "-C", str(repo), "-c", "user.email=amca@test",
            "-c", "user.name=AMCA Test", "commit", "-am", "initial",
        ]
        subprocess.run(["git", "init", "-q"], cwd=repo, check=True)
        subprocess.run(
            ["git", "config", "core.autocrlf", "false"], cwd=repo, check=True
        )
        subprocess.run(["git", "-C", str(repo), "add", "README.md"], check=True)
        subprocess.run(env_args, check=True, capture_output=True)
        return repo

    def _grant_for(self, repo: Path):
        return self.store.create_grant(
            project_id=self.project_id,
            root_path=repo,
            capabilities=[FileCapability.PROPOSE_COMMAND],
            reason="Bounded git grant",
        )

    def _approve_argv(self, grant_id: str, argv: list[str], cwd: str = ".") -> str:
        scope = {
            "grant_id": grant_id,
            "cwd": cwd,
            "argv": argv,
            "argv_sha256": hashlib.sha256(
                json.dumps(argv, ensure_ascii=False).encode("utf-8")
            ).hexdigest(),
            "executor": "bounded_subprocess",
        }
        with PermissionStore(self.database) as permissions:
            request = permissions.request(
                project_id=self.project_id,
                action=PermissionAction.RUN_COMMAND,
                scope=scope,
                reason="Approved git write",
                risk="high",
            )
            permissions.decide(request.id, approve=True, decided_by="test_user")
            return request.id

    def test_status_reports_branch_and_changes_per_repo(self) -> None:
        repo_a = self._init_repo("repo-a")
        repo_b = self._init_repo("repo-b")
        grant_a = self._grant_for(repo_a)
        grant_b = self._grant_for(repo_b)

        (repo_a / "changed.txt").write_text("dirty", encoding="utf-8")

        status_a = self.executor.git_status(
            project_id=self.project_id, grant_id=grant_a.grant_id
        )
        status_b = self.executor.git_status(
            project_id=self.project_id, grant_id=grant_b.grant_id
        )
        self.assertIn(status_a["branch"], {"main", "master"})
        self.assertTrue(any("changed.txt" in line for line in status_a["changes"]))
        self.assertEqual(status_b["changes"], [])

    def test_approved_commit_writes_only_its_repo(self) -> None:
        repo_a = self._init_repo("commit-a")
        repo_b = self._init_repo("commit-b")
        grant_a = self._grant_for(repo_a)
        (repo_a / "feature.txt").write_text("feature", encoding="utf-8")

        add_argv = ["git", "add", "feature.txt"]
        approval_add = self._approve_argv(grant_a.grant_id, add_argv)
        result_add = self.executor.git_write_command(
            project_id=self.project_id,
            grant_id=grant_a.grant_id,
            approval_id=approval_add,
        )
        self.assertEqual(result_add["exit_code"], 0)

        commit_argv = ["git", "commit", "-m", "Add feature"]
        approval_commit = self._approve_argv(grant_a.grant_id, commit_argv)
        result_commit = self.executor.git_write_command(
            project_id=self.project_id,
            grant_id=grant_a.grant_id,
            approval_id=approval_commit,
        )
        self.assertEqual(result_commit["exit_code"], 0)

        log = subprocess.run(
            ["git", "-C", str(repo_a), "log", "--oneline"],
            capture_output=True, text=True, check=True,
        ).stdout
        self.assertIn("Add feature", log)
        status_b = self.executor.git_status(
            project_id=self.project_id,
            grant_id=self._grant_for(repo_b).grant_id,
        )
        self.assertEqual(status_b["changes"], [])

    def test_refused_subcommand_stays_refused_with_approval(self) -> None:
        repo = self._init_repo("refused")
        grant = self._grant_for(repo)
        argv = ["git", "push", "origin", "main"]
        approval_id = self._approve_argv(grant.grant_id, argv)
        with self.assertRaisesRegex(PermissionError, "refused at v1"):
            self.executor.git_write_command(
                project_id=self.project_id,
                grant_id=grant.grant_id,
                approval_id=approval_id,
            )

    def test_worktree_add_and_list(self) -> None:
        repo = self._init_repo("worktree-src")
        grant = self._grant_for(repo)
        branch_approval = self._approve_argv(grant.grant_id, ["git", "branch", "feature/x"])
        self.executor.git_write_command(
            project_id=self.project_id,
            grant_id=grant.grant_id,
            approval_id=branch_approval,
        )

        worktree_dir = repo / "wt-feature"
        wt_argv = ["git", "worktree", "add", str(worktree_dir), "feature/x"]
        wt_approval = self._approve_argv(grant.grant_id, wt_argv)
        result = self.executor.git_worktree_add(
            project_id=self.project_id,
            grant_id=grant.grant_id,
            approval_id=wt_approval,
        )
        self.assertEqual(result["exit_code"], 0)
        branches = [entry.get("branch") for entry in result["worktrees"]]
        self.assertIn("feature/x", branches)

        listing = self.executor.git_worktree_list(
            project_id=self.project_id, grant_id=grant.grant_id
        )
        self.assertGreaterEqual(len(listing["worktrees"]), 2)


if __name__ == "__main__":
    unittest.main()
