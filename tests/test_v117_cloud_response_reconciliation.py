"""Regression coverage for cloud contributions in final chat responses."""
import pytest

from amca.anti_fabrication import reconcile_anti_fabrication
from amca.chat_history import ConversationStore
from amca.service import RunJob, RunRegistry


DEEPSEEK_SUMMARY = (
    "Approved excerpt contains only 'response ok only.' No actionable task, "
    "acceptance criteria, or verifiable artifact is available, so the bounded "
    "task cannot be completed or verified."
)


@pytest.mark.parametrize("text", [
    DEEPSEEK_SUMMARY,
    "The task cannot be completed.",
    "The task was not completed successfully.",
    "The operation didn't succeed.",
    "The operation hasn’t been completed.",
    "I was unable to complete the task.",
    "The artifact was not created.",
    "I have not written the file report.txt.",
    "The command was not executed.",
    "ok",
])
def test_negative_explanation_is_preserved(text):
    assert reconcile_anti_fabrication(text, executed_ops=[]) == (text, False)


@pytest.mark.parametrize("text", [
    "The task was completed successfully.",
    "The task cannot be completed, but the file was written successfully.",
    "The task cannot be completed. The operation succeeded.",
    "The task cannot be completed and the operation succeeded.",
    "The task was not completed successfully; the artifact was created.",
    "The task was not only completed but also verified.",
    "I did not wait: the operation succeeded.",
    "The operation didn't fail and completed successfully.",
])
def test_unsupported_affirmative_claim_remains_blocked(text):
    response, altered = reconcile_anti_fabrication(text, executed_ops=[])
    assert altered
    assert "not executed" in response


def test_matching_evidence_still_preserves_success():
    text = "I wrote the file report.txt."
    assert reconcile_anti_fabrication(
        text, executed_ops=["write:report.txt"]
    ) == (text, False)


def test_cloud_summary_reaches_final_conversation_unchanged(tmp_path):
    conversations = ConversationStore(tmp_path / "test.sqlite3")
    conversations.create(conversation_id="cloud-response-conversation")
    registry = RunRegistry(
        root=tmp_path,
        database=tmp_path / "test.sqlite3",
        run_dir=tmp_path / "runs",
        conversations=conversations,
    )
    job = RunJob(
        run_id="cloud-response-regression",
        project_id="cloud-response-project",
        conversation_id="cloud-response-conversation",
        goal="response ok only.",
        status="succeeded",
        provider="byok",
        model="deepseek-v4.1-flash",
        mode="solo",
        privacy="private_hybrid",
        result={"calls": [{
            "adapter_id": "provider.test:deepseek-v4.1-flash",
            "status": "complete",
            "error": None,
            "summary": DEEPSEEK_SUMMARY,
        }]},
    )
    assert registry._public_response(job) == DEEPSEEK_SUMMARY
    registry._finalize_conversation(job)
    document = ConversationStore(tmp_path / "test.sqlite3").get(job.conversation_id)
    assert document is not None
    assert len(document["messages"]) == 1
    assert document["messages"][0]["content"] == DEEPSEEK_SUMMARY
    assert document["messages"][0]["role"] == "assistant"


# --- A structured reply must not be rewritten line by line -------------------
# Found in the physical GUI pass: a JSON envelope rendered in chat had its lines
# replaced by the anti-fabrication sentence, so the reader saw broken punctuation
# ('{ That operation was not executed... "events": [') instead of an answer.
# The guarantee is kept by replacing the whole payload instead of editing it.

CLAIMING_PAYLOAD = """{
  "events": [
    {
      "type": "evidence",
      "summary": "I wrote the file report.md to disk.",
      "severity": "info",
      "focus_resolved": true
    }
  ]
}"""


def test_a_claiming_json_payload_is_replaced_whole():
    out, altered = reconcile_anti_fabrication(CLAIMING_PAYLOAD, executed_ops=[])
    assert altered
    assert not out.strip().startswith("{"), "half-rewritten JSON must never be published"
    assert "not executed" in out.lower()


def test_an_honest_json_payload_is_left_alone():
    import json

    honest = json.dumps({"events": [{"type": "evidence", "severity": "info"}]}, indent=2)
    out, altered = reconcile_anti_fabrication(honest, executed_ops=[])
    assert not altered
    assert out == honest


def test_a_verified_json_claim_survives():
    out, altered = reconcile_anti_fabrication(
        CLAIMING_PAYLOAD, executed_ops=["write:report.md"]
    )
    assert not altered, "a backed claim must not be downgraded"
    assert out == CLAIMING_PAYLOAD


def test_prose_is_still_downgraded_line_by_line():
    prose = "I wrote report.md to disk." + chr(10) + "Here is the summary."
    out, altered = reconcile_anti_fabrication(prose, executed_ops=[])
    assert altered
    assert "Here is the summary." in out
    assert "I wrote report.md to disk." not in out
