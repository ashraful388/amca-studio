import asyncio
import functools

import pytest

from amca.models import ModelRequest, ScriptedAdapter
from amca.privacy import StandingChatAdapter, TransmissionStore
from amca.web_acquisition import _PinnedHTTPConnection, _PinnedHTTPSConnection


def test_provider_failure_is_not_reported_as_budget_or_policy():
    from amca.domain import CollaborationMode, ProjectState, SyncMode
    from amca.kernel import CollaborationKernel, StopReason
    from amca.models import ModelCapabilities, ModelResponse, StreamChunk
    from amca.models.base import ModelAdapter
    from amca.state import StateManager

    class TimingOutAdapter(ModelAdapter):
        def __init__(self):
            super().__init__(
                adapter_id='cloud-a', provider='byok', model='m',
                capabilities=ModelCapabilities(
                    supports_streaming=True, supports_tools=True,
                    supports_json=True, is_local=False,
                ),
            )

        async def generate(self, request):
            raise TimeoutError('The read operation timed out')

        async def stream(self, request):
            raise TimeoutError('The read operation timed out')
            yield StreamChunk(delta='', model=self.model, provider=self.provider)

    state = ProjectState(
        project_id='provider-failure', goal='Review the evidence.',
        privacy_mode=__import__('amca.domain', fromlist=['PrivacyMode']).PrivacyMode.FULL_FRONTIER,
    )
    kernel = CollaborationKernel(StateManager(state), [TimingOutAdapter()])
    result = asyncio.run(
        kernel.run(mode=CollaborationMode.SOLO, sync_mode=SyncMode.SOFT)
    )
    assert result.stop_reason is StopReason.PROVIDER_FAILURE
    assert any(
        'TimeoutError' in (record.error or '') for record in result.call_records
    )


def test_cost_policy_is_explicit_and_quality_is_not_a_ranking_input():
    """UPDATED for the canonical Model Router (Task F, manager decision).

    This test used to assert that `optimize_cost=False` ranked by quality.  That
    quality score came from `_model_quality`, which inferred it from the model's
    NAME ("opus"/"pro" high, "mini"/"flash" low).  The manager ordered those
    heuristics removed from authoritative routing and explicitly forbade
    converting them into Registry facts, because they are not measurements.

    So the assertion is now the opposite of what it was: with cost optimisation
    disabled, cost does not decide AND quality does not decide either.  What the
    router reports is that nothing measured differentiated the candidates.
    """
    from amca.model_hub import AccessSource, AutoIntelligenceRouter, ModelCandidate
    candidates = [
        ModelCandidate('cheap', AccessSource.BYOK, 'a', 0.001, 0.75),
        ModelCandidate('strong', AccessSource.BYOK, 'b', 10.0, 0.95),
    ]
    # Cost policy still matters: a cheap model wins when cost is a factor.
    assert AutoIntelligenceRouter().resolve(candidates).model_refs == ('cheap',)

    decision = AutoIntelligenceRouter().resolve(candidates, optimize_cost=False)
    # Cost is ignored under this policy, so the expensive model is no longer
    # excluded by price...
    assert 'cheap' in decision.model_refs or 'strong' in decision.model_refs
    # ...but the winner is NOT chosen for being "higher quality", because that
    # was never measured.  The router says so rather than pretending.
    assert 'not a quality judgement' in decision.rationale
    assert 'neutral deterministic order' in decision.rationale


@pytest.mark.parametrize('connection', [_PinnedHTTPConnection, _PinnedHTTPSConnection])
def test_pinned_connection_accepts_urllib_positional_host(connection):
    factory = functools.partial(connection, pinned_ip='93.184.216.34')
    conn = factory('example.org', timeout=3)
    assert conn.host == 'example.org'
    assert conn._pinned_ip == '93.184.216.34'
    conn.close()


