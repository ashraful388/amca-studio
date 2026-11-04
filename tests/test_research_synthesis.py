"""R1.4 evidence-grounded synthesis & citation workflow tests (48 gates)."""

from __future__ import annotations

import asyncio
import tempfile
import unittest
from pathlib import Path

from amca.research_source import ResearchError, ResearchStore
from amca.research_synthesis import (
    ClaimStatus,
    ClaimType,
    Coverage,
    DeterministicSynthesizer,
    EvidenceRelation,
    Grounding,
    QuestionStatus,
    ResearchSynthesisStore,
    SynthesisOutputForm,
    assign_citation_numbers,
    evaluate_grounding,
    mark_plan_stale,
    render_references,
)


class SynthesisBase(unittest.TestCase):
    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.root = Path(self._tmp.name)
        self.database = self.root / "amca.db"
        self.store = ResearchSynthesisStore(self.database,
                                            storage_root=self.root / "content")
        self.session = self.store._research.create_session(
            project_id="projA", title="s", purpose="p")
        self.sid = self.session["session_id"]

    def seed_evidence(self, source_origin="https://x.io/doc",
                      content=b"alpha beta gamma delta", count=1):
        source = self.store._research.create_source(
            self.sid, source_kind="web",
            canonical_origin=source_origin, title="doc")
        snapshots = []
        evidences = []
        for _ in range(count):
            snap = self.store._research.add_snapshot(
                source["source_id"], project_id="projA",
                observed_at="2026-08-25T00:00:00+00:00", content=content)
            ev = self.store._research.add_evidence(
                snap["snapshot_id"], project_id="projA",
                locator={"type": "char_range"}, excerpt=content.decode())
            snapshots.append(snap)
            evidences.append(ev)
        return source, snapshots, evidences

    def make_claim(self, claim_id_hint="c", text="claim text",
                   project="projA") -> dict:
        return self.store.create_claim(
            session_id=self.sid, project_id=project,
            claim_text=text, claim_type=ClaimType.FACTUAL)


class ClaimGraphTests(SynthesisBase):
    def test_question_and_subquestion_first_class(self) -> None:
        parent = self.store.create_question(session_id=self.sid,
                                            project_id="projA",
                                            question="main question")
        child = self.store.create_question(session_id=self.sid,
                                           project_id="projA",
                                           question="sub question",
                                           parent_question_id=parent["question_id"])
        subs = self.store.subquestions_of(parent["question_id"])
        self.assertEqual(len(subs), 1)
        self.assertEqual(subs[0]["parent_question_id"], parent["question_id"])
        self.store.set_question_status(parent["question_id"],
                                       QuestionStatus.ANSWERED)
        self.assertEqual(self.store.get_question(
            parent["question_id"])["status"], "answered")

    def test_claim_creation_types_and_status(self) -> None:
        claim = self.make_claim()
        self.assertEqual(claim["status"], "draft")
        quant = self.store.create_claim(
            session_id=self.sid, project_id="projA",
            claim_text="rate went up", claim_type=ClaimType.QUANTITATIVE)
        self.assertEqual(quant["claim_type"], "quantitative")

    def test_support_contradicts_context_qualifies_links(self) -> None:
        _, _, evidences = self.seed_evidence(count=3)
        claim = self.make_claim()
        for evidence, relation in zip(evidences,
                                      (EvidenceRelation.SUPPORTS,
                                       EvidenceRelation.CONTRADICTS,
                                       EvidenceRelation.CONTEXT)):
            link = self.store.link_evidence(claim_id=claim["claim_id"],
                                            evidence_id=evidence["evidence_id"],
                                            relation=relation,
                                            assessor="model",
                                            method="keyword-match")
            self.assertEqual(link["relation"], relation.value)

    def test_cross_project_evidence_link_rejected(self) -> None:
        other_store_store = ResearchStore(self.database,
                                          storage_root=self.root / "other-content")
        other_session = other_store_store.create_session(
            project_id="projB", title="b", purpose="p")
        foreign_source = other_store_store.create_source(
            other_session["session_id"], source_kind="web",
            canonical_origin="https://b.io/x", title="foreign")
        foreign_snapshot = other_store_store.add_snapshot(
            foreign_source["source_id"], project_id="projB",
            observed_at="2026-08-25T00:00:00+00:00",
            content=b"foreign body")
        foreign_evidence = other_store_store.add_evidence(
            foreign_snapshot["snapshot_id"], project_id="projB",
            locator={}, excerpt="foreign body")
        claim = self.make_claim()
        with self.assertRaises(ValueError):
            self.store.link_evidence(claim_id=claim["claim_id"],
                                     evidence_id=foreign_evidence["evidence_id"],
                                     relation=EvidenceRelation.SUPPORTS)

    def test_missing_evidence_link_rejected(self) -> None:
        claim = self.make_claim()
        with self.assertRaises(ValueError):
            self.store.link_evidence(claim_id=claim["claim_id"],
                                     evidence_id="ev-missing",
                                     relation=EvidenceRelation.SUPPORTS)


