"""AMCA production-fix regression tests (audit BUG-1/2/3 + retry + public errors).

Covers the worker-team fixes that closed the Codex audit findings:
- artifact registry sync (image/zip removed, ArtifactError never KeyError)
- concurrent-edit integrity (loser leaves no trace, winner intact)
- rendition availability == export reality (incl. zip rows, truthful reasons)
- one bounded retry on transient 5xx (524/503 twice, 429/401 once)
- public model error text (no adapter names, codes, or secrets)

unittest style per repo convention (pytest fixtures are not used here;
each test builds its own tmp store via tempfile).
"""
from __future__ import annotations

import hashlib
import json
import tempfile
import threading
import unittest
import urllib.error
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

from amca.artifact_render import (
    ArtifactExporter,
    render_capabilities,
    rendition_available,
)
from amca.artifacts import ArtifactError, ArtifactStore


def make_store() -> tuple[ArtifactStore, tempfile.TemporaryDirectory[str]]:
    tmp = tempfile.TemporaryDirectory(prefix="amca-audit-fix-")
    root = Path(tmp.name)
    store = ArtifactStore(root / "amca.db", root / "payloads")
    return store, tmp


class ArtifactRegistryFixTests(unittest.TestCase):
    def test_image_and_zip_are_not_advertised_source_types(self) -> None:
        from amca.artifacts import ARTIFACT_TYPES

        self.assertNotIn("image", ARTIFACT_TYPES)
        self.assertNotIn("zip", ARTIFACT_TYPES)

    def test_removed_or_unknown_types_raise_artifact_error(self) -> None:
        store, tmp = make_store()
        try:
            for kind in ["image", "zip", "unknown", "IMAGE", ""]:
                with self.assertRaises(ArtifactError, msg=kind):
                    store.author(
                        project_id="audit",
                        artifact_type=kind,
                        title="audit",
                        content="test",
                        created_by="audit",
                    )
        finally:
            tmp.cleanup()

    def test_each_advertised_type_has_a_canonical_source(self) -> None:
        from amca.artifacts import ARTIFACT_TYPES, CANONICAL_SOURCE_EXT

        store, tmp = make_store()
        try:
            for kind in sorted(ARTIFACT_TYPES):
                self.assertIn(kind, CANONICAL_SOURCE_EXT)
                item = store.author(
                    project_id="audit",
                    artifact_type=kind,
                    title=f"audit-{kind}",
                    content="hello",
                    created_by="audit",
                )
                self.assertEqual(item["artifact_type"], kind)
        finally:
            tmp.cleanup()


class ConcurrentEditIntegrityFixTests(unittest.TestCase):
    def test_loser_leaves_no_trace_and_winner_stays_intact(self) -> None:
        store, tmp = make_store()
        try:
            item = store.author(
                project_id="audit",
                artifact_type="markdown",
                title="race",
                content="original",
                created_by="audit",
            )
            barrier = threading.Barrier(3, timeout=10)
            first_finished = threading.Event()
            original_path = store._payload_path

            def controlled_path(artifact_id: str, version: int, ext: str) -> Path:
                result = original_path(artifact_id, version, ext)
                if version == 2:
                    barrier.wait()
                return result

            store._payload_path = controlled_path  # type: ignore[method-assign]

            def edit(label: str, text: str) -> str:
                try:
                    store.edit(
                        artifact_id=item["artifact_id"],
                        content=text,
                        created_by="audit",
                        base_version=1,
                    )
                    return "success"
                except Exception as exc:
                    return type(exc).__name__
                finally:
                    if label == "audit-first":
                        first_finished.set()

            try:
                with ThreadPoolExecutor(max_workers=2) as pool:
                    first = pool.submit(edit, "audit-first", "first committed content")
                    second = pool.submit(
                        edit, "audit-second", "second rejected content"
                    )
                    barrier.wait(timeout=10)
                    first_finished.wait(10)
                    results = [first.result(timeout=15), second.result(timeout=15)]
            finally:
                store._payload_path = original_path  # type: ignore[method-assign]

            self.assertEqual(sorted(results), ["IntegrityError", "success"])
            source = store.read_source(item["artifact_id"], 2)
            actual = hashlib.sha256(source["content"].encode()).hexdigest()
            self.assertEqual(actual, source["provenance"]["sha256"])
            leftovers = list((Path(tmp.name) / "payloads").rglob("*.tmp-*"))
            self.assertEqual(leftovers, [])
        finally:
            tmp.cleanup()


class RenditionTruthfulnessFixTests(unittest.TestCase):
    def test_unimplemented_pairs_report_unavailable_even_when_library_imports(
        self,
    ) -> None:
        try:
            __import__("fitz")
        except ImportError:
            self.skipTest("PyMuPDF not installed")
        available, reason = rendition_available("markdown", "pdf")
        self.assertFalse(available)
        self.assertIn("not implemented", reason)

    def test_zip_advertised_available_for_every_source_type(self) -> None:
        from amca.artifacts import ARTIFACT_TYPES

        rows = {
            (row["artifact_type"], row["target_format"]): row
            for row in render_capabilities()
        }
        for kind in ARTIFACT_TYPES:
            row = rows.get((kind, "zip"))
            self.assertIsNotNone(row, f"missing zip row for {kind}")
            assert row is not None
            self.assertTrue(row["available"])

    def test_implemented_identity_and_html_renditions_advertised(self) -> None:
        rows = {
            (row["artifact_type"], row["target_format"]): row
            for row in render_capabilities()
        }
        for pair in [("bib", "bib"), ("json", "json"), ("markdown", "html")]:
            self.assertTrue(rows[pair]["available"])

    def test_export_and_availability_agree_on_zip(self) -> None:
        store, tmp = make_store()
        try:
            exporter = ArtifactExporter(store, Path(tmp.name) / "renditions")
            item = store.author(
                project_id="audit",
                artifact_type="markdown",
                title="zip-agree",
                content="# hi",
                created_by="audit",
            )
            available, _ = rendition_available("markdown", "zip")
            self.assertTrue(available)
            result = exporter.export(
                project_id="audit",
                artifact_id=item["artifact_id"],
                target_format="zip",
            )
            self.assertEqual(result["status"], "exported")
        finally:
            tmp.cleanup()

    def test_malformed_pptx_source_raises_artifact_error_not_value_error(
        self,
    ) -> None:
        try:
            __import__("pptx")
        except ImportError:
            self.skipTest("python-pptx not installed")
        store, tmp = make_store()
        try:
            exporter = ArtifactExporter(store, Path(tmp.name) / "renditions")
            item = store.author(
                project_id="audit",
                artifact_type="pptx",
                title="bad-deck",
                content="not json at all",
                created_by="audit",
            )
            with self.assertRaises(ArtifactError):
                exporter.export(
                    project_id="audit",
                    artifact_id=item["artifact_id"],
                    target_format="pptx",
                )
        finally:
            tmp.cleanup()