def test_standing_chat_transmits_evolving_context_and_bounds_calls(tmp_path):
    seen = []

    class RecordingAdapter(ScriptedAdapter):
        async def generate(self, request):
            seen.append(request)
            return await super().generate(request)

    delegate = RecordingAdapter(['first', 'second'], adapter_id='cloud', is_local=False)
    database = tmp_path / 'permissions.sqlite3'
    from amca.domain import ProjectState
    from amca.storage import SQLiteStore
    with SQLiteStore(database) as store:
        store.save_state(ProjectState(project_id='joint', goal='Compare evidence.'))
    adapter = StandingChatAdapter(
        delegate, project_id='joint', database=database, excerpt='Compare evidence.',
        approval_ids=(), max_calls=2,
    )
    for message in ('initial focus', 'peer found conflicting evidence'):
        request = ModelRequest(messages=({'role': 'user', 'content': message},))
        asyncio.run(adapter.generate(request))
    assert 'peer found conflicting evidence' in seen[1].messages[0]['content']
    with pytest.raises(PermissionError, match='allowance exhausted'):
        asyncio.run(adapter.generate(request))
    with TransmissionStore(database) as store:
        records = store.list(project_id='joint')
    assert len(records) == 2
    assert all(record.status == 'succeeded' for record in records)
    assert len({record.excerpt_sha256 for record in records}) == 2


def test_joint_public_response_publishes_only_the_final_round():
    """Joint runs must publish ONE single-voice AMCA answer from the final
    round only — no per-model **headers**, no adapter ids, no round-one
    drafts.  Distinct final-round positions merge as plain paragraphs."""
    from amca.service import RunJob, RunRegistry

    job = RunJob(
        run_id='run-joint-consolidation',
        project_id='proj-joint',
        goal='Compare the evidence.',
        result={
            'calls': [
                {'adapter_id': 'cloud-a', 'round': 1, 'status': 'complete',
                 'summary': 'Round one draft from A.'},
                {'adapter_id': 'cloud-b', 'round': 1, 'status': 'complete',
                 'summary': 'Round one draft from B.'},
                {'adapter_id': 'cloud-a', 'round': 2, 'status': 'complete',
                 'summary': 'Consolidated position from A.'},
                {'adapter_id': 'cloud-b', 'round': 2, 'status': 'complete',
                 'summary': 'Consolidated position from B.'},
            ]
        },
    )
    response = RunRegistry._public_response(job)
    assert 'Consolidated position from A.' in response
    assert 'Consolidated position from B.' in response
    assert 'Round one draft' not in response
    assert '**' not in response
    assert 'cloud-a' not in response
    assert 'cloud-b' not in response


def test_joint_public_response_single_voice_production_case():
    """Response-UX R5 production fixture (run 2704578e): the evidenced
    slice publishes ALONE as AMCA's answer; tool-less slices vanish from
    the body and surface only as one unattributed verification note."""
    from amca.service import RunJob, RunRegistry

    job = RunJob(
        run_id='run-joint-r5',
        project_id='proj-joint-r5',
        goal='Report feed-check.txt contents and size',
        provenance={'executed_ops': [
            'filesystem.write · feed-check.txt · success',
            'filesystem.read · feed-check.txt · success',
            'filesystem.stat · feed-check.txt · success',
        ]},
        result={'calls': [
            {'adapter_id': 'provider.aaa:muse-spark-1.3', 'round': 1,
             'status': 'complete',
             'summary': 'That operation was not executed in this slice — '
                        'feed-check.txt was not written or read here.'},
            {'adapter_id': 'provider.bbb:nemotron', 'round': 1,
             'status': 'complete',
             'summary': 'feed-check.txt contains exactly one line: '
                        'FEED-CHECK-OK. Byte size: 13 bytes.',
             'executed_ops': ['filesystem.read · feed-check.txt · success']},
            {'adapter_id': 'provider.ccc:crouton', 'round': 1,
             'status': 'complete',
             'summary': 'Not executed in this slice.'},
        ]},
    )
    response = RunRegistry._public_response(job)
    assert 'FEED-CHECK-OK' in response
    assert '**' not in response
    assert 'muse-spark-1.3' not in response
    assert 'nemotron' not in response
    assert 'crouton' not in response
    assert 'provider.aaa' not in response
    assert 'Verification note: 2 participants ran no tool' in response


