from __future__ import annotations

import asyncio
import tempfile
import unittest
from pathlib import Path

from amca.domain import ProjectState
from amca.models.base import ModelRequest, StreamChunk
from amca.models.scripted import ScriptedAdapter
from amca.permissions import ApprovalStatus, PermissionStore
from amca.privacy import (
    PrivateHybridAdapter,
    TransmissionStore,
    request_transmission_approval,
)
from amca.storage import SQLiteStore


class _RecordingAdapter(ScriptedAdapter):
    def __init__(self, scripts: list[str]) -> None:
        super().__init__(
            scripts,
            adapter_id="cloud-reviewer",
            provider="example-cloud",
            model="review-model",
        )
        self.requests: list[ModelRequest] = []

    async def generate(self, request: ModelRequest):  # type: ignore[no-untyped-def]
        self.requests.append(request)
        return await super().generate(request)


class _BlockingStreamAdapter(_RecordingAdapter):
    def __init__(self) -> None:
        super().__init__(["unused"])
        self.closed = False

    async def stream(self, request: ModelRequest):  # type: ignore[no-untyped-def]
        self.requests.append(request)
        try:
            async with self._call_scope(request):
                yield StreamChunk(
                    delta="first",
                    model=self.model,
                    provider=self.provider,
                )
                await asyncio.Event().wait()
        finally:
            self.closed = True


