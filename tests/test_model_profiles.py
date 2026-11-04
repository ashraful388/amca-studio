from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from amca.model_profiles import BUILTIN_PROFILES, ModelProfileStore


class ModelProfileStoreTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temporary = tempfile.TemporaryDirectory()
        self.database = Path(self.temporary.name) / "amca.sqlite3"
        self.store = ModelProfileStore(self.database)

    def tearDown(self) -> None:
        self.temporary.cleanup()

    def test_builtins_are_seeded_idempotently(self) -> None:
        ModelProfileStore(self.database)
        profiles = self.store.list()
        self.assertEqual(len(profiles), len(BUILTIN_PROFILES))
        self.assertTrue(all(profile.is_builtin for profile in profiles))

    def test_custom_profile_round_trips_without_secret_field(self) -> None:
        profile = self.store.create(
            {
                "provider_kind": "openai_compatible",
                "label": "Groq Llama",
                "model": "llama-3.1-8b-instant",
                "endpoint": "https://api.groq.com/openai/v1/",
                "pricing_basis": "free",
                "secret": "ignored-by-metadata-store",
            }
        )
        restored = ModelProfileStore(self.database).get(profile.profile_id)
        self.assertEqual(restored, profile)
        self.assertNotIn(b"ignored-by-metadata-store", self.database.read_bytes())
        self.assertNotIn("secret", profile.to_public_dict())

    def test_rejects_unsafe_endpoint_and_invalid_pricing(self) -> None:
        common = {
            "provider_kind": "openai",
            "label": "Unsafe",
            "model": "gpt-test",
            "pricing_basis": "token",
            "input_cost_per_million": 1,
        }
        with self.assertRaisesRegex(ValueError, "HTTPS"):
            self.store.create({**common, "endpoint": "http://api.example/v1"})
        with self.assertRaisesRegex(ValueError, "credentials"):
            self.store.create(
                {**common, "endpoint": "https://user:pass@api.example/v1"}
            )
        with self.assertRaisesRegex(ValueError, "positive"):
            self.store.create(
                {
                    **common,
                    "endpoint": "https://api.example/v1",
                    "input_cost_per_million": 0,
                }
            )
        with self.assertRaisesRegex(ValueError, "token or declared-free"):
            self.store.create(
                {
                    "provider_kind": "anthropic",
                    "label": "Wrong protocol pricing",
                    "model": "claude-test",
                    "endpoint": "https://api.anthropic.com",
                    "pricing_basis": "request",
                    "price_per_request_usd": 1,
                }
            )
        with self.assertRaisesRegex(ValueError, "Tabitoken"):
            self.store.create(
                {
                    "provider_kind": "tabitoken",
                    "label": "Wrong Tabitoken pricing",
                    "model": "claude-test",
                    "endpoint": "https://tabitoken.com/v1",
                    "pricing_basis": "free",
                }
            )

    def test_custom_delete_and_builtin_delete_guard(self) -> None:
        custom = self.store.create(
            {
                "provider_kind": "kimi",
                "label": "Kimi Test",
                "model": "kimi-k2.5",
                "endpoint": "https://api.moonshot.ai/v1",
                "pricing_basis": "free",
            }
        )
        self.assertEqual(self.store.delete(custom.profile_id), custom)
        self.assertIsNone(self.store.get(custom.profile_id))
        with self.assertRaisesRegex(ValueError, "built-in"):
            self.store.delete(BUILTIN_PROFILES[0].profile_id)


if __name__ == "__main__":
    unittest.main()