class TransientRetryFixTests(unittest.TestCase):
    def _adapter_with_fake_transport(
        self, codes: list[int | None]
    ):
        from amca.models.openai_compatible import OpenAICompatibleAdapter

        calls = {"count": 0}

        class FakeResponse:
            def __init__(self, payload: bytes) -> None:
                self._payload = payload

            def read(self) -> bytes:
                return self._payload

            def __enter__(self) -> FakeResponse:
                return self

            def __exit__(self, *exc: object) -> bool:
                return False

        def fake_urlopen(request: object, *, timeout: float = 0.0):
            calls["count"] += 1
            code = codes[min(calls["count"] - 1, len(codes) - 1)]
            if code is None:
                return FakeResponse(
                    json.dumps(
                        {"choices": [{"message": {"content": "ok"}}], "usage": {}}
                    ).encode()
                )
            raise urllib.error.HTTPError(
                url="http://127.0.0.1/", code=code, msg="err", hdrs=None, fp=None
            )

        adapter = OpenAICompatibleAdapter(
            provider="audit",
            model="audit-model",
            base_url="http://127.0.0.1/",
            api_key="audit-key",
            urlopen_fn=fake_urlopen,
            timeout=5.0,
            allow_remote=True,
        )
        return adapter, calls

    def test_transient_524_retried_once_then_succeeds(self) -> None:
        adapter, calls = self._adapter_with_fake_transport([524, None])
        result = adapter._post_json({"model": "m", "messages": []}, timeout=5.0)
        self.assertEqual(result["choices"][0]["message"]["content"], "ok")
        self.assertEqual(calls["count"], 2)

    def test_persistent_503_retried_once_then_unavailable(self) -> None:
        from amca.models.base import ModelProviderUnavailableError

        adapter, calls = self._adapter_with_fake_transport([503, 503])
        with self.assertRaises(ModelProviderUnavailableError):
            adapter._post_json({"model": "m", "messages": []}, timeout=5.0)
        self.assertEqual(calls["count"], 2)

    def test_non_transient_never_retried(self) -> None:
        from amca.models.base import ModelError

        for code in [401, 403, 400, 404, 422, 429]:
            adapter, calls = self._adapter_with_fake_transport([code, code])
            with self.assertRaises(ModelError, msg=f"code {code}"):
                adapter._post_json({"model": "m", "messages": []}, timeout=5.0)
            self.assertEqual(calls["count"], 1, f"code {code} was retried")


class PublicModelErrorFixTests(unittest.TestCase):
    def test_no_adapter_names_codes_or_secrets_leak(self) -> None:
        from amca.models.base import (
            ModelAuthenticationError,
            ModelInvalidRequestError,
            ModelProviderUnavailableError,
            ModelRateLimitError,
            ModelTimeoutError,
            ModelUnavailableError,
        )
        from amca.kernel import _public_model_error

        cases = [
            ModelAuthenticationError("OpenAICompatibleAdapter returned HTTP 401"),
            ModelRateLimitError("OpenAICompatibleAdapter rate limited (HTTP 429)"),
            ModelTimeoutError("OpenAICompatibleAdapter transport timeout"),
            ModelUnavailableError("provider is down"),
            ModelProviderUnavailableError(
                "OpenAICompatibleAdapter returned HTTP 524"
            ),
            ModelInvalidRequestError(
                "OpenAICompatibleAdapter rejected request (HTTP 422)"
            ),
            RuntimeError("something unexpected with sk-secret-fragment"),
            TimeoutError(""),
        ]
        for exc in cases:
            text = _public_model_error(exc)
            self.assertNotIn("OpenAICompatibleAdapter", text)
            self.assertNotIn("sk-secret-fragment", text)
            self.assertNotIn("401", text)
            self.assertNotIn("524", text)
            self.assertNotIn("429", text)

    def test_kernel_timeout_maps_to_timeout_text(self) -> None:
        import asyncio

        from amca.kernel import _public_model_error

        self.assertIn("did not answer in time", _public_model_error(TimeoutError()))
        self.assertIn(
            "did not answer in time", _public_model_error(asyncio.TimeoutError())
        )

    def test_cancellation_and_configuration_have_honest_text(self) -> None:
        from amca.models.base import ModelCancelledError, ModelConfigurationError
        from amca.kernel import _public_model_error

        self.assertIn(
            "cancelled", _public_model_error(ModelCancelledError("x")).lower()
        )
        config_text = _public_model_error(ModelConfigurationError("missing key"))
        self.assertIn("Settings", config_text)
