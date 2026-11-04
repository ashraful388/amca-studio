"""R2.2 local semantic + hybrid retrieval acceptance tests.

Maps the manager-reply58 required list: deterministic offline embeddings,
stable embedding identity, bounded binary vectors, idempotent indexing,
stale-vector exclusion, project/session/kind isolation, cosine semantics,
RRF hybrid fusion with per-path provenance, explicit evidence pinning,
restart persistence, derived-vector deletion, truthful degradation without
a backend, orchestrator SEARCH_LOCAL mode journaling, canonical amca.db and
zero network / zero paid calls.
"""

from __future__ import annotations

import math
import tempfile
import unittest
from pathlib import Path

from amca.research_ingest import ResearchIngestor
from amca.research_orchestrator import (
    DeterministicResearchPlanner,
    ReasonCode,
    ResearchAction,
    ResearchLimits,
    ResearchOrchestrator,
    ResearchRunStore,
    RunStatus,
)
from amca.research_semantic import (
    DEFAULT_RRF_K,
    MAX_CHARS_PER_ITEM,
    EmbeddingError,
    EmbeddingProvider,
    HybridLocalSearch,
    RetrievalMode,
    SemanticBackendUnavailable,
    SemanticRetrievalStore,
    SemanticSearchResult,
    cosine_similarity,
    decode_vector,
    DeterministicEmbeddingProvider,
    encode_vector,
    evidence_from_retrieval_hit,
    hybrid_fuse,
)
from amca.research_source import ResearchStore
from amca.research_synthesis import ClaimType, ResearchSynthesisStore
from amca.tool_fabric import ToolFabric, ToolFabricStore
from amca.web_acquisition import NetworkToolPolicy, WebAcquisitionStore
from test_research_orchestrator import ScriptedPlanner


def _corpus_text(name: str) -> str:
    return {
        "battery": "quantum battery efficiency reached forty two percent "
                   "under laboratory conditions",
        "solar": "solar panel degradation depends on ultraviolet exposure "
                 "and thermal cycling over the years",
        "tea": "the ceremonial tea preparation requires precise water "
               "temperature and timing",
    }[name]


class SemanticBase(unittest.TestCase):
    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.root = Path(self._tmp.name)
        self.db = self.root / "amca.db"
        self.fabric = ToolFabric(ToolFabricStore(self.db),
                                 project_root="projA")
        NetworkToolPolicy.register(self.fabric)
        self.research = ResearchStore(self.db,
                                      storage_root=self.root / "content")
        self.synthesis = ResearchSynthesisStore(
            self.db, storage_root=self.root / "content")
        self.ingestor = ResearchIngestor(self.research)
        self.runs = ResearchRunStore(self.db)
        self.acq_store = WebAcquisitionStore(self.db)
        self.session_id = self.research.create_session(
            project_id="projA", title="r22", purpose="semantic")[
            "session_id"]
        self.provider = DeterministicEmbeddingProvider(dimension=64)
        self.store = SemanticRetrievalStore(self.db)

    def seed_segment(self, text: str, *, project_id: str = "projA",
                     source_kind: str = "user_supplied") -> str:
        """Create a real authoritative segment through the accepted R1.2
        path and return its id."""
        session_id = self.session_id if project_id == "projA" else \
            self.research.create_session(project_id=project_id, title="x",
                                         purpose="isolation")["session_id"]
        created = self.ingestor.ingest_user_content(
            session_id=session_id, project_id=project_id,
            title=f"doc-{abs(hash(text)) % 10**8}", text=text)
        extraction = self.ingestor.extract(
            created["snapshot"]["snapshot_id"], project_id=project_id)
        with self.research._connect() as connection:
            row = connection.execute(
                "SELECT segment_id FROM research_extracted_segments "
                "WHERE extraction_id = ? ORDER BY seq LIMIT 1",
                (extraction["extraction_id"],)).fetchone()
        return row["segment_id"]

    def index_all(self) -> dict:
        return self.store.index_segments(
            self.research, self.provider, project_id="projA")


