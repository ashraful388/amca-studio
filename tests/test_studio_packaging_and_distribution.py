"""
Packaging, Installer & Portable Distribution Validation Suite for AMCA Studio
Validates:
1. Tauri configuration integrity, NSIS packaging targets & CSP policies
2. Portable directory structure, frontend dist assets, and bundled icons
3. Upgrade path safety & user database state preservation across simulated version upgrades
4. Security and origin boundaries across packaging surfaces
"""

import json
import os
import shutil
import sqlite3
import tempfile
import threading
import time
import unittest
from pathlib import Path
from urllib.error import HTTPError
from urllib.request import Request, urlopen

from amca.service import create_server


class StudioPackagingAndDistributionTests(unittest.TestCase):
    def setUp(self) -> None:
        self.repo_root = Path(__file__).resolve().parents[2]
        self.software_root = Path(__file__).resolve().parents[1]

    # -------------------------------------------------------------------------
    # 1. Tauri Configuration & Packaging Target Audit
    # -------------------------------------------------------------------------
    def test_tauri_conf_packaging_configuration(self) -> None:
        """Verify that tauri.conf.json contains valid packaging and security configurations."""
        tauri_conf_path = self.software_root / "desktop-ui" / "src-tauri" / "tauri.conf.json"
        self.assertTrue(tauri_conf_path.exists(), f"tauri.conf.json not found at {tauri_conf_path}")

        with open(tauri_conf_path, "r", encoding="utf-8") as f:
            conf = json.load(f)

        # H2B.2 Part B: authorized v1.0.1 release identity. productName was
        # aligned "AMCA" -> "AMCA Studio" by the H2A RC convergence (768b66f8);
        # assertion strength unchanged - exact match.
        self.assertEqual(conf.get("productName"), "AMCA Studio")
        self.assertEqual(conf.get("identifier"), "ai.amca.desktop")
        
        # Verify bundle targets
        bundle = conf.get("bundle", {})
        self.assertTrue(bundle.get("active"))
        targets = bundle.get("targets", [])
        self.assertIn("nsis", targets)

        # Verify Windows NSIS hooks configuration
        win_conf = bundle.get("windows", {})
        self.assertIn("nsis", win_conf)
        self.assertIn("installerHooks", win_conf.get("nsis", {}))

        # Verify Content Security Policy (CSP)
        csp = conf.get("app", {}).get("security", {}).get("csp", "")
        self.assertIn("default-src 'self'", csp)
        self.assertIn("connect-src", csp)
        self.assertIn("127.0.0.1", csp)

    # -------------------------------------------------------------------------
    # 2. Frontend Dist Asset Availability for Bundling
    # -------------------------------------------------------------------------
    def test_frontend_dist_bundle_availability(self) -> None:
        """Verify that frontend dist contains valid index.html and compiled javascript chunks."""
        dist_path = self.software_root / "desktop-ui" / "dist"
        self.assertTrue(dist_path.exists(), f"Frontend dist directory missing at {dist_path}")
        
        index_html = dist_path / "index.html"
        self.assertTrue(index_html.exists(), "dist/index.html missing")

        assets_dir = dist_path / "assets"
        self.assertTrue(assets_dir.exists(), "dist/assets missing")
        
        # Check that compiled js and css assets are present
        js_files = list(assets_dir.glob("*.js"))
        css_files = list(assets_dir.glob("*.css"))
        self.assertGreater(len(js_files), 0, "No JS files found in dist/assets")
        self.assertGreater(len(css_files), 0, "No CSS files found in dist/assets")

    # -------------------------------------------------------------------------
    # 3. Upgrade Path & Database State Preservation
    # -------------------------------------------------------------------------
    def test_simulated_version_upgrade_preserves_user_data(self) -> None:
        """Simulate an application version upgrade: user data in SQLite must remain 100% intact."""
        with tempfile.TemporaryDirectory() as tmp_dir:
            shared_db_path = Path(tmp_dir) / "appdata" / "amca.sqlite3"
            shared_db_path.parent.mkdir(parents=True, exist_ok=True)

            # Step 1: Launch "v0.5.1" server instance and populate state
            v1_server = create_server(host="127.0.0.1", port=0, root=self.software_root, database=shared_db_path)
            v1_thread = threading.Thread(target=v1_server.serve_forever, daemon=True)
            v1_thread.start()
            time.sleep(0.05)
            v1_url = f"http://127.0.0.1:{v1_server.server_port}"

            # Get session token
            req = Request(f"{v1_url}/api/session", method="GET")
            req.add_header("Origin", "http://localhost:1420")
            with urlopen(req) as resp:
                v1_token = json.loads(resp.read().decode("utf-8"))["session_token"]

            # Create project
            req_proj = Request(
                f"{v1_url}/api/studio-projects",
                data=json.dumps({"name": "Upgrade Test Project", "template": "software"}).encode("utf-8"),
                headers={"Content-Type": "application/json", "Origin": "http://localhost:1420", "X-AMCA-Session": v1_token},
                method="POST"
            )
            with urlopen(req_proj) as resp:
                proj_data = json.loads(resp.read().decode("utf-8"))
                proj_id = proj_data["project"]["project_id"]

            # Create artifact
            req_art = Request(
                f"{v1_url}/api/projects/{proj_id}/artifacts",
                data=json.dumps({"title": "Upgrade Spec", "artifact_type": "markdown", "content": "Before Upgrade", "created_by": "qa"}).encode("utf-8"),
                headers={"Content-Type": "application/json", "Origin": "http://localhost:1420", "X-AMCA-Session": v1_token},
                method="POST"
            )
            with urlopen(req_art) as resp:
                art_data = json.loads(resp.read().decode("utf-8"))
                art_id = art_data["artifact"]["artifact_id"]

            # Gracefully shutdown v1 server
            v1_server.shutdown()
            v1_server.server_close()
            v1_thread.join(timeout=1.0)

            # Step 2: Launch "v0.6.0" upgraded server instance against exact same database
            v2_server = create_server(host="127.0.0.1", port=0, root=self.software_root, database=shared_db_path)
            v2_thread = threading.Thread(target=v2_server.serve_forever, daemon=True)
            v2_thread.start()
            time.sleep(0.05)
            v2_url = f"http://127.0.0.1:{v2_server.server_port}"

            req2 = Request(f"{v2_url}/api/session", method="GET")
            req2.add_header("Origin", "http://localhost:1420")
            with urlopen(req2) as resp:
                v2_token = json.loads(resp.read().decode("utf-8"))["session_token"]

            try:
                # Query project
                req_chk_proj = Request(f"{v2_url}/api/studio-projects/{proj_id}", headers={"Origin": "http://localhost:1420", "X-AMCA-Session": v2_token})
                with urlopen(req_chk_proj) as resp:
                    chk_proj = json.loads(resp.read().decode("utf-8"))
                    self.assertEqual(chk_proj["name"], "Upgrade Test Project")

                # Query artifact
                req_chk_art = Request(f"{v2_url}/api/studio/artifacts/{art_id}?project_id={proj_id}", headers={"Origin": "http://localhost:1420", "X-AMCA-Session": v2_token})
                with urlopen(req_chk_art) as resp:
                    chk_art = json.loads(resp.read().decode("utf-8"))
                    self.assertEqual(chk_art["artifact"]["title"], "Upgrade Spec")
            finally:
                v2_server.shutdown()
                v2_server.server_close()
                v2_thread.join(timeout=1.0)

    # -------------------------------------------------------------------------
    # 4. Lifecycle Audit Log Persistence
    # -------------------------------------------------------------------------
    def test_lifecycle_audit_log_created(self) -> None:
        """Verify that server startup and shutdown record entries into background-lifecycle.jsonl."""
        with tempfile.TemporaryDirectory() as tmp_dir:
            db_path = Path(tmp_dir) / "amca_audit.db"
            audit_file = Path(tmp_dir) / "background-lifecycle.jsonl"

            server = create_server(host="127.0.0.1", port=0, root=self.software_root, database=db_path)
            thread = threading.Thread(target=server.serve_forever, daemon=True)
            thread.start()
            time.sleep(0.05)

            # Shutdown server
            server.shutdown()
            server.server_close()
            thread.join(timeout=1.0)

            # Check audit log existence and content
            self.assertTrue(audit_file.exists(), "background-lifecycle.jsonl was not created")
            with open(audit_file, "r", encoding="utf-8") as f:
                lines = [json.loads(line) for line in f if line.strip()]

            events = [entry.get("event") for entry in lines]
            self.assertIn("backend_started", events)


if __name__ == "__main__":
    unittest.main()