class PrivateHybridTests(unittest.IsolatedAsyncioTestCase):
    def _database(self, directory: str) -> Path:
        database = Path(directory) / "amca.sqlite3"
        with SQLiteStore(database) as store:
            store.save_state(ProjectState(project_id="private-project", goal="private task"))
        return database

    async def test_exact_approval_transmits_only_reviewed_excerpt_and_audits_hashes(self) -> None:
        excerpt = "Reviewed fact A and bounded question B."
        response = "bounded cloud answer"
        hidden_canary = "FULL_PRIVATE_CONTEXT_MUST_NOT_LEAVE"
        with tempfile.TemporaryDirectory() as directory:
            database = self._database(directory)
            delegate = _RecordingAdapter([response])
            with PermissionStore(database) as permissions:
                approval = request_transmission_approval(
                    permissions,
                    project_id="private-project",
                    adapter_id=delegate.adapter_id,
                    provider=delegate.provider,
                    model=delegate.model,
                    excerpt=excerpt,
                )
                permissions.decide(approval.id, approve=True)

            guarded = PrivateHybridAdapter(
                delegate,
                project_id="private-project",
                database=database,
                excerpt=excerpt,
                approval_ids=[approval.id],
            )
            result = await guarded.generate(
                ModelRequest(
                    messages=hidden_canary,
                    system="PRIVATE_DYNAMIC_SYSTEM",
                    tools=({"type": "function", "private": hidden_canary},),
                    metadata={"private_context": hidden_canary},
                    extra={"private_option": hidden_canary},
                    max_output_tokens=9999,
                )
            )

            self.assertEqual(result.text, response)
            self.assertEqual(len(delegate.requests), 1)
            transmitted = delegate.requests[0]
            serialized = delegate._request_text(transmitted)
            self.assertIn(excerpt, serialized)
            self.assertNotIn(hidden_canary, serialized)
            self.assertNotIn("PRIVATE_DYNAMIC_SYSTEM", serialized)
            self.assertFalse(transmitted.tools)
            self.assertFalse(transmitted.metadata)
            self.assertFalse(transmitted.extra)
            self.assertEqual(transmitted.max_output_tokens, 1800)

            with PermissionStore(database) as permissions:
                consumed = permissions.get(approval.id)
            with TransmissionStore(database) as transmissions:
                rows = transmissions.list(project_id="private-project")
            raw = database.read_bytes()

        self.assertIsNotNone(consumed)
        self.assertIs(consumed.status, ApprovalStatus.CONSUMED)
        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0].status, "succeeded")
        self.assertEqual(len(rows[0].excerpt_sha256), 64)
        self.assertEqual(len(rows[0].response_sha256 or ""), 64)
        self.assertNotIn(excerpt.encode(), raw)
        self.assertNotIn(response.encode(), raw)
        self.assertNotIn(hidden_canary.encode(), raw)

    async def test_missing_or_wrong_scope_approval_blocks_before_delegate(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            database = self._database(directory)
            delegate = _RecordingAdapter(["must not execute"])
            with PermissionStore(database) as permissions:
                approval = request_transmission_approval(
                    permissions,
                    project_id="private-project",
                    adapter_id=delegate.adapter_id,
                    provider=delegate.provider,
                    model=delegate.model,
                    excerpt="approved excerpt",
                )
                permissions.decide(approval.id, approve=True)

            guarded = PrivateHybridAdapter(
                delegate,
                project_id="private-project",
                database=database,
                excerpt="different excerpt",
                approval_ids=[approval.id],
            )
            with self.assertRaises(PermissionError):
                await guarded.generate(ModelRequest(messages="ignored private context"))

            with PermissionStore(database) as permissions:
                untouched = permissions.get(approval.id)
            with TransmissionStore(database) as transmissions:
                rows = transmissions.list(project_id="private-project")

        self.assertEqual(delegate.calls_consumed, 0)
        self.assertIs(untouched.status, ApprovalStatus.APPROVED)
        self.assertEqual(rows, ())

    async def test_approval_use_limit_is_enforced_before_second_network_call(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            database = self._database(directory)
            delegate = _RecordingAdapter(["first", "second"])
            with PermissionStore(database) as permissions:
                approval = request_transmission_approval(
                    permissions,
                    project_id="private-project",
                    adapter_id=delegate.adapter_id,
                    provider=delegate.provider,
                    model=delegate.model,
                    excerpt="one bounded excerpt",
                    max_uses=1,
                )
                permissions.decide(approval.id, approve=True)

            guarded = PrivateHybridAdapter(
                delegate,
                project_id="private-project",
                database=database,
                excerpt="one bounded excerpt",
                approval_ids=[approval.id],
            )
            await guarded.generate(ModelRequest(messages="first private prompt"))
            with self.assertRaises(PermissionError):
                await guarded.generate(ModelRequest(messages="second private prompt"))

        self.assertEqual(delegate.calls_consumed, 1)

    async def test_cancelled_private_stream_closes_delegate_and_audit(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            database = self._database(directory)
            delegate = _BlockingStreamAdapter()
            with PermissionStore(database) as permissions:
                approval = request_transmission_approval(
                    permissions,
                    project_id="private-project",
                    adapter_id=delegate.adapter_id,
                    provider=delegate.provider,
                    model=delegate.model,
                    excerpt="approved excerpt",
                    max_output_tokens=128,
                )
                permissions.decide(approval.id, approve=True)
            guarded = PrivateHybridAdapter(
                delegate,
                project_id="private-project",
                database=database,
                excerpt="approved excerpt",
                approval_ids=[approval.id],
                max_output_tokens=128,
            )
            request = ModelRequest.from_prompt(
                "ignored", request_id="cancel-private-stream"
            )
            stream = guarded.stream(request)
            first_delta = asyncio.Event()
            hold = asyncio.Event()

            async def consume() -> None:
                async for chunk in stream:
                    if chunk.delta:
                        first_delta.set()
                        await hold.wait()

            task = asyncio.create_task(consume())
            await asyncio.wait_for(first_delta.wait(), timeout=0.2)
            self.assertTrue(await guarded.cancel(request.request_id))
            with self.assertRaises(asyncio.CancelledError):
                await task
            await asyncio.wait_for(stream.aclose(), timeout=0.2)
            with TransmissionStore(database) as transmissions:
                rows = transmissions.list(project_id="private-project")

        self.assertTrue(delegate.closed)
        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0].status, "failed")


if __name__ == "__main__":
    unittest.main()