class ProviderAndIdentityTests(SemanticBase):
    def test_1_deterministic_provider_stable(self) -> None:
        other = DeterministicEmbeddingProvider(dimension=64)
        a = self.provider.embed_texts(["alpha beta"])[0]
        b = other.embed_texts(["alpha beta"])[0]
        self.assertEqual(a, b)

    def test_3_dimension_recorded_and_rejected_on_mismatch(self) -> None:
        self.seed_segment(_corpus_text("battery"))
        summary = self.index_all()
        self.assertGreater(summary["indexed"], 0)
        with self.store._connect() as connection:
            dims = {r["dimension"] for r in connection.execute(
                "SELECT dimension FROM research_embeddings").fetchall()}
        self.assertEqual(dims, {64})
        # A different embedding space must never see these vectors.
        with self.assertRaises(EmbeddingError):
            self.store.semantic_search(
                self.research, DeterministicEmbeddingProvider(dimension=32),
                project_id="projA", query="anything")

    def test_4_incompatible_dimensions_rejected_at_codec(self) -> None:
        vector = [0.5] * 8
        blob, digest = encode_vector(vector)
        with self.assertRaises(EmbeddingError):
            decode_vector(blob, 16, digest)

    def test_5_non_finite_vectors_rejected(self) -> None:
        for bad in ([float("nan")] * 4, [float("inf")] * 4,
                    [float("-inf")] * 4):
            with self.assertRaises(EmbeddingError):
                encode_vector(bad)

    def test_6_embedding_identity_links_digest_and_provenance(self) -> None:
        sid = self.seed_segment(_corpus_text("battery"))
        self.index_all()
        hits = self.store.semantic_search(
            self.research, self.provider, project_id="projA",
            query="battery efficiency", limit=5)
        hit = next(h for h in hits if h.segment_id == sid)
        self.assertEqual(hit.source_id.startswith("src-"), True)
        self.assertTrue(hit.snapshot_id)
        self.assertTrue(hit.extraction_id)
        self.assertIn("quantum battery", hit.preview)
        prov = hit.embedding_provenance
        self.assertEqual(prov["provider_id"], "deterministic-local")
        self.assertEqual(prov["model_id"], "hashed-bof")
        self.assertEqual(prov["dimension"], 64)
        self.assertEqual(prov["metric"], "cosine")
        self.assertEqual(prov["similarity_label"], "retrieval_similarity")

    def test_non_local_provider_refused(self) -> None:
        class Cloudy(EmbeddingProvider):
            provider_id = "cloudy"
            version = "1"
            model_id = "cloud-model"
            dimension = 8
            is_local = False

            def embed_texts(self, texts):
                return [[0.0] * 8 for _ in texts]

        with self.assertRaises(EmbeddingError):
            self.store.index_segments(self.research, Cloudy(),
                                      project_id="projA")


class IndexingTests(SemanticBase):
    def test_7_duplicate_indexing_idempotent(self) -> None:
        self.seed_segment(_corpus_text("battery"))
        first = self.index_all()
        second = self.index_all()
        self.assertGreaterEqual(first["indexed"], 1)
        self.assertEqual(second["indexed"], first["indexed"])
        with self.store._connect() as connection:
            count = connection.execute(
                "SELECT COUNT(*) c FROM research_embeddings").fetchone()["c"]
        self.assertEqual(count, first["indexed"])

    def test_37_restart_does_not_duplicate_indexing(self) -> None:
        self.seed_segment(_corpus_text("battery"))
        self.index_all()
        fresh_store = SemanticRetrievalStore(self.db)
        fresh_store.index_segments(self.research, self.provider,
                                   project_id="projA")
        with fresh_store._connect() as connection:
            count = connection.execute(
                "SELECT COUNT(*) c FROM research_embeddings").fetchone()["c"]
        self.assertEqual(count, 1)

    def test_bounds_enforced_truthfully(self) -> None:
        # Provider-level bound: oversized text fails truthfully.
        with self.assertRaises(EmbeddingError):
            self.provider.embed_texts(["word " * 2000])
        # Segment-level guard: a stored segment exceeding the per-item
        # character bound is reported as a failure, never silently
        # truncated (injected directly to bypass the R1.2 storage cap).
        self.seed_segment(_corpus_text("battery"))
        with self.research._connect() as connection:
            connection.execute(
                """INSERT INTO research_extracted_segments
                   (segment_id, extraction_id, seq, locator_json, text)
                   VALUES ('seg-oversized',
                           (SELECT extraction_id FROM research_extractions
                            LIMIT 1), 99, '{}', ?)""",
                ("z" * (MAX_CHARS_PER_ITEM + 500),))
        summary = self.index_all()
        self.assertEqual(summary["failures"], 1)
        self.assertEqual(
            [f["segment_id"] for f in summary["failures_detail"]],
            ["seg-oversized"])
        self.assertGreaterEqual(summary["indexed"], 1)

    def test_telemetry_counts_items_even_across_batches(self) -> None:
        for name in ("battery", "solar", "tea"):
            self.seed_segment(_corpus_text(name))
        self.store.index_segments(self.research, self.provider,
                                  project_id="projA", batch_size=2)
        metrics = self.store.retrieval_metrics("projA")
        self.assertEqual(metrics["segments_indexed"],
                         metrics["embedding_items"])
        self.assertGreaterEqual(metrics["embedding_batches"], 2)
        self.assertNotIn("model_inference_count", metrics)


