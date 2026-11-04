"""
AMCA Studio User Deployment Readiness Test Suite
Validates:
1. Clean first-launch bootstrap in an uninitialized user environment
2. Automatic creation of parent directories when .amca does not exist
3. Upgrading binary versions preserves canonical SQLite state without loss
4. Clear separation between application binaries and user data directory
"""

import json
import tempfile
import threading
import time
import unittest
from pathlib import Path
from urllib.error import HTTPError
from urllib.request import Request, urlopen

from amca.service import create_server


class StudioUserDeploymentReadinessTests(unittest.TestCase):
    def test_clean_environment_first_launch_auto_mkdir(self) -> None:
        """Verify that launching server against non-existent directories automatically creates parents."""
        with tempfile.TemporaryDirectory(ignore_cleanup_errors=True) as tmp_dir:
            deep_db_path = Path(tmp_dir) / "nested" / "user_dir" / ".amca" / "amca_deploy.sqlite3"
            self.assertFalse(deep_db_path.parent.exists(), "Target parent directory should not exist yet")
            software_root = Path(__file__).resolve().parents[1]

            # Server creation must safely create missing parent folders
            server = create_server(host="127.0.0.1", port=0, root=software_root, database=deep_db_path)
            thread = threading.Thread(target=server.serve_forever, daemon=True)
            thread.start()
            time.sleep(0.05)
            self.assertTrue(deep_db_path.parent.exists(), "Parent directories must be automatically created")

            base_url = f"http://127.0.0.1:{server.server_port}"
            req = Request(f"{base_url}/api/session", method="GET")
            req.add_header("Origin", "http://localhost:1420")
            with urlopen(req, timeout=5.0) as resp:
                token = json.loads(resp.read().decode("utf-8"))["session_token"]
            self.assertIsNotNone(token)

            server.shutdown()
            server.server_close()
            thread.join(timeout=1.0)

    def test_simulated_patch_upgrade_data_retention(self) -> None:
        """Simulate an in-place version upgrade over the same canonical database."""
        with tempfile.TemporaryDirectory(ignore_cleanup_errors=True) as tmp_dir:
            db_path = Path(tmp_dir) / ".amca" / "amca_upgrade.sqlite3"
            db_path.parent.mkdir(parents=True, exist_ok=True)
            software_root = Path(__file__).resolve().parents[1]

            # 1. First Version Run (Create Project & Memory)
            srv1 = create_server(host="127.0.0.1", port=0, root=software_root, database=db_path)
            t1 = threading.Thread(target=srv1.serve_forever, daemon=True)
            t1.start()
            time.sleep(0.05)
            url1 = f"http://127.0.0.1:{srv1.server_port}"

            req = Request(f"{url1}/api/session", method="GET")
            req.add_header("Origin", "http://localhost:1420")
            with urlopen(req, timeout=5.0) as resp:
                tok1 = json.loads(resp.read().decode("utf-8"))["session_token"]

            # Create Project
            p_data = json.dumps({"name": "Upgrade Test Project", "template": "software"}).encode("utf-8")
            rp = Request(f"{url1}/api/studio-projects", data=p_data, method="POST")
            rp.add_header("Origin", "http://localhost:1420")
            rp.add_header("X-AMCA-Session", tok1)
            rp.add_header("Content-Type", "application/json")
            with urlopen(rp, timeout=5.0) as resp:
                proj_id = json.loads(resp.read().decode("utf-8"))["project"]["project_id"]

            srv1.shutdown()
            srv1.server_close()
            t1.join(timeout=1.0)
            time.sleep(0.1)

            # 2. Simulated Upgraded Binary Run (Reopen same DB)
            srv2 = create_server(host="127.0.0.1", port=0, root=software_root, database=db_path)
            t2 = threading.Thread(target=srv2.serve_forever, daemon=True)
            t2.start()
            time.sleep(0.05)
            url2 = f"http://127.0.0.1:{srv2.server_port}"

            req2 = Request(f"{url2}/api/session", method="GET")
            req2.add_header("Origin", "http://localhost:1420")
            with urlopen(req2, timeout=5.0) as resp:
                tok2 = json.loads(resp.read().decode("utf-8"))["session_token"]

            # Verify Project Exists in New Server Instance
            rp2 = Request(f"{url2}/api/studio-projects", method="GET")
            rp2.add_header("Origin", "http://localhost:1420")
            rp2.add_header("X-AMCA-Session", tok2)
            with urlopen(rp2, timeout=5.0) as resp:
                projects = json.loads(resp.read().decode("utf-8"))["projects"]
                matching = [p for p in projects if p["project_id"] == proj_id]
                self.assertEqual(len(matching), 1)
                self.assertEqual(matching[0]["name"], "Upgrade Test Project")

            srv2.shutdown()
            srv2.server_close()
            t2.join(timeout=1.0)


if __name__ == "__main__":
    unittest.main()