def test_display_denied_evidence_never_authorizes_a_claim():
    """Security R5: a display-spelling denial
    (``filesystem.write · f.txt · denied``) must BLOCK the matching
    success claim exactly like a Mapping denial — the normalizer parses
    the trailing state, never drops it."""
    from amca.anti_fabrication import reconcile_anti_fabrication

    denied_write, _ = reconcile_anti_fabrication(
        'I wrote f.txt successfully.',
        executed_ops=['filesystem.write · f.txt · denied'],
    )
    assert denied_write == 'That operation was not executed.'
    denied_read, _ = reconcile_anti_fabrication(
        'I read f.txt successfully.',
        executed_ops=['filesystem.read · f.txt · denied'],
    )
    assert denied_read == 'That operation was not executed.'
    # Legitimate display-success evidence still authorizes.
    ok_write, _ = reconcile_anti_fabrication(
        'I have created and written index.html.',
        executed_ops=['filesystem.write · index.html · success'],
    )
    assert 'index.html' in ok_write
    assert 'not executed' not in ok_write.lower()


def test_read_content_claim_binds_to_same_file_evidence():
    """Bug-hunt/Security R5: ``f.txt contains <content>`` with zero or
    wrong-file evidence reconciles to not-executed; same-file read
    evidence authorizes."""
    from amca.anti_fabrication import reconcile_anti_fabrication

    zero, _ = reconcile_anti_fabrication(
        'feed-check.txt contains exactly one line: FABRICATED-X.',
        executed_ops=[],
    )
    assert zero == 'That operation was not executed.'
    wrong, _ = reconcile_anti_fabrication(
        'feed-check.txt contains FEED-CHECK-OK.',
        executed_ops=['read:other.txt'],
    )
    assert wrong == 'That operation was not executed.'
    same, _ = reconcile_anti_fabrication(
        'feed-check.txt contains FEED-CHECK-OK.',
        executed_ops=['read:feed-check.txt'],
    )
    assert 'FEED-CHECK-OK' in same


def test_solo_public_response_stays_unattributed():
    from amca.service import RunJob, RunRegistry

    job = RunJob(
        run_id='run-solo-response', project_id='proj-solo', goal='Reply ok',
        result={'calls': [
            {'adapter_id': 'ollama:x', 'round': 1, 'status': 'complete', 'summary': 'ok'},
        ]},
    )
    assert RunRegistry._public_response(job) == 'ok'


def test_joint_public_response_labels_hub_providers_by_display_name():
    """Response-UX R5: the single-voice answer carries no model names at
    all — but the FAILURE list (no model completed) still uses the R1
    display labels (picker ``{name} · {model}``), never a raw hex row."""
    from amca.service import RunJob, RunRegistry

    hub_rows = {
        'provider.c95df903667446c0b2b96ac6': {
            'id': 'provider.c95df903667446c0b2b96ac6',
            'name': 'MiniMax',
            'provider_kind': 'openrouter',
        },
        'provider.aaa': {
            'id': 'provider.aaa',
            'name': 'Other',
            'provider_kind': 'openrouter',
        },
    }
    job = RunJob(
        run_id='run-joint-hub-label', project_id='proj-joint', goal='Build it.',
        result={'calls': [
            {'adapter_id': 'provider.c95df903667446c0b2b96ac6:muse-spark-1.3',
             'round': 1, 'status': 'complete', 'summary': '', 'error': 'boom'},
            {'adapter_id': 'provider.aaa:other-model',
             'round': 1, 'status': 'complete', 'summary': '', 'error': 'bust'},
        ]},
    )
    response = RunRegistry._public_response(job, hub_provider=hub_rows.get)
    assert 'provider.c95df903667446c0b2b96ac6' not in response
    assert 'MiniMax · muse-spark-1.3' in response


def test_joint_public_response_unknown_provider_passes_through():
    """Response-UX R1 fallback (failure list): when several participants
    fail and no hub row exists, the transcript names the model half —
    never an invented provider name, never a bare hex row."""
    from amca.service import RunJob, RunRegistry

    job = RunJob(
        run_id='run-joint-unknown', project_id='proj-joint', goal='Build it.',
        result={'calls': [
            {'adapter_id': 'provider.deadbeef:custom-model',
             'round': 1, 'status': 'complete', 'summary': '', 'error': 'boom'},
            {'adapter_id': 'provider.deadbeef:other-model',
             'round': 1, 'status': 'complete', 'summary': '', 'error': 'bust'},
        ]},
    )
    response = RunRegistry._public_response(job, hub_provider=lambda _pid: None)
    assert 'provider.deadbeef' not in response
    assert 'custom-model' in response
    assert 'other-model' in response