class QueryTests(SemanticBase):
    def setUp(self) -> None:
        super().setUp()
        self.battery_sid = self.seed_segment(_corpus_text("battery"))
        self.solar_sid = self.seed_segment(_corpus_text("solar"))
        self.tea_sid = self.seed_segment(_corpus_text("tea"))
        self.index_all()

    def test_14_semantic_search_returns_exact_identities_ranked(self) -> None:
        hits = self.store.semantic_search(
            self.research, self.provider, project_id="projA",
            query="battery efficiency", limit=5)
        self.assertEqual(hits[0].segment_id, self.battery_sid)
        self.assertGreaterEqual(hits[0].semantic_similarity,
                                hits[-1].semantic_similarity)
        self.assertTrue(all(h.locator for h in hits))

    def test_15_cosine_deterministic(self) -> None:
        a = self.provider.embed_texts(["battery"])[0]
        b = self.provider.embed_texts(["battery"])[0]
        self.assertEqual(cosine_similarity(a, b), 1.0)
        first = self.store.semantic_search(
            self.research, self.provider, project_id="projA",
            query="battery efficiency", limit=3)
        second = self.store.semantic_search(
            self.research, self.provider, project_id="projA",
            query="battery efficiency", limit=3)
        self.assertEqual([h.segment_id for h in first],
                         [h.segment_id for h in second])

    def test_16_zero_vector_safe(self) -> None:
        self.assertEqual(
            cosine_similarity([0.0] * 64, self.provider.embed_texts(["x"])[0]),
            0.0)

    def test_17_result_limit_bounded(self) -> None:
        hits = self.store.semantic_search(
            self.research, self.provider, project_id="projA",
            query="battery efficiency solar tea", limit=2)
        self.assertEqual(len(hits), 2)
        # Absurd limits are clamped into the hard bound, never unbounded.
        clamped = self.store.semantic_search(
            self.research, self.provider, project_id="projA",
            query="battery efficiency solar tea", limit=100000)
        self.assertLessEqual(len(clamped), 3)

    def test_18_query_length_bounded(self) -> None:
        with self.assertRaises(EmbeddingError):
            self.store.semantic_search(
                self.research, self.provider, project_id="projA",
                query="x" * 2000)

    def test_10_project_isolation(self) -> None:
        proj_a_battery = self.battery_sid
        proj_b_battery = self.seed_segment(_corpus_text("battery"),
                                           project_id="projB")
        self.store.index_segments(self.research, self.provider,
                                  project_id="projB")
        hits_b = self.store.semantic_search(
            self.research, self.provider, project_id="projB",
            query="battery efficiency", limit=10)
        self.assertEqual([h.segment_id for h in hits_b],
                         [proj_b_battery])
        self.assertNotIn(proj_a_battery,
                         [h.segment_id for h in hits_b])
        hits_a = self.store.semantic_search(
            self.research, self.provider, project_id="projA",
            query="battery efficiency", limit=10)
        self.assertIn(proj_a_battery, [h.segment_id for h in hits_a])

    def test_11_session_filter(self) -> None:
        session_b = self.research.create_session(
            project_id="projA", title="other", purpose="filter")
        created = self.ingestor.ingest_user_content(
            session_id=session_b["session_id"], project_id="projA",
            title="other-doc", text=_corpus_text("tea"))
        extraction = self.ingestor.extract(
            created["snapshot"]["snapshot_id"], project_id="projA")
        with self.research._connect() as connection:
            row = connection.execute(
                "SELECT segment_id FROM research_extracted_segments "
                "WHERE extraction_id = ? ORDER BY seq LIMIT 1",
                (extraction["extraction_id"],)).fetchone()
        other_seg = row["segment_id"]
        self.store.delete_project_vectors("projA")
        self.store.index_segments(
            self.research, self.provider, project_id="projA",
            session_id=session_b["session_id"])
        hits = self.store.semantic_search(
            self.research, self.provider, project_id="projA",
            query="ceremonial tea", limit=10,
            session_id=self.session_id)
        self.assertEqual(hits, [])
        hits_other = self.store.semantic_search(
            self.research, self.provider, project_id="projA",
            query="ceremonial tea", limit=10,
            session_id=session_b["session_id"])
        self.assertEqual([h.segment_id for h in hits_other], [other_seg])

    def test_12_source_kind_filter(self) -> None:
        hits = self.store.semantic_search(
            self.research, self.provider, project_id="projA",
            query="battery efficiency", limit=10,
            source_kind="web")
        self.assertEqual(hits, [])
        hits_user = self.store.semantic_search(
            self.research, self.provider, project_id="projA",
            query="battery efficiency", limit=10,
            source_kind="user_supplied")
        self.assertTrue(hits_user)

    def test_26_similarity_labelled_not_confidence(self) -> None:
        payload = self.store.semantic_search(
            self.research, self.provider, project_id="projA",
            query="battery efficiency", limit=1)[0].to_dict()
        self.assertIn("semantic_similarity", payload)
        rendered = str(payload.keys())
        self.assertNotIn("confidence", rendered.lower())
        self.assertNotIn("truth", rendered.lower())
        self.assertEqual(
            payload["embedding_provenance"]["similarity_label"],
            "retrieval_similarity")

    def test_stale_segment_excluded_from_results(self) -> None:
        # Mutate the AUTHORITATIVE segment content directly to simulate a
        # changed underlying segment; embedding must be treated stale.
        with self.research._connect() as connection:
            connection.execute(
                "UPDATE research_extracted_segments SET text = ? "
                "WHERE segment_id = ?",
                ("completely different content now", self.tea_sid))
        hits = self.store.semantic_search(
            self.research, self.provider, project_id="projA",
            query="ceremonial tea temperature", limit=10)
        self.assertNotIn(self.tea_sid,
                         [h.segment_id for h in hits])
        self.assertGreaterEqual(
            self.store.retrieval_metrics("projA")["stale_vectors_excluded"],
            1)

    def test_stale_hit_cannot_become_evidence(self) -> None:
        with self.research._connect() as connection:
            connection.execute(
                "UPDATE research_extracted_segments SET text = ? "
                "WHERE segment_id = ?",
                ("mutated body", self.tea_sid))
        stale_hit = SemanticSearchResult(
            source_id="src-x", snapshot_id="snap-x",
            extraction_id="ext-x", segment_id=self.tea_sid, locator={},
            semantic_similarity=0.9,
            segment_digest="outdated-digest", preview="mutated body",
            embedding_provenance={})
        with self.assertRaises(EmbeddingError):
            evidence_from_retrieval_hit(self.research, self.ingestor,
                                        stale_hit, project_id="projA")


