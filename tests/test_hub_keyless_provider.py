"""Round 27: keyless provider creation contract.

"+ Add provider" creates the card IMMEDIATELY, without a credential. The
provider must be honestly "not configured": nothing may treat it as usable
until the operator saves a key on the card.
"""

from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from amca.credentials import MemoryCredentialStore
from amca.service import ServiceContext, create_server


class KeylessProviderTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temporary = tempfile.TemporaryDirectory()
        root = Path(__file__).resolve().parents[1]
        self.server = create_server(
            host="127.0.0.1",
            port=0,
            root=root,
            database=Path(self.temporary.name) / "state.sqlite3",
            credential_store=MemoryCredentialStore(),
        )
        self.context = self.server.amca_context

    def tearDown(self) -> None:
        self.server.server_close()
        self.temporary.cleanup()

    def test_create_without_secret_is_configured_false(self) -> None:
        outcome = self.context.create_hub_provider(
            {"provider_kind": "custom", "name": "New provider", "endpoint": "", "secret": ""}
        )
        provider = outcome["provider"]
        self.assertFalse(provider["configured"])
        # No credential row was stored for it: reading one raises.
        with self.assertRaises(Exception):
            self.context.credential_store.get(f"model-hub.{provider['id']}")

    def test_whitespace_secret_counts_as_absent(self) -> None:
        outcome = self.context.create_hub_provider(
            {"provider_kind": "custom", "name": "Keyless", "endpoint": "", "secret": "   "}
        )
        self.assertFalse(outcome["provider"]["configured"])

    def test_secret_is_still_required_for_a_keyed_provider(self) -> None:
        outcome = self.context.create_hub_provider(
            {
                "provider_kind": "custom",
                "name": "Keyed",
                "endpoint": "https://api.example.com/v1",
                "secret": "sk-real-key",
            }
        )
        self.assertTrue(outcome["provider"]["configured"])
        stored = self.context.credential_store.get(
            f"model-hub.{outcome['provider']['id']}"
        )
        self.assertEqual(stored, "sk-real-key")


if __name__ == "__main__":  # pragma: no cover
    unittest.main()