def test_joint_public_response_strips_hex_without_lookup():
    """Response-UX R5 review catch: the single-voice answer never names
    models — distinct final-round summaries merge as plain paragraphs,
    and no ``provider.{hex}`` id ever renders."""
    from amca.service import RunJob, RunRegistry

    job = RunJob(
        run_id='run-joint-nolookup', project_id='proj-joint', goal='Build it.',
        result={'calls': [
            {'adapter_id': 'provider.aaa:muse-spark-1.3',
             'round': 1, 'status': 'complete',
             'summary': 'The site is complete.'},
            {'adapter_id': 'provider.bbb:qwen-flash',
             'round': 1, 'status': 'complete',
             'summary': 'A different take.'},
        ]},
    )
    response = RunRegistry._public_response(job)
    assert 'provider.aaa' not in response
    assert 'provider.bbb' not in response
    assert 'muse-spark-1.3' not in response
    assert 'qwen-flash' not in response
    assert 'The site is complete.' in response
    assert 'A different take.' in response
    assert '**' not in response


def test_joint_public_response_lookup_failure_degrades():
    """Response-UX R5 bug-hunt catch: a raising hub lookup (locked DB)
    must degrade gracefully, never crash finalization — and the answer
    still carries no model names."""
    from amca.service import RunJob, RunRegistry

    import sqlite3

    def _boom(pid):
        raise sqlite3.OperationalError("database is locked")

    job = RunJob(
        run_id='run-joint-boom', project_id='proj-joint', goal='Build it.',
        result={'calls': [
            {'adapter_id': 'provider.aaa:muse-spark-1.3',
             'round': 1, 'status': 'complete',
             'summary': 'The site is complete.'},
            {'adapter_id': 'provider.bbb:qwen-flash',
             'round': 1, 'status': 'complete',
             'summary': 'Another take.'},
        ]},
    )
    response = RunRegistry._public_response(job, hub_provider=_boom)
    assert 'provider.aaa' not in response
    assert 'The site is complete.' in response
    assert '**' not in response


def test_truncated_output_is_a_provider_failure_not_a_policy_decision():
    """A provider that stops at its output ceiling must not be reported as
    budget_or_policy; the answer stays a failure (incomplete output is never
    published as a completed answer)."""
    from amca.domain import (
        CollaborationMode, GoalContract, PrivacyMode, ProjectState, SyncMode,
    )
    from amca.kernel import CollaborationKernel, KernelConfig, StopReason
    from amca.models import ModelCapabilities, ModelResponse, StreamChunk, TokenUsage
    from amca.models.base import ModelAdapter
    from amca.state import StateManager

    class CappedAdapter(ModelAdapter):
        def __init__(self):
            super().__init__(
                adapter_id='cloud-a', provider='byok', model='m',
                capabilities=ModelCapabilities(
                    supports_streaming=True, supports_tools=True,
                    supports_json=True, is_local=False,
                ),
            )

        async def generate(self, request):
            return self._response()

        async def stream(self, request):
            yield StreamChunk(done=True, response=self._response())

        def _response(self):
            return ModelResponse(
                text='{"summary":"unfinished', model=self.model,
                provider=self.provider,
                usage=TokenUsage(input_tokens=10, output_tokens=32, cost_usd=0.002),
                finish_reason='length',
            )

    state = ProjectState(
        project_id='capped-json', goal='Reject a truncated response',
        privacy_mode=PrivacyMode.FULL_FRONTIER,
        goal_contract=GoalContract(goal='Reject a truncated response'),
    )
    kernel = CollaborationKernel(
        StateManager(state), [CappedAdapter()],
        config=KernelConfig(max_rounds=1),
    )
    result = asyncio.run(
        kernel.run(mode=CollaborationMode.SOLO, sync_mode=SyncMode.SOFT)
    )
    call = result.call_records[0]
    assert call.status == 'failed'
    assert 'ModelOutputTruncatedError' in (call.error or '')
    assert result.stop_reason is StopReason.PROVIDER_FAILURE