class HybridTests(SemanticBase):
    def setUp(self) -> None:
        super().setUp()
        self.battery_sid = self.seed_segment(_corpus_text("battery"))
        self.solar_sid = self.seed_segment(_corpus_text("solar"))
        self.tea_sid = self.seed_segment(_corpus_text("tea"))
        self.index_all()

    def _lexical(self, query: str) -> list[dict]:
        self.ingestor.rebuild_index("projA")
        return self.ingestor.search(query, project_id="projA", limit=5)

    def test_19_semantic_result_is_not_evidence(self) -> None:
        hits = self.store.semantic_search(
            self.research, self.provider, project_id="projA",
            query="battery efficiency", limit=5)
        before = len(self.research.list_evidence(
            hits[0].snapshot_id, project_id="projA"))
        # Merely searching must not create evidence anywhere.
        self.assertEqual(before, 0)

    def test_20_semantic_result_to_validated_evidence(self) -> None:
        hit = self.store.semantic_search(
            self.research, self.provider, project_id="projA",
            query="battery efficiency", limit=1)[0]
        evidence = evidence_from_retrieval_hit(
            self.research, self.ingestor, hit, project_id="projA")
        self.assertEqual(evidence["extraction_method"], "extractive")

    def test_22_hybrid_rrf_deterministic_with_both_ranks(self) -> None:
        # Tokens co-occurring in ONE segment so lexical (implicit AND)
        # and semantic paths both retrieve it.
        lexical = self._lexical("battery efficiency")
        semantic = self.store.semantic_search(
            self.research, self.provider, project_id="projA",
            query="battery efficiency", limit=5)
        fused_a = hybrid_fuse(lexical, semantic, limit=10)
        fused_b = hybrid_fuse(lexical, semantic, limit=10)
        self.assertEqual([(h.segment_id, h.hybrid_rank) for h in fused_a],
                         [(h.segment_id, h.hybrid_rank) for h in fused_b])
        both = [h for h in fused_a if h.lexical_rank and h.semantic_rank]
        self.assertTrue(both, "expected at least one dual-path result")
        top = fused_a[0]
        expected_score = round(
            1.0 / (DEFAULT_RRF_K + top.lexical_rank)
            + 1.0 / (DEFAULT_RRF_K + (top.semantic_rank or 10 ** 9)), 8)
        self.assertAlmostEqual(top.hybrid_score, expected_score, places=6)
        self.assertIn(f"rrf(k={DEFAULT_RRF_K})", top.fusion_method)

    def test_23_lexical_only_survives_fusion_without_fabricated_scores(
            self) -> None:
        lexical = self._lexical("ceremonial tea")
        fused = hybrid_fuse(lexical, [], limit=10)
        self.assertTrue(fused)
        self.assertTrue(all(h.semantic_rank is None for h in fused))
        self.assertIsNone(fused[0].semantic_similarity)

    def test_24_semantic_only_survives_fusion(self) -> None:
        semantic = self.store.semantic_search(
            self.research, self.provider, project_id="projA",
            query="thermal cycling panels", limit=5)
        fused = hybrid_fuse([], semantic, limit=10)
        self.assertTrue(fused)
        self.assertTrue(all(h.lexical_rank is None for h in fused))
        self.assertIsNotNone(fused[0].semantic_similarity)

    def test_27_similar_text_does_not_auto_support_claim(self) -> None:
        self.store.semantic_search(
            self.research, self.provider, project_id="projA",
            query="battery efficiency", limit=1)
        question = self.synthesis.create_question(
            session_id=self.session_id, project_id="projA",
            question="battery q")
        claim = self.synthesis.create_claim(
            session_id=self.session_id, project_id="projA",
            claim_text="some battery claim", claim_type=ClaimType.FACTUAL,
            question_id=question["question_id"])
        # Similarity alone never creates a relation; coverage stays empty.
        self.assertEqual(self.synthesis.claim_links(claim["claim_id"]), [])
        self.assertEqual(self.synthesis.coverage_for(
            claim["claim_id"]).value, "no_evidence")

    def test_hybrid_facade_modes_and_degradation(self) -> None:
        engine_no_backend = HybridLocalSearch(
            ingestor=self.ingestor, retrieval_store=self.store,
            provider=None)
        self.ingestor.rebuild_index("projA")
        lexical = engine_no_backend.local_search(
            "battery efficiency", project_id="projA",
            mode=RetrievalMode.LEXICAL.value)
        self.assertEqual(lexical["effective_mode"], "lexical")
        with self.assertRaises(SemanticBackendUnavailable):
            engine_no_backend.local_search(
                "battery efficiency", project_id="projA",
                mode=RetrievalMode.SEMANTIC.value)
        degraded = engine_no_backend.local_search(
            "battery efficiency", project_id="projA",
            mode=RetrievalMode.HYBRID.value)
        self.assertEqual(degraded["requested_mode"], "hybrid")
        self.assertEqual(degraded["effective_mode"], "lexical")
        self.assertEqual(degraded["warning"], "SEMANTIC_BACKEND_UNAVAILABLE")
        self.assertGreaterEqual(
            self.store.retrieval_metrics("projA")[
                "semantic_backend_unavailable"], 1)

    def test_engine_full_pipeline_semantic_and_hybrid(self) -> None:
        engine = HybridLocalSearch(ingestor=self.ingestor,
                                   retrieval_store=self.store,
                                   provider=self.provider)
        self.ingestor.rebuild_index("projA")
        semantic = engine.local_search("battery efficiency",
                                       project_id="projA",
                                       mode=RetrievalMode.SEMANTIC.value)
        self.assertEqual(semantic["effective_mode"], "semantic")
        self.assertEqual(semantic["hits"][0]["segment_id"],
                         self.battery_sid)
        hybrid = engine.local_search("battery efficiency solar",
                                     project_id="projA",
                                     mode=RetrievalMode.HYBRID.value)
        self.assertEqual(hybrid["effective_mode"], "hybrid")
        provenance_ok = any(
            h.get("fusion_method") for h in hybrid["hits"])
        self.assertTrue(provenance_ok)


