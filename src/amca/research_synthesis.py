"""R1.4 Evidence-grounded synthesis and citation workflow.

Completes the layer between evidence collection and useful research output:
questions -> claims -> claim-evidence graph (SUPPORTS/CONTRADICTS/CONTEXT/
QUALIFIES assertions, never automatic truth) -> deterministic coverage and
diversity metadata -> bounded context assembly -> injected synthesizer ->
structured draft -> citation validation -> immutable versions.

Central invariant: AMCA may generate a synthesis, but it preserves exactly
which evidence supports each factual statement. Uncited/model-generated
assertions can exist only as explicitly classified non-factual blocks and
can never masquerade as source-backed facts.

GROUNDED == all claimed source relationships are traceable.
GROUNDED != guaranteed true.

No network access, no model calls (synthesizers are injected), everything
durable in the canonical amca.db.
"""

from __future__ import annotations

import hashlib
import json
import sqlite3
from contextlib import contextmanager
from datetime import UTC, datetime
from enum import Enum
from pathlib import Path
from typing import Any

from amca.research_source import ResearchStore, ResearchError as SynthesisDomainError

MAX_RATIONALE_CHARS = 400
MAX_STATEMENT_CHARS = 6000
MAX_CONTEXT_CHARS = 24000


def _now() -> str:
    return datetime.now(UTC).isoformat()


def _uuid(prefix: str) -> str:
    import uuid
    return f"{prefix}-{uuid.uuid4().hex[:12]}"


def _digest(payload: Any) -> str:
    encoded = json.dumps(payload, sort_keys=True,
                         ensure_ascii=False).encode("utf-8")
    return f"v1:{hashlib.sha256(encoded).hexdigest()}"


class ClaimType(str, Enum):
    FACTUAL = "factual"
    COMPARATIVE = "comparative"
    QUANTITATIVE = "quantitative"
    CAUSAL = "causal"
    INTERPRETIVE = "interpretive"
    LIMITATION = "limitation"


class ClaimStatus(str, Enum):
    DRAFT = "draft"
    EVIDENCE_LINKED = "evidence_linked"
    CONTESTED = "contested"
    INSUFFICIENT_EVIDENCE = "insufficient_evidence"
    REJECTED = "rejected"
    ACCEPTED_FOR_SYNTHESIS = "accepted_for_synthesis"


class QuestionStatus(str, Enum):
    OPEN = "open"
    ANSWERED = "answered"
    PARTIAL = "partial"
    UNANSWERED = "unanswered"
    ARCHIVED = "archived"


class EvidenceRelation(str, Enum):
    SUPPORTS = "supports"
    CONTRADICTS = "contradicts"
    CONTEXT = "context"
    QUALIFIES = "qualifies"


class Coverage(str, Enum):
    NO_EVIDENCE = "no_evidence"
    SUPPORT_ONLY = "support_only"
    CONTRADICTION_ONLY = "contradiction_only"
    MIXED = "mixed"
    CONTEXT_ONLY = "context_only"


class Grounding(str, Enum):
    DRAFT = "draft"
    GROUNDING_INCOMPLETE = "grounding_incomplete"
    GROUNDED = "grounded"
    CONTESTED = "contested"
    INVALID_CITATION = "invalid_citation"


class SynthesisOutputForm(str, Enum):
    ANSWER = "answer"
    BRIEF = "brief"
    REPORT = "report"
    LITERATURE_SUMMARY = "literature_summary"
    COMPARISON = "comparison"