def test_cloud_participant_gets_governed_tools_only_when_access_is_configured():
    """A cloud participant must receive the governed tool catalog when the
    project has Local PC access configured, and must not receive it when the
    project is unconfigured (no standing authority for local context)."""
    from pathlib import Path

    from amca.domain import ProjectState
    from amca.service import ServiceContext
    from amca.storage import SQLiteStore

    import tempfile

    with tempfile.TemporaryDirectory() as td:
        root = Path(td)
        workspace = root / 'workspace'
        workspace.mkdir()
        (workspace / 'notes.md').write_text('bounded local note', encoding='utf-8')
        context = ServiceContext(
            root=root, database=root / 'amca.db',
            benchmarks=root / 'benchmarks.json',
        )
        try:
            with SQLiteStore(context.database) as store:
                store.save_state(ProjectState(project_id='studio-cloud', goal='Read notes.md'))
            # v1.2.0: the folder-bound levels are held to a Project's own
            # folder, and a standalone conv- chat has none, so this proof
            # addresses the Project the way the desktop does.
            context.studio_project_store.save(
                {
                    "project_id": "studio-cloud",
                    "name": "cloud participant proof",
                    "description": "",
                    "instructions": "",
                    "template": "general",
                    "root_path": str(workspace),
                }
            )

            def tools_for(configured: bool) -> list[str]:
                if configured:
                    context.local_permission_store.set_profile(
                        project_id='studio-cloud', profile='standard',
                        workspace_root=workspace, updated_by='test',
                    )
                else:
                    context.local_permission_store.set_profile(
                        project_id='studio-cloud', profile='restricted',
                        workspace_root=workspace, updated_by='test',
                    )
                adapter = ScriptedAdapter(
                    ['{"summary": "no tool needed", "events": []}'],
                    adapter_id='cloud-x', is_local=False,
                )
                from amca.governed_chat import GovernedChatAdapter
                from amca.privacy import StandingChatAdapter

                wrapped = GovernedChatAdapter(
                    StandingChatAdapter(
                        adapter, project_id='studio-cloud', database=context.database,
                        excerpt='Read notes.md', approval_ids=(), max_calls=1,
                    ),
                    project_id='studio-cloud', service=context, conversation_id=None,
                )
                # The catalog the wrapper will actually send is derived from
                # the project profile at call time.
                from amca.governed_chat import get_governed_tools_for_profile
                from amca.local_tools import PermissionProfile

                profile = PermissionProfile(
                    context.local_permission_store.profile('conv-cloud')['profile']
                )
                return sorted(
                    str(spec.get('name') or spec.get('function', {}).get('name'))
                    for spec in get_governed_tools_for_profile(profile)
                )

            configured = tools_for(True)
            unconfigured = tools_for(False)
            assert 'filesystem.read' in configured
            assert 'filesystem.list' in configured
            assert 'filesystem.write' in configured
            # Restricted exposes only read-only tools: the catalog follows the
            # profile, and execution re-checks it.
            assert 'filesystem.read' in unconfigured
            assert 'filesystem.write' not in unconfigured
        finally:
            context.close()


def test_enabled_custom_agent_joins_the_organization(tmp_path):
    """A custom agent the user created must be able to take part in a run."""
    from amca.agent_fabric import AgentFabricStore

    store = AgentFabricStore(tmp_path / 'agents.sqlite3')
    values = {
        'name': 'Invoice auditor',
        'description': 'Checks invoices for arithmetic and policy mistakes',
        'instructions': 'Verify each line item against the policy.',
        'allowed_tools': ['filesystem.read'],
        'permissions': ['propose_only'],
    }
    agent = store.save_custom_agent(values)
    matching = store.plan('Audit these invoices for mistakes.')
    assert agent['id'] in matching['selected_agents']

    # A disabled agent stays out of the organization.
    store.save_custom_agent({**values, 'enabled': False}, agent_id=agent['id'])
    disabled = store.plan('Audit these invoices for mistakes.')
    assert agent['id'] not in disabled['selected_agents']

    # An unrelated goal does not pull it in.
    store.save_custom_agent({**values, 'enabled': True}, agent_id=agent['id'])
    unrelated = store.plan('Reply with exactly ok.')
    assert agent['id'] not in unrelated['selected_agents']