class DeletionRestartTests(SemanticBase):
    def test_28_embeddings_survive_restart_with_same_ranking(self) -> None:
        self.seed_segment(_corpus_text("battery"))
        self.seed_segment(_corpus_text("solar"))
        self.index_all()
        before = [(h.segment_id, h.semantic_similarity) for h in
                  self.store.semantic_search(
                      self.research, self.provider, project_id="projA",
                      query="battery efficiency", limit=5)]
        reopened_store = SemanticRetrievalStore(self.db)
        after = [(h.segment_id, h.semantic_similarity) for h in
                 reopened_store.semantic_search(
                     self.research, self.provider, project_id="projA",
                     query="battery efficiency", limit=5)]
        self.assertEqual(before, after)

    def test_30_delete_vectors_preserves_evidence_citations_lexical(
            self) -> None:
        self.seed_segment(_corpus_text("battery"))
        self.index_all()
        hit = self.store.semantic_search(
            self.research, self.provider, project_id="projA",
            query="battery efficiency", limit=1)[0]
        evidence = evidence_from_retrieval_hit(
            self.research, self.ingestor, hit, project_id="projA")
        citation = self.research.add_citation(
            evidence["evidence_id"], project_id="projA",
            display_label="[1]")
        deleted = self.store.delete_project_vectors("projA")
        self.assertGreaterEqual(deleted, 1)
        # Authoritative substrate untouched.
        still_evidence = self.research.get_evidence(
            evidence["evidence_id"], project_id="projA")
        self.assertIsNotNone(still_evidence)
        self.assertIsNotNone(self.research.get_snapshot(
            hit.snapshot_id, project_id="projA"))
        # Lexical projection rebuilds and works independently.
        self.ingestor.rebuild_index("projA")
        self.assertTrue(self.ingestor.search("battery efficiency",
                                             project_id="projA"))
        # Reindex restores identical retrieval identity.
        self.index_all()
        restored = self.store.semantic_search(
            self.research, self.provider, project_id="projA",
            query="battery efficiency", limit=1)[0]
        self.assertEqual(restored.segment_id, hit.segment_id)
        del citation

    def test_canonical_amca_db_only(self) -> None:
        db_files = sorted(p.name for p in self.root.glob("*.db"))
        self.assertEqual(db_files, ["amca.db"])