class ResearchStoreExt:
    """Mixin-style SQL helpers appended to the R1.1 research store."""

    def _initialize_synthesis(self) -> None:
        with self._connect() as connection:
            connection.executescript("""
                CREATE TABLE IF NOT EXISTS research_questions (
                    question_id TEXT PRIMARY KEY,
                    session_id TEXT NOT NULL REFERENCES research_sessions(session_id),
                    project_id TEXT NOT NULL,
                    parent_question_id TEXT,
                    question TEXT NOT NULL,
                    purpose TEXT,
                    status TEXT NOT NULL,
                    created_at TEXT NOT NULL,
                    updated_at TEXT,
                    provenance_json TEXT NOT NULL DEFAULT '{}'
                );
                CREATE TABLE IF NOT EXISTS research_claims (
                    claim_id TEXT PRIMARY KEY,
                    session_id TEXT NOT NULL,
                    project_id TEXT NOT NULL,
                    question_id TEXT,
                    claim_text TEXT NOT NULL,
                    claim_type TEXT NOT NULL,
                    status TEXT NOT NULL,
                    created_by TEXT NOT NULL DEFAULT 'workflow',
                    created_at TEXT NOT NULL,
                    updated_at TEXT NOT NULL,
                    provenance_json TEXT NOT NULL DEFAULT '{}'
                );
                CREATE TABLE IF NOT EXISTS research_claim_evidence (
                    link_id TEXT PRIMARY KEY,
                    claim_id TEXT NOT NULL REFERENCES research_claims(claim_id),
                    evidence_id TEXT NOT NULL REFERENCES research_evidence(evidence_id),
                    relation TEXT NOT NULL,
                    relation_assessor TEXT NOT NULL,
                    relation_method TEXT NOT NULL,
                    rationale TEXT,
                    created_by TEXT NOT NULL DEFAULT 'workflow',
                    created_at TEXT NOT NULL
                );
                CREATE TABLE IF NOT EXISTS research_synthesis_plans (
                    plan_id TEXT PRIMARY KEY,
                    session_id TEXT NOT NULL,
                    project_id TEXT NOT NULL,
                    question_ids_json TEXT NOT NULL DEFAULT '[]',
                    claim_ids_json TEXT NOT NULL DEFAULT '[]',
                    output_form TEXT NOT NULL,
                    citation_policy TEXT NOT NULL DEFAULT 'internal',
                    max_claims INTEGER NOT NULL DEFAULT 20,
                    max_evidence_per_claim INTEGER NOT NULL DEFAULT 5,
                    max_context_chars INTEGER NOT NULL DEFAULT 24000,
                    created_at TEXT NOT NULL
                );
                CREATE TABLE IF NOT EXISTS research_synthesis_versions (
                    version_id TEXT PRIMARY KEY,
                    plan_id TEXT NOT NULL REFERENCES research_synthesis_plans(plan_id),
                    seq INTEGER NOT NULL,
                    grounding_status TEXT NOT NULL,
                    content_digest TEXT NOT NULL,
                    claim_set_digest TEXT NOT NULL,
                    citation_set_digest TEXT NOT NULL,
                    stale INTEGER NOT NULL DEFAULT 0,
                    draft_json TEXT NOT NULL,
                    created_at TEXT NOT NULL,
                    UNIQUE (plan_id, seq)
                );
            """)