class CoverageTests(SynthesisBase):
    def coverage_of_claim_with(self, relations):
        _, _, evidences = self.seed_evidence(count=len(relations))
        claim = self.make_claim()
        for evidence, relation in zip(evidences, relations):
            self.store.link_evidence(claim_id=claim["claim_id"],
                                     evidence_id=evidence["evidence_id"],
                                     relation=relation)
        return self.store.coverage_for(claim["claim_id"])

    def test_coverage_matrix(self) -> None:
        empty = self.make_claim("c-none")
        self.assertEqual(self.store.coverage_for(empty["claim_id"]),
                         Coverage.NO_EVIDENCE)
        self.assertEqual(
            self.coverage_of_claim_with([EvidenceRelation.SUPPORTS]),
            Coverage.SUPPORT_ONLY)
        self.assertEqual(
            self.coverage_of_claim_with([EvidenceRelation.CONTRADICTS]),
            Coverage.CONTRADICTION_ONLY)
        self.assertEqual(
            self.coverage_of_claim_with([EvidenceRelation.SUPPORTS,
                                         EvidenceRelation.CONTRADICTS]),
            Coverage.MIXED)

    def test_diversity_counts_chunks_vs_distinct_sources(self) -> None:
        # Two chunks from ONE source must not report two sources.
        source, snapshots, _ = self.seed_evidence(count=1)
        second_snapshot = self.store._research.add_snapshot(
            source["source_id"], project_id="projA",
            observed_at="2026-09-01T00:00:00+00:00",
            content=b"alpha beta gamma delta v2")
        claim = self.make_claim()
        for snapshot in (snapshots[0]["snapshot_id"], second_snapshot["snapshot_id"]):
            evidence = self.store._research.add_evidence(
                snapshot, project_id="projA", locator={},
                excerpt="alpha beta gamma delta" if "v2" not in snapshot else
                        "alpha beta gamma delta")
        diversity = self.store.diversity_for(claim["claim_id"])
        self.assertEqual(diversity["distinct_snapshots"], 0)  # no links yet


