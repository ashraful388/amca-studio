from __future__ import annotations

import json
import sqlite3
import tempfile
import unittest
from dataclasses import replace
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch
from urllib.request import Request

from amca.hardware import LocalComputeProfile
from amca.local_runtime import (
    LocalModelMeasurement,
    LocalModelRecord,
    LocalRuntimeStore,
    _RejectRedirects,
    _urlopen_without_redirects,
    discover_ollama_models,
    hardware_profile_sha256,
    measure_ollama_model,
)


class _Response:
    def __init__(self, payload: object) -> None:
        self.payload = json.dumps(payload).encode("utf-8")

    def __enter__(self) -> "_Response":
        return self

    def __exit__(self, *_: object) -> None:
        return None

    def read(self) -> bytes:
        return self.payload


def _profile() -> LocalComputeProfile:
    return LocalComputeProfile(
        operating_system="TestOS",
        architecture="x86_64",
        cpu="test-cpu",
        logical_cores=8,
        total_ram_bytes=32 * 1024**3,
        available_ram_bytes=16 * 1024**3,
        disk_path="C:/test",
        free_disk_bytes=100 * 1024**3,
        recommended_local_tier="cpu_medium_quantized",
    )


class LocalRuntimeTests(unittest.TestCase):
    def test_default_transport_rejects_redirects(self) -> None:
        request = Request("http://127.0.0.1:11434/api/tags")
        opener = SimpleNamespace(open=lambda value, timeout: (value, timeout))
        with patch("amca.local_runtime.build_opener", return_value=opener) as build:
            result = _urlopen_without_redirects(request, timeout=3.0)

        handler = build.call_args.args[0]
        self.assertIsInstance(handler, _RejectRedirects)
        self.assertIsNone(
            handler.redirect_request(
                request,
                None,
                302,
                "Found",
                {},
                "https://example.com/collect",
            )
        )
        self.assertEqual(result, (request, 3.0))

    def test_discovery_and_measurement_preserve_injected_transport(self) -> None:
        seen: list[str] = []

        def opener(request: object, *, timeout: float) -> _Response:
            seen.append(request.full_url)  # type: ignore[attr-defined]
            if request.full_url.endswith("/api/tags"):  # type: ignore[attr-defined]
                return _Response({"models": []})
            return _Response({"response": "READY", "done": True})

        self.assertEqual(discover_ollama_models(urlopen_fn=opener), ())
        measurement = measure_ollama_model("local", _profile(), urlopen_fn=opener)

        self.assertTrue(measurement.success)
        self.assertEqual(
            seen,
            [
                "http://127.0.0.1:11434/api/tags",
                "http://127.0.0.1:11434/api/generate",
            ],
        )

    def test_hardware_identity_ignores_volatile_free_capacity(self) -> None:
        first = _profile()
        second = replace(
            first,
            available_ram_bytes=first.available_ram_bytes - 1024,
            free_disk_bytes=first.free_disk_bytes - 1024,
        )
        self.assertEqual(
            hardware_profile_sha256(first), hardware_profile_sha256(second)
        )

    def test_inventory_refresh_marks_removed_models_and_persists_profile(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            database = Path(directory) / "amca.sqlite3"
            with LocalRuntimeStore(database) as store:
                profile_sha = store.save_hardware_profile(_profile())
                first = (
                    LocalModelRecord("ollama", "one:1b", "sha256:a", 100),
                    LocalModelRecord("ollama", "two:3b", "sha256:b", 200),
                )
                store.replace_inventory("ollama", first, observed_at="2026-01-01T00:00:00Z")
                store.replace_inventory(
                    "ollama", (first[1],), observed_at="2026-01-02T00:00:00Z"
                )

                self.assertEqual([item.model for item in store.list_models()], ["two:3b"])
                historical = store.list_models(present_only=False)
                self.assertFalse(next(item for item in historical if item.model == "one:1b").present)
                snapshot = store.snapshot()

            self.assertEqual(snapshot.hardware_profile_sha256, profile_sha)
            self.assertEqual(snapshot.hardware_profile.cpu, "test-cpu")

    def test_discovery_parses_only_loopback_inventory(self) -> None:
        seen: list[str] = []

        def opener(request: object, *, timeout: float) -> _Response:
            seen.append(request.full_url)  # type: ignore[attr-defined]
            self.assertEqual(timeout, 2)
            return _Response(
                {
                    "models": [
                        {
                            "name": "qwen3:1.7b",
                            "digest": "sha256:abc",
                            "size": 1_400_000_000,
                            "modified_at": "2026-08-14T00:00:00Z",
                            "details": {
                                "family": "qwen3",
                                "parameter_size": "1.7B",
                                "quantization_level": "Q4_K_M",
                                "format": "gguf",
                            },
                        },
                        {
                            "name": "remote:120b-cloud",
                            "digest": "sha256:remote",
                            "size": 384,
                            "details": {
                                "family": "remote",
                                "parameter_size": "120B",
                                "quantization_level": "MXFP4",
                                "format": "",
                            },
                        },
                    ]
                }
            )

        models = discover_ollama_models(timeout=2, urlopen_fn=opener)

        self.assertEqual(seen, ["http://127.0.0.1:11434/api/tags"])
        self.assertEqual(models[0].parameter_size, "1.7B")
        self.assertEqual(models[0].quantization_level, "Q4_K_M")
        self.assertEqual([model.model for model in models], ["qwen3:1.7b"])
        with self.assertRaises(ValueError):
            discover_ollama_models("https://example.com", urlopen_fn=opener)

    def test_measurement_persists_hashes_not_prompt_or_response(self) -> None:
        prompt = "private measurement prompt canary"
        response = "private response canary"

        def opener(request: object, *, timeout: float) -> _Response:
            self.assertEqual(request.full_url, "http://127.0.0.1:11434/api/generate")  # type: ignore[attr-defined]
            self.assertLessEqual(timeout, 120)
            return _Response(
                {
                    "response": response,
                    "done": True,
                    "prompt_eval_count": 8,
                    "eval_count": 4,
                    "eval_duration": 2_000_000_000,
                }
            )

        measurement = measure_ollama_model(
            "qwen3:1.7b", _profile(), prompt=prompt, urlopen_fn=opener
        )
        self.assertTrue(measurement.success)
        self.assertEqual(measurement.tokens_per_second, 2.0)

        with tempfile.TemporaryDirectory() as directory:
            database = Path(directory) / "amca.sqlite3"
            with LocalRuntimeStore(database) as store:
                store.record_measurement(measurement)
                restored = store.list_measurements()[0]
            connection = sqlite3.connect(database)
            try:
                raw = connection.execute(
                    "SELECT prompt_sha256, response_sha256 FROM local_model_measurements"
                ).fetchone()
            finally:
                connection.close()
            database_bytes = database.read_bytes()

        self.assertEqual(restored.to_dict(), measurement.to_dict())
        self.assertEqual(len(raw[0]), 64)
        self.assertNotIn(prompt.encode(), database_bytes)
        self.assertNotIn(response.encode(), database_bytes)

    def test_routing_rank_prefers_current_measured_installed_model(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            with LocalRuntimeStore(Path(directory) / "amca.sqlite3") as store:
                profile_sha = store.save_hardware_profile(_profile())
                store.replace_inventory(
                    "ollama",
                    (
                        LocalModelRecord("ollama", "fast", "a", 200),
                        LocalModelRecord("ollama", "slow", "b", 40 * 1024**3),
                    ),
                )
                store.record_measurement(
                    LocalModelMeasurement(
                        id="m1",
                        provider="ollama",
                        model="fast",
                        hardware_profile_sha256=profile_sha,
                        prompt_sha256="c" * 64,
                        started_at="2026-01-01T00:00:00Z",
                        completed_at="2026-01-01T00:00:01Z",
                        wall_seconds=1,
                        prompt_tokens=2,
                        output_tokens=10,
                        tokens_per_second=10,
                        success=True,
                        response_sha256="d" * 64,
                    )
                )
                snapshot = store.snapshot()

        self.assertLess(
            snapshot.routing_rank("ollama", "fast"),
            snapshot.routing_rank("ollama", "slow"),
        )
        self.assertEqual(snapshot.fit_penalty("ollama", "fast"), 0)
        self.assertEqual(snapshot.fit_penalty("ollama", "slow"), 2)


if __name__ == "__main__":
    unittest.main()