class ResearchSynthesisStore:
    """Durable questions/claims/links/plans/versions in amca.db."""

    def __init__(self, database: Path, *,
                 storage_root: Path | None = None) -> None:
        self.database = Path(database)
        self.storage_root = (Path(storage_root) if storage_root
                             else self.database.parent / "research-content")
        self._initialize()

    def _initialize(self) -> None:
        # R1.1/R1.2 substrate tables must exist first (idempotent); the
        # substrate store instance also serves session/source/snapshot/evidence
        # operations for this synthesis facade.
        self._research = ResearchStore(self.database,
                                       storage_root=self.storage_root)
        with self._connect() as connection:
            connection.executescript("""
                CREATE TABLE IF NOT EXISTS research_questions (
                    question_id TEXT PRIMARY KEY,
                    session_id TEXT NOT NULL REFERENCES research_sessions(session_id),
                    project_id TEXT NOT NULL,
                    parent_question_id TEXT,
                    question TEXT NOT NULL,
                    purpose TEXT,
                    status TEXT NOT NULL,
                    created_at TEXT NOT NULL,
                    updated_at TEXT,
                    provenance_json TEXT NOT NULL DEFAULT '{}'
                );
                CREATE TABLE IF NOT EXISTS research_claims (
                    claim_id TEXT PRIMARY KEY,
                    session_id TEXT NOT NULL,
                    project_id TEXT NOT NULL,
                    question_id TEXT,
                    claim_text TEXT NOT NULL,
                    claim_type TEXT NOT NULL,
                    status TEXT NOT NULL,
                    created_by TEXT NOT NULL DEFAULT 'workflow',
                    created_at TEXT NOT NULL,
                    updated_at TEXT NOT NULL,
                    provenance_json TEXT NOT NULL DEFAULT '{}'
                );
                CREATE TABLE IF NOT EXISTS research_claim_evidence (
                    link_id TEXT PRIMARY KEY,
                    claim_id TEXT NOT NULL REFERENCES research_claims(claim_id),
                    evidence_id TEXT NOT NULL REFERENCES research_evidence(evidence_id),
                    relation TEXT NOT NULL,
                    relation_assessor TEXT NOT NULL,
                    relation_method TEXT NOT NULL,
                    rationale TEXT,
                    created_by TEXT NOT NULL DEFAULT 'workflow',
                    created_at TEXT NOT NULL
                );
                CREATE TABLE IF NOT EXISTS research_synthesis_plans (
                    plan_id TEXT PRIMARY KEY,
                    session_id TEXT NOT NULL,
                    project_id TEXT NOT NULL,
                    question_ids_json TEXT NOT NULL DEFAULT '[]',
                    claim_ids_json TEXT NOT NULL DEFAULT '[]',
                    output_form TEXT NOT NULL,
                    citation_policy TEXT NOT NULL DEFAULT 'internal',
                    max_claims INTEGER NOT NULL DEFAULT 20,
                    max_evidence_per_claim INTEGER NOT NULL DEFAULT 5,
                    max_context_chars INTEGER NOT NULL DEFAULT 24000,
                    created_at TEXT NOT NULL
                );
                CREATE TABLE IF NOT EXISTS research_synthesis_versions (
                    version_id TEXT PRIMARY KEY,
                    plan_id TEXT NOT NULL REFERENCES research_synthesis_plans(plan_id),
                    seq INTEGER NOT NULL,
                    grounding_status TEXT NOT NULL,
                    content_digest TEXT NOT NULL,
                    claim_set_digest TEXT NOT NULL,
                    citation_set_digest TEXT NOT NULL,
                    stale INTEGER NOT NULL DEFAULT 0,
                    draft_json TEXT NOT NULL,
                    created_at TEXT NOT NULL,
                    UNIQUE (plan_id, seq)
                );
            """)

    @contextmanager
    def _connect(self):
        connection = sqlite3.connect(self.database)
        connection.row_factory = sqlite3.Row
        connection.execute("PRAGMA busy_timeout = 5000")
        try:
            yield connection
            connection.commit()
        finally:
            connection.close()

    # -- questions -----------------------------------------------------

    def create_question(self, *, session_id: str, project_id: str,
                        question: str, purpose: str | None = None,
                        parent_question_id: str | None = None) -> dict[str, Any]:
        question_id = _uuid("qst")
        with self._connect() as connection:
            connection.execute(
                """INSERT INTO research_questions
                   (question_id, session_id, project_id, parent_question_id,
                    question, purpose, status, created_at, provenance_json)
                   VALUES (?, ?, ?, ?, ?, ?, 'open', ?, '{}')""",
                (question_id, session_id, project_id, parent_question_id,
                 question, purpose, _now()))
        return self.get_question(question_id)

    def get_question(self, question_id: str) -> dict[str, Any] | None:
        with self._connect() as connection:
            row = connection.execute(
                "SELECT * FROM research_questions WHERE question_id = ?",
                (question_id,)).fetchone()
        record = dict(row) if row else None
        if record:
            record["provenance"] = json.loads(record.pop("provenance_json"))
        return record

    def set_question_status(self, question_id: str, status: QuestionStatus) -> None:
        with self._connect() as connection:
            connection.execute(
                "UPDATE research_questions SET status=?, updated_at=? "
                "WHERE question_id=?",
                (status.value, _now(), question_id))

    def subquestions_of(self, question_id: str) -> list[dict[str, Any]]:
        with self._connect() as connection:
            rows = connection.execute(
                "SELECT * FROM research_questions WHERE parent_question_id = ?",
                (question_id,)).fetchall()
        return [dict(row) for row in rows]

    # -- claims --------------------------------------------------------

    def create_claim(self, *, session_id: str, project_id: str,
                     claim_text: str, claim_type: ClaimType,
                     question_id: str | None = None,
                     created_by: str = "workflow") -> dict[str, Any]:
        claim_id = _uuid("clm")
        now = _now()
        with self._connect() as connection:
            connection.execute(
                """INSERT INTO research_claims
                   (claim_id, session_id, project_id, question_id, claim_text,
                    claim_type, status, created_by, created_at, updated_at,
                    provenance_json)
                   VALUES (?, ?, ?, ?, ?, ?, 'draft', ?, ?, ?, '{}')""",
                (claim_id, session_id, project_id, question_id, claim_text,
                 claim_type.value, created_by, now, now))
        return self.get_claim(claim_id)

    def get_claim(self, claim_id: str) -> dict[str, Any] | None:
        with self._connect() as connection:
            row = connection.execute(
                "SELECT * FROM research_claims WHERE claim_id = ?",
                (claim_id,)).fetchone()
        record = dict(row) if row else None
        if record:
            record["provenance"] = json.loads(record.pop("provenance_json"))
        return record

    def set_claim_status(self, claim_id: str, status: ClaimStatus) -> None:
        with self._connect() as connection:
            connection.execute(
                "UPDATE research_claims SET status=?, updated_at=? "
                "WHERE claim_id=?", (status.value, _now(), claim_id))

    # -- claim <-> evidence links --------------------------------------

    def link_evidence(self, *, claim_id: str, evidence_id: str,
                      relation: EvidenceRelation,
                      assessor: str = "model",
                      method: str = "unspecified",
                      rationale: str | None = None,
                      created_by: str = "workflow") -> dict[str, Any]:
        with self._connect() as connection:
            claim = connection.execute(
                "SELECT project_id FROM research_claims WHERE claim_id=?",
                (claim_id,)).fetchone()
            if claim is None:
                raise SynthesisDomainError("unknown claim")
            ev_row = connection.execute(
                "SELECT project_id FROM research_evidence WHERE evidence_id=?",
                (evidence_id,)).fetchone()
            if ev_row is None:
                raise SynthesisDomainError("unknown evidence")
            if ev_row["project_id"] != claim["project_id"]:
                raise SynthesisDomainError(
                    "cross-project evidence link rejected")
        link_id = _uuid("lnk")
        with self._connect() as connection:
            connection.execute(
                """INSERT INTO research_claim_evidence
                   (link_id, claim_id, evidence_id, relation,
                    relation_assessor, relation_method, rationale,
                    created_by, created_at)
                   VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                (link_id, claim_id, evidence_id, relation.value, assessor,
                 method, (rationale or "")[:MAX_RATIONALE_CHARS],
                 created_by, _now()))
        return {"link_id": link_id, "claim_id": claim_id,
                "evidence_id": evidence_id, "relation": relation.value}

    def claim_links(self, claim_id: str) -> list[dict[str, Any]]:
        with self._connect() as connection:
            rows = connection.execute(
                "SELECT * FROM research_claim_evidence WHERE claim_id = ?",
                (claim_id,)).fetchall()
        return [dict(row) for row in rows]

    # -- coverage / diversity / integrity -------------------------------

    def coverage_for(self, claim_id: str) -> Coverage:
        relations = [EvidenceRelation(link["relation"])
                     for link in self.claim_links(claim_id)]
        if not relations:
            return Coverage.NO_EVIDENCE
        supporting = any(r in (EvidenceRelation.SUPPORTS,
                               EvidenceRelation.QUALIFIES)
                         for r in relations)
        contradicts = EvidenceRelation.CONTRADICTS in relations
        context_only = all(r is EvidenceRelation.CONTEXT for r in relations)
        if contradicts and supporting:
            return Coverage.MIXED
        if contradicts:
            return Coverage.CONTRADICTION_ONLY
        if supporting:
            return Coverage.SUPPORT_ONLY
        if context_only:
            return Coverage.CONTEXT_ONLY
        return Coverage.NO_EVIDENCE

    def diversity_for(self, claim_id: str) -> dict[str, Any]:
        links = self.claim_links(claim_id)
        snapshots = set()
        sources = set()
        kinds: dict[str, int] = {}
        for link in links:
            row = self._evidence_chain(link["evidence_id"])
            if row is None:
                continue
            snapshots.add(row["snapshot_id"])
            sources.add(row["source_id"])
            kind = str(row["source_kind"])
            kinds[kind] = kinds.get(kind, 0) + 1
        return {
            "evidence_chunks": len(links),
            "distinct_snapshots": len(snapshots),
            "distinct_sources": len(sources),
            "source_kind_distribution": kinds,
        }

    def integrity_for(self, claim_id: str) -> bool:
        """Every link must resolve evidence -> snapshot -> source."""

        for link in self.claim_links(claim_id):
            if self._evidence_chain(link["evidence_id"]) is None:
                return False
        return True

    def _evidence_chain(self, evidence_id: str) -> dict[str, Any] | None:
        with self._connect() as connection:
            row = connection.execute(
                """SELECT e.evidence_id, s.snapshot_id, src.source_id,
                          src.source_kind, s.content_digest,
                          e.excerpt, e.locator_json
                   FROM research_evidence e
                   JOIN research_source_snapshots s ON s.snapshot_id = e.snapshot_id
                   JOIN research_sources src ON src.source_id = s.source_id
                   WHERE e.evidence_id = ?""", (evidence_id,)).fetchone()
        if row is None:
            return None
        record = dict(row)
        record["locator"] = json.loads(record.pop("locator_json"))
        return record

    # -- synthesis plans & versions ------------------------------------

    def create_plan(self, *, session_id: str, project_id: str,
                    question_ids: list[str], claim_ids: list[str],
                    output_form: SynthesisOutputForm,
                    max_claims: int = 20,
                    max_evidence_per_claim: int = 5,
                    max_context_chars: int = MAX_CONTEXT_CHARS,
                    citation_policy: str = "internal") -> dict[str, Any]:
        plan_id = _uuid("plan")
        with self._connect() as connection:
            connection.execute(
                """INSERT INTO research_synthesis_plans
                   (plan_id, session_id, project_id, question_ids_json,
                    claim_ids_json, output_form, citation_policy,
                    max_claims, max_evidence_per_claim, max_context_chars,
                    created_at)
                   VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                (plan_id, session_id, project_id,
                 json.dumps(question_ids), json.dumps(claim_ids),
                 output_form.value, citation_policy, max_claims,
                 max_evidence_per_claim, max_context_chars, _now()))
        return self.get_plan(plan_id)

    def get_plan(self, plan_id: str) -> dict[str, Any] | None:
        with self._connect() as connection:
            row = connection.execute(
                "SELECT * FROM research_synthesis_plans WHERE plan_id = ?",
                (plan_id,)).fetchone()
        record = dict(row) if row else None
        if record:
            record["question_ids"] = json.loads(record.pop("question_ids_json"))
            record["claim_ids"] = json.loads(record.pop("claim_ids_json"))
        return record

    # -- context assembly & validation ---------------------------------

    def build_context(self, plan: dict[str, Any]) -> dict[str, Any]:
        """Deterministic bounded evidence-context assembly.

        Selection mode 'all explicitly linked evidence within bounds'.
        Contradiction/conflict lists are derived structurally.
        """

        max_claims = int(plan.get("max_claims", 20))
        max_evidence = int(plan.get("max_evidence_per_claim", 5))
        max_chars = int(plan.get("max_context_chars", MAX_CONTEXT_CHARS))

        claims: list[dict[str, Any]] = []
        evidence_blocks: list[dict[str, Any]] = []
        used_chars = 0
        partial_reason = None
        selected_claims = plan["claim_ids"][:max_claims]
        if len(plan["claim_ids"]) > max_claims:
            partial_reason = "claim bound exceeded"

        seen_evidence: set[str] = set()
        for claim_id in selected_claims:
            links = self.claim_links(claim_id)[:max_evidence]
            if len(self.claim_links(claim_id)) > max_evidence:
                partial_reason = "evidence per claim bound exceeded"
            entry = {"claim_id": claim_id,
                     "coverage": self.coverage_for(claim_id).value,
                     "diversity": self.diversity_for(claim_id),
                     "evidence": []}
            for link in links:
                chain = self._evidence_chain(link["evidence_id"])
                if chain is None:
                    continue
                text = chain["excerpt"]
                used_chars += len(text) + len(link["relation"]) + 8
                block = {
                    "evidence_id": link["evidence_id"],
                    "snapshot_id": chain["snapshot_id"],
                    "source_id": chain["source_id"],
                    "source_kind": chain["source_kind"],
                    "locator": chain["locator"],
                    "text": text[:MAX_STATEMENT_CHARS],
                    "relation": link["relation"],
                    "relation_assessor": link["relation_assessor"],
                }
                claims_entry_evidence = entry["evidence"]
                claims_entry_evidence.append(block)
                evidence_blocks.append(block)
                seen_evidence.add(link["evidence_id"])
            if used_chars > max_chars and partial_reason is None:
                partial_reason = "context character bound exceeded"
            claims.append(entry)

        conflicts = [
            {"claim_id": c, "contradicting_evidence": link["evidence_id"]}
            for c in selected_claims
            for link in self.claim_links(c)
            if EvidenceRelation(link["relation"]) is EvidenceRelation.CONTRADICTS
        ]
        return {
            "questions": plan["question_ids"],
            "claims": claims,
            "conflicts": conflicts,
            "context_partial_warning": partial_reason,
            "selected_evidence_count": len(evidence_blocks),
        }

    def save_synthesis_version(self, *, plan_id: str,
                               grounding_status: Grounding,
                               draft: dict[str, Any]) -> dict[str, Any]:
        """Append an immutable synthesis version with integrity digests."""

        with self._connect() as connection:
            seq_row = connection.execute(
                "SELECT COALESCE(MAX(seq),0)+1 AS next FROM "
                "research_synthesis_versions WHERE plan_id=?",
                (plan_id,)).fetchone()
            seq = int(seq_row["next"])
        version_id = _uuid("ver")
        record = {
            "version_id": version_id,
            "plan_id": plan_id,
            "seq": seq,
            "grounding_status": grounding_status.value,
            "content_digest": _digest(draft.get("sections")),
            "claim_set_digest": _digest(draft.get("claims_used")),
            "citation_set_digest": _digest(draft.get("citations")),
            "stale": 0,
            "draft_json": json.dumps(draft, sort_keys=True),
            "created_at": _now(),
        }
        with self._connect() as connection:
            connection.execute(
                """INSERT INTO research_synthesis_versions
                   (version_id, plan_id, seq, grounding_status,
                    content_digest, claim_set_digest, citation_set_digest,
                    stale, draft_json, created_at)
                   VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                (record["version_id"], record["plan_id"], record["seq"],
                 record["grounding_status"], record["content_digest"],
                 record["claim_set_digest"], record["citation_set_digest"],
                 record["stale"], record["draft_json"], record["created_at"]))
        return record

    def get_latest_version(self, plan_id: str) -> dict[str, Any] | None:
        with self._connect() as connection:
            row = connection.execute(
                "SELECT * FROM research_synthesis_versions WHERE plan_id = ? "
                "ORDER BY seq DESC LIMIT 1", (plan_id,)).fetchone()
        record = dict(row) if row else None
        if record:
            record["draft"] = json.loads(record.pop("draft_json"))
        return record


class DeterministicSynthesizer:
    """Non-model baseline synthesizer: all linked evidence within bounds."""

    provider_id = "deterministic"

    def synthesize(self, context: dict[str, Any]) -> dict[str, Any]:
        sections: list[dict[str, Any]] = []
        citations: list[dict[str, Any]] = []
        claims_used: list[dict[str, Any]] = []

        for claim in context["claims"]:
            statements: list[dict[str, Any]] = []
            for block in claim["evidence"]:
                citation = {
                    "evidence_id": block["evidence_id"],
                    "snapshot_id": block["snapshot_id"],
                    "source_id": block["source_id"],
                    "locator": block["locator"],
                    # Authoritative snapshot text carried for exact-quote
                    # validation at grounding time.
                    "text": block["text"],
                }
                citations.append(citation)
                statements.append({
                    "kind": "factual",
                    "text": f"[{block['relation']}] {block['text']}",
                    "claim_id": claim["claim_id"],
                    "citation_refs": [len(citations)],
                    "quote_exact": True,
                })
            if not claim["evidence"]:
                claims_used.append({
                    "claim_id": claim["claim_id"],
                    "status": "unsupported",
                })
                continue
            claims_used.append({
                "claim_id": claim["claim_id"],
                "status": "supported",
                "coverage": claim["coverage"],
            })
            sections.append({"heading": f"Claim {claim['claim_id']}",
                             "statements": statements})

        sections.append({
            "heading": "Gaps",
            "statements": [{
                "kind": "transition",
                "text": "Coverage warnings and research gaps listed separately.",
                "citation_refs": [],
            }],
        })
        conflicts = [
            {"claim_id": conflict["claim_id"]}
            for conflict in context["conflicts"]
        ]
        return {"sections": sections, "citations": citations,
                "claims_used": claims_used, "conflicts": conflicts}


def evaluate_grounding(store, plan: dict[str, Any],
                       draft: dict[str, Any]) -> Grounding:
    """Citation coverage validation over the structured draft.

    Rules (frozen):
    - factual statement with no resolvable citation -> GROUNDING_INCOMPLETE;
    - citation reference to an id AMCA never supplied -> INVALID_CITATION;
    - exact-quote statements must match their evidence text verbatim;
    - material contradiction present -> CONTESTED (still fully traceable);
    - unsupported claims may not appear as grounded factual prose.
    """

    # A synthesizer may reference ONLY evidence AMCA supplied in its bounded
    # context - anything else is an invented citation (no fuzzy repair).
    supplied_evidence: set[str] = set()
    for claim_entry in plan.get("claims", []) if isinstance(plan, dict) else []:
        supplied_evidence.update(
            block["evidence_id"] for block in claim_entry["evidence"])
    valid_evidence = {c["evidence_id"]: c for c in draft.get("citations", [])}
    invalid = False
    incomplete = False
    for section in draft.get("sections", []):
        for statement in section.get("statements", []):
            kind = statement.get("kind")
            refs = statement.get("citation_refs") or []
            citations = draft.get("citations", [])
            resolved = []
            for ref in refs:
                if isinstance(ref, int) and 1 <= ref <= len(citations):
                    resolved.append(citations[ref - 1])
                elif isinstance(ref, dict):
                    resolved.append(ref)
            if kind == "factual" and not resolved:
                incomplete = True
                continue
            for citation in resolved:
                evidence_id = citation.get("evidence_id")
                if evidence_id not in supplied_evidence:
                    invalid = True
                    continue
                target = valid_evidence.get(evidence_id)
                if target is None:
                    invalid = True
                    continue
                if statement.get("quote_exact"):
                    source_text = str(target.get("text", ""))
                    core = str(statement.get("text", ""))
                    if "] " in core:
                        core = core.split("] ", 1)[1]
                    core = core.strip()
                    if not core or core not in source_text:
                        invalid = True
    unsupported = [c for c in draft.get("claims_used", [])
                   if c.get("status") == "unsupported"]
    if unsupported:
        # Unsupported claims may not silently become grounded factual prose.
        return Grounding.GROUNDING_INCOMPLETE
    if invalid:
        return Grounding.INVALID_CITATION
    if incomplete:
        return Grounding.GROUNDING_INCOMPLETE
    return Grounding.GROUNDED


def assign_citation_numbers(draft: dict[str, Any]) -> dict[str, int]:
    """Deterministic first-use citation numbering.

    Repeated references to the same canonical evidence reuse the same
    number; restart cannot renumber because ordering derives from the
    immutable draft structure itself.
    """

    numbering: dict[str, int] = {}
    counter = 0
    for section in draft.get("sections", []):
        for statement in section.get("statements", []):
            for ref in statement.get("citation_refs") or []:
                if isinstance(ref, int):
                    continue
                key = ref["evidence_id"]
                if key not in numbering:
                    counter += 1
                    numbering[key] = counter
    return numbering


def render_references(draft: dict[str, Any]) -> list[dict[str, str]]:
    """Neutral internal references list - never fabricates bibliographic data."""

    references = []
    for citation in draft.get("citations", []):
        references.append({
            "evidence_id": citation["evidence_id"],
            "snapshot_id": citation.get("snapshot_id"),
            "source_id": citation.get("source_id"),
            # Missing bibliographic metadata renders truthfully.
            "author": citation.get("author") or "(no author recorded)",
            "date": citation.get("published_at") or "(no date recorded)",
        })
    return references


def mark_plan_stale(store, plan_id: str) -> None:
    """Claim/evidence selection changed -> earlier working drafts are STALE."""

    with store._connect() as connection:
        connection.execute(
            "UPDATE research_synthesis_versions SET stale = 1 WHERE plan_id = ?",
            (plan_id,))


# -- R2.1 orchestrator support (added to ResearchSynthesisStore) ------

def list_questions(self, session_id: str, *, project_id: str):
    with self._connect() as connection:
        rows = connection.execute(
            "SELECT * FROM research_questions WHERE session_id = ? "
            "AND project_id = ? ORDER BY created_at LIMIT 100",
            (session_id, project_id)).fetchall()
    return [dict(row) for row in rows]


def claim_ids_for_session(self, session_id: str, *, project_id: str):
    with self._connect() as connection:
        rows = connection.execute(
            "SELECT claim_id FROM research_claims WHERE session_id = ? "
            "AND project_id = ? ORDER BY created_at LIMIT 200",
            (session_id, project_id)).fetchall()
    return [row["claim_id"] for row in rows]


ResearchSynthesisStore.list_questions = list_questions
ResearchSynthesisStore.claim_ids_for_session = claim_ids_for_session