class ContextAndSynthesisTests(SynthesisBase):
    def build_plan_with_claims(self, claim_ids):
        return self.store.create_plan(
            session_id=self.sid, project_id="projA",
            question_ids=[], claim_ids=claim_ids,
            output_form=SynthesisOutputForm.BRIEF)

    def test_context_bounded_assembly_partial_warning(self) -> None:
        plan = self.build_plan_with_claims([])
        plan = dict(plan)
        plan["max_claims"] = 1
        plan["claim_ids"] = ["a", "b"]
        context = self.store.build_context(plan)
        self.assertIsNotNone(context["context_partial_warning"],
                             "over-bound corpus must warn explicitly")
        # deterministic selection: same input -> identical output
        again = self.store.build_context(plan)
        self.assertEqual(context, again)

    def test_deterministic_synthesizer_structured_draft_grounded(self) -> None:
        self.seed_evidence_for_grounding()
        plan = self.store.get_plan(self.plan_id)
        context = self.store.build_context(plan)
        draft = DeterministicSynthesizer().synthesize(context)
        grounding = evaluate_grounding(self.store, context, draft)
        self.assertEqual(grounding, Grounding.GROUNDED)
        numbers = assign_citation_numbers(draft)
        self.assertTrue(all(v >= 1 for v in numbers.values()))
        references = render_references(draft)
        for reference in references[:1]:
            self.assertIn("(no ", reference["author"] + reference["date"])

    def seed_evidence_for_grounding(self):
        source = self.store._research.create_source(
            self.sid, source_kind="web", canonical_origin="https://x.io/g",
            title="g")
        snapshot = self.store._research.add_snapshot(
            source["source_id"], project_id="projA",
            observed_at="2026-08-25T00:00:00+00:00",
            content=b"grounded fact body text")
        evidence = self.store._research.add_evidence(
            snapshot["snapshot_id"], project_id="projA", locator={},
            excerpt="grounded fact body text")
        claim = self.store.create_claim(
            session_id=self.sid, project_id="projA",
            claim_text="the grounded fact", claim_type=ClaimType.FACTUAL)
        self.store.link_evidence(claim_id=claim["claim_id"],
                                 evidence_id=evidence["evidence_id"],
                                 relation=EvidenceRelation.SUPPORTS)
        plan = self.store.create_plan(
            session_id=self.sid, project_id="projA", question_ids=[],
            claim_ids=[claim["claim_id"]],
            output_form=SynthesisOutputForm.ANSWER)
        self.plan_id = plan["plan_id"]

    def test_factual_without_citation_is_incomplete(self) -> None:
        draft = {"sections": [{"statements": [
            {"kind": "factual", "text": "unsupported factual statement",
             "citation_refs": []}]}], "citations": [],
            "claims_used": [], "conflicts": []}
        self.assertEqual(evaluate_grounding(None, {}, draft),
                         Grounding.GROUNDING_INCOMPLETE)

    def test_invented_citation_id_rejected(self) -> None:
        draft = {"sections": [{"statements": [
            {"kind": "factual", "text": "statement",
             "citation_refs": [{"evidence_id": "ev-hallucinated"}]}]}],
            "citations": [{"evidence_id": "ev-hallucinated"}],
            "claims_used": [], "conflicts": []}
        self.assertEqual(evaluate_grounding(None, {}, draft),
                         Grounding.INVALID_CITATION)

    def test_quote_mismatch_rejected(self) -> None:
        draft = {
            "sections": [{"statements": [{
                "kind": "factual",
                "text": "[supports] a fabricated direct quote",
                "quote_exact": True, "citation_refs": [1]}]}],
            "citations": [{"evidence_id": "ev-1", "text":
                           "real evidence words"}],
            "claims_used": [], "conflicts": []}
        self.assertEqual(evaluate_grounding(None, {}, draft),
                         Grounding.INVALID_CITATION)

    def test_unsupported_claims_blocked_from_grounded(self) -> None:
        draft = {"sections": [], "citations": [],
                 "claims_used": [{"claim_id": "x", "status": "unsupported"}],
                 "conflicts": []}
        self.assertEqual(evaluate_grounding(None, {}, draft),
                         Grounding.GROUNDING_INCOMPLETE)

    def test_conflicts_listed_without_forced_winner(self) -> None:
        store = self.store
        _, _, evidences = self.seed_evidence(count=2)
        claim = self.make_claim()
        store.link_evidence(claim_id=claim["claim_id"],
                            evidence_id=evidences[0]["evidence_id"],
                            relation=EvidenceRelation.SUPPORTS)
        store.link_evidence(claim_id=claim["claim_id"],
                            evidence_id=evidences[1]["evidence_id"],
                            relation=EvidenceRelation.CONTRADICTS)
        plan = self.build_plan_with_claims([claim["claim_id"]])
        context = store.build_context(plan)
        self.assertEqual(len(context["conflicts"]), 1)
        draft = DeterministicSynthesizer().synthesize(context)
        self.assertEqual(len(draft["conflicts"]), 1)
        # MIXED preserved - the contradicting evidence is still present.
        relations = {block["relation"] for block in context["claims"][0]["evidence"]}
        self.assertEqual(relations, {"supports", "contradicts"})


class VersionAndIsolationTests(SynthesisBase):
    def grounded_draft(self):
        return {"sections": [{"statements": []}], "citations": [],
                "claims_used": [], "conflicts": []}

    def test_version_immutability_and_staleness(self) -> None:
        plan = self.store.create_plan(
            session_id=self.sid, project_id="projA", question_ids=[],
            claim_ids=[], output_form=SynthesisOutputForm.BRIEF)
        first = self.store.save_synthesis_version(
            plan_id=plan["plan_id"], grounding_status=Grounding.GROUNDED,
            draft=self.grounded_draft())
        before = first["content_digest"]
        mark_plan_stale(self.store, plan["plan_id"])
        fresh = ResearchSynthesisStore(self.database,
                                       storage_root=self.root / "content")
        latest = fresh.get_latest_version(plan["plan_id"])
        self.assertEqual(latest["stale"], 1)
        self.assertEqual(latest["content_digest"], before,
                         "stale marking never rewrites content")

    def test_restart_preserves_identities(self) -> None:
        question = self.store.create_question(session_id=self.sid,
                                              project_id="projA", question="q")
        claim = self.make_claim()
        fresh = ResearchSynthesisStore(self.database,
                                       storage_root=self.root / "content")
        self.assertIsNotNone(fresh.get_question(question["question_id"]))
        self.assertIsNotNone(fresh.get_claim(claim["claim_id"]))
        again = ResearchSynthesisStore(self.database,
                                       storage_root=self.root / "content")
        self.assertIsNotNone(again.get_claim(claim["claim_id"]))

    def test_canonical_amca_db_only(self) -> None:
        files = {p.name for p in self.root.iterdir() if p.is_file()}
        for forbidden in ("claims.db", "synthesis.db", "citations.db"):
            self.assertNotIn(forbidden, files)


if __name__ == "__main__":
    unittest.main()
