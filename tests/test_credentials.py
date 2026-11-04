"""Credential-store policy tests that never persist a real secret."""

from __future__ import annotations

import os
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from amca.credentials import (
    CredentialError,
    CredentialNotFound,
    CredentialStoreUnavailable,
    MemoryCredentialStore,
    ProviderCredentialResolver,
    ResilientCredentialStore,
    WindowsCredentialStore,
    WindowsDpapiCredentialStore,
    credential_target,
)


class CredentialStoreTests(unittest.TestCase):
    def test_targets_are_namespaced_and_reject_ambiguous_names(self):
        self.assertEqual(credential_target("OpenAI"), "AMCA/provider/openai")
        for invalid in ("", "../openai", "open ai", "provider/key", "x" * 65):
            with self.subTest(invalid=invalid), self.assertRaises(ValueError):
                credential_target(invalid)

    def test_memory_store_round_trip_hides_values_from_repr(self):
        store = MemoryCredentialStore()
        store.set("gemini", "secret-canary")
        self.assertEqual(store.get("gemini"), "secret-canary")
        self.assertNotIn("secret-canary", repr(store))
        self.assertTrue(store.delete("gemini"))
        self.assertFalse(store.delete("gemini"))
        with self.assertRaises(CredentialNotFound):
            store.get("gemini")

    def test_resolver_precedence_is_explicit_then_environment_then_store(self):
        store = MemoryCredentialStore()
        store.set("openai", "stored")
        resolver = ProviderCredentialResolver(store)
        with patch.dict(os.environ, {"OPENAI_API_KEY": "ephemeral"}, clear=True):
            self.assertEqual(
                resolver.resolve(
                    provider="openai",
                    environment_variable="OPENAI_API_KEY",
                    explicit="explicit",
                ),
                "explicit",
            )
            self.assertEqual(
                resolver.resolve(
                    provider="openai", environment_variable="OPENAI_API_KEY"
                ),
                "ephemeral",
            )
        with patch.dict(os.environ, {}, clear=True):
            self.assertEqual(
                resolver.resolve(
                    provider="openai", environment_variable="OPENAI_API_KEY"
                ),
                "stored",
            )

    def test_platform_availability_is_explicit(self):
        if os.name == "nt":
            self.assertTrue(WindowsCredentialStore.is_available())
        else:
            with self.assertRaises(CredentialStoreUnavailable):
                WindowsCredentialStore()

    def test_resilient_store_falls_back_without_exposing_secret(self):
        class FailingStore:
            def get(self, provider: str) -> str:
                raise CredentialError("primary unavailable")

            def set(self, provider: str, secret: str) -> None:
                raise CredentialError("primary unavailable")

            def delete(self, provider: str) -> bool:
                raise CredentialError("primary unavailable")

        fallback = MemoryCredentialStore()
        store = ResilientCredentialStore(FailingStore(), fallback)
        store.set("tabitoken.claude-opus-5", "fallback-canary")
        self.assertEqual(
            store.get("tabitoken.claude-opus-5"), "fallback-canary"
        )
        self.assertNotIn("fallback-canary", repr(store))
        self.assertTrue(store.delete("tabitoken.claude-opus-5"))

    @unittest.skipUnless(os.name == "nt", "Windows DPAPI test")
    def test_dpapi_file_round_trip_contains_no_plaintext(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "credentials.dpapi.json"
            store = WindowsDpapiCredentialStore(path)
            canary = "dpapi-plaintext-canary"
            store.set("tabitoken.claude-opus-4-8", canary)
            self.assertEqual(store.get("tabitoken.claude-opus-4-8"), canary)
            self.assertNotIn(canary, path.read_text(encoding="utf-8"))
            self.assertTrue(store.delete("tabitoken.claude-opus-4-8"))


if __name__ == "__main__":
    unittest.main()