class OrchestratorIntegrationTests(SemanticBase):
    def _orchestrator(self, engine=None) -> ResearchOrchestrator:
        return ResearchOrchestrator(
            run_store=self.runs, research_store=self.research,
            synthesis_store=self.synthesis, fabric=self.fabric,
            acq_store=self.acq_store, ingestor=self.ingestor,
            search_provider=None, semantic_engine=engine)

    def test_35_36_38_search_local_modes_journalled(self) -> None:
        self.seed_segment(_corpus_text("battery"))
        self.index_all()
        self.ingestor.rebuild_index("projA")
        engine = HybridLocalSearch(ingestor=self.ingestor,
                                   retrieval_store=self.store,
                                   provider=self.provider)
        orch = self._orchestrator(engine=engine)
        run_id = self.runs.create_run(
            project_id="projA", session_id=self.session_id,
            objective="local retrieval",
            limits=ResearchLimits(max_steps=4))["run_id"]
        scripted = ScriptedPlanner([
            (ResearchAction.SEARCH_LOCAL,
             {"query": "battery efficiency",
              "retrieval_mode": "semantic"}),
            (ResearchAction.SEARCH_LOCAL,
             {"query": "battery efficiency solar",
              "retrieval_mode": "hybrid"}),
        ])
        result = orch.execute_run(run_id, scripted)
        self.assertEqual(result["status"], RunStatus.PARTIAL.value)
        steps = self.runs.steps_for(run_id)
        semantic_step, hybrid_step = steps[0], steps[1]
        self.assertEqual(semantic_step["outcome"], "executed")
        self.assertEqual(semantic_step["result"]["requested_mode"],
                         "semantic")
        self.assertEqual(semantic_step["result"]["effective_mode"],
                         "semantic")
        self.assertEqual(semantic_step["result"]["embedding_model"],
                         "deterministic-local:hashed-bof")
        self.assertIn("segment_id", semantic_step["result"]["hits"][0])
        self.assertNotIn("vector_blob", str(semantic_step["result"]))
        self.assertEqual(hybrid_step["result"]["effective_mode"], "hybrid")
        self.assertIn("fusion_method", str(hybrid_step["result"]))

    def test_orchestrator_defaults_stay_lexical(self) -> None:
        self.seed_segment(_corpus_text("battery"))
        self.ingestor.rebuild_index("projA")
        orch = self._orchestrator()  # no engine configured at all
        run_id = self.runs.create_run(
            project_id="projA", session_id=self.session_id,
            objective="legacy lexical",
            limits=ResearchLimits(max_steps=2))["run_id"]
        scripted = ScriptedPlanner([
            (ResearchAction.SEARCH_LOCAL,
             {"query": "battery efficiency"})])
        orch.execute_run(run_id, scripted)
        step = self.runs.steps_for(run_id)[0]
        self.assertEqual(step["outcome"], "executed")
        self.assertEqual(step["result"]["effective_mode"], "lexical")
        self.assertIsNone(step["result"].get("warning"))

    def test_semantic_unavailable_outcome_truthful(self) -> None:
        engine = HybridLocalSearch(ingestor=self.ingestor,
                                   retrieval_store=self.store,
                                   provider=None)
        orch = self._orchestrator(engine=engine)
        run_id = self.runs.create_run(
            project_id="projA", session_id=self.session_id,
            objective="unavailable semantic",
            limits=ResearchLimits(max_steps=2))["run_id"]
        scripted = ScriptedPlanner([
            (ResearchAction.SEARCH_LOCAL,
             {"query": "anything", "retrieval_mode": "semantic"})])
        orch.execute_run(run_id, scripted)
        step = self.runs.steps_for(run_id)[0]
        self.assertEqual(step["outcome"], "semantic_backend_unavailable")

    def test_zero_network_and_paid_calls(self) -> None:
        # The whole layer is offline by construction: deterministic hashed
        # provider, sqlite storage, no sockets imported/used at runtime.
        import amca.research_semantic as module

        self.assertFalse(any(
            name in dir(module) for name in
            ("requests", "urllib.request", "http.client")))
        self.assertTrue(self.provider.is_local)
        metrics = self.store.retrieval_metrics("projA")
        self.assertNotIn("paid_calls", metrics)


if __name__ == "__main__":
    unittest.main()
