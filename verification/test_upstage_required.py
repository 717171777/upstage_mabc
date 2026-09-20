"""Mandatory-provider policy contracts. External calls are always blocked/stubbed."""
import asyncio
import base64
import copy
import json
import threading
from pathlib import Path

import pytest

from backend import analysis, auto_export, judge, plans, store, upstage, workflow, workflow_files
from backend.processing_requirements import assert_upstage_complete

ROOT = Path(__file__).resolve().parents[1]
SOURCE = ROOT/'fixtures/eval_v0/docs/docx-report_minutes-01.docx'


class CapturedExecutor:
    def __init__(self): self.calls = []
    def submit(self, *args): self.calls.append(args)


@pytest.fixture(autouse=True)
def required_policy(tmp_path, monkeypatch):
    monkeypatch.setattr(store, 'DATA_DIR', tmp_path)
    monkeypatch.setenv('GARIMI_REQUIRE_UPSTAGE', '1')
    monkeypatch.setenv('GARIMI_ALLOW_USER_DOCUMENTS', '1')
    monkeypatch.setenv('UPSTAGE_API_KEY', 'test-placeholder-never-sent')
    monkeypatch.setenv('ANTHROPIC_API_KEY', 'test-placeholder-never-sent')
    def deny(*args, **kwargs):
        pytest.fail('Policy test attempted an external API or Hermes process')
    monkeypatch.setattr(upstage, '_post_with_retry', deny)
    monkeypatch.setattr(judge, 'judge_exceptions', deny)
    monkeypatch.setattr(judge.subprocess, 'run', deny)
    executor = CapturedExecutor()
    monkeypatch.setattr(workflow, 'EXECUTOR', executor)
    monkeypatch.setattr(workflow, 'CAPACITY', threading.BoundedSemaphore(4))
    return executor


def create_args(enabled=True):
    return {'data': base64.b64encode(SOURCE.read_bytes()).decode(),
            'filename': '일반 문서.docx', 'format': 'docx', 'aiEnabled': enabled}


def seed_completed():
    job, token = store.create_job(SOURCE.read_bytes(), '검증.docx', 'docx', True)
    job['analysis'] = {stage: {'status': 'completed', 'model': f'test-{stage}'}
                       for stage in ('parse', 'classify', 'extract')}
    job['analysis'].update(hermes={'status': 'completed', 'model': 'claude-sonnet-5', 'required': True}, warnings=[])
    plans.apply_plan(job, {'version': job['version'], 'metadataReviewed': True,
        'candidates': [{'id': c['id'], 'method': 'full', 'mask': [], 'confirmed': True}
                       for c in job['candidates']]})
    store.save_job(job)
    return job, token


def test_required_rejects_ai_off_before_persisting_or_submitting(required_policy):
    with pytest.raises(store.StoreError) as error:
        workflow.execute('create', create_args(False))
    assert error.value.status == 422 and error.value.code == 'UPSTAGE_REQUIRED'
    assert not list(store.DATA_DIR.iterdir()) and not required_policy.calls
    with pytest.raises(store.StoreError):
        store.create_job(SOURCE.read_bytes(), '직접.docx', 'docx', False)
    with pytest.raises(ValueError, match='필수'):
        upstage.analyze_file(SOURCE, False)


@pytest.mark.parametrize('key', ['', '   '])
def test_missing_key_fails_clearly_before_persisting(key, monkeypatch, required_policy):
    monkeypatch.setenv('UPSTAGE_API_KEY', key)
    with pytest.raises(store.StoreError) as error:
        workflow.execute('create', create_args())
    assert error.value.status == 503 and error.value.code == 'UPSTAGE_UNAVAILABLE'
    assert 'API 키' in error.value.message
    assert not list(store.DATA_DIR.iterdir()) and not required_policy.calls


def test_general_document_opt_in_is_separate_and_create_queues_analysis(monkeypatch, required_policy):
    monkeypatch.setattr(upstage, 'is_synthetic', lambda path: False)
    created = workflow.execute('create', create_args())
    job = created['job']
    assert job['aiEnabled'] is True and job['status'] == 'analyzing'
    assert job['upstageRequired'] is True and job['artifact'] is None
    assert len(required_policy.calls) == 1
    assert all(job['analysis'][stage]['status'] == 'pending'
               for stage in ('parse', 'classify', 'extract', 'hermes'))
    monkeypatch.delenv('GARIMI_ALLOW_USER_DOCUMENTS')
    with pytest.raises(store.StoreError) as error:
        workflow.execute('create', create_args())
    assert error.value.code == 'AI_ONLY_SYNTHETIC'
    assert len(required_policy.calls) == 1


@pytest.mark.parametrize('operation', ['render', 'auto-export', 'ack', 'download', 'preview'])
def test_legacy_local_complete_cannot_bypass_required_policy(operation, monkeypatch):
    with monkeypatch.context() as legacy_env:
        legacy_env.delenv('GARIMI_REQUIRE_UPSTAGE')
        job, token = store.create_job(SOURCE.read_bytes(), '이전 작업.docx', 'docx', False)
        auto_export.run(job, {'version': job['version'], 'mode': 'local_automatic'})
    before = copy.deepcopy(job)
    persisted = (store.workspace(job['id'])/'job.json').read_bytes()
    saved_path = store.workspace(job['id'])/job['_artifactName']
    saved_bytes = saved_path.read_bytes()
    args = {'jobId': job['id'], 'token': token, 'variant': 'copy',
            'payload': {'version': job['version'], 'mode': 'local_automatic',
                        'validationVersion': job['artifact']['validationVersion'],
                        'sha256': job['artifact']['sha256']}}
    with pytest.raises(store.StoreError) as error:
        workflow.execute(operation, args)
    assert error.value.code == 'UPSTAGE_REANALYSIS_REQUIRED'
    public = store.public_job(job)
    assert public['status'] == 'review' and public['artifact'] is None
    assert public['acknowledged'] is False and public['upstageReanalysisRequired'] is True
    assert 'Upstage' in public['processingBlocked']
    assert job == before and (store.workspace(job['id'])/'job.json').read_bytes() == persisted
    assert saved_path.read_bytes() == saved_bytes
    assert store.load_job(job['id'], token)['status'] == 'validated'


@pytest.mark.parametrize('stage,status', [(stage, status)
    for stage in ('parse', 'classify', 'extract', 'hermes')
    for status in ('pending', 'running', 'failed', 'disabled', 'partial')])
def test_manual_confirmation_cannot_replace_incomplete_provider_stage(stage, status):
    job, _ = seed_completed()
    job['analysis'][stage]['status'] = status
    before = copy.deepcopy(job)
    with pytest.raises(store.StoreError) as error:
        workflow_files.render_copy(job, {'version': job['version']})
    assert error.value.code == 'UPSTAGE_ANALYSIS_INCOMPLETE'
    with pytest.raises(store.StoreError):
        auto_export.run(job, {'version': job['version'], 'mode': 'ai_automatic'})
    assert job == before and not list(store.workspace(job['id']).glob('copy-*'))


@pytest.mark.parametrize('hermes', [
    {'status': 'not_needed'}, {'status': 'not_needed', 'required': True},
    {'status': 'completed', 'model': 'different-model', 'required': True},
])
def test_needed_or_wrong_model_hermes_cannot_be_silently_skipped(hermes):
    job, _ = seed_completed(); job['analysis']['hermes'] = hermes
    with pytest.raises(store.StoreError): assert_upstage_complete(job)


def test_explicitly_unneeded_hermes_and_completed_stages_allow_verified_copy():
    job, _ = seed_completed()
    job['analysis']['hermes'] = {'status': 'not_needed', 'required': False, 'model': None}
    assert_upstage_complete(job)
    workflow_files.render_copy(job, {'version': job['version']})
    artifact = job['artifact']
    workflow_files.acknowledge(job, {'version': job['version'],
        'validationVersion': artifact['validationVersion'], 'sha256': artifact['sha256']})
    assert job['status'] == 'validated' and workflow_files.download(job)['binary']
    # Even a previously saved file becomes inaccessible if a required result is invalid.
    job['analysis']['extract']['status'] = 'failed'
    with pytest.raises(store.StoreError): workflow_files.artifact_path(job, ack_required=True)
    with pytest.raises(store.StoreError): workflow_files.download(job)


def test_failed_provider_analysis_leaves_review_without_local_completion(monkeypatch):
    job, token = seed_completed(); job['status'] = 'analyzing'; store.save_job(job)
    monkeypatch.setattr(upstage, 'analyze_file', lambda *args, **kwargs: {
        'documentType': 'report_minutes', 'stages': {
            'parse': {'status': 'failed'}, 'classify': {'status': 'completed'},
            'extract': {'status': 'failed'}}, 'warnings': ['분석 실패'], 'extracted': {}})
    monkeypatch.setattr(judge, 'judge_exceptions', lambda payload: {
        'status': 'completed', 'model': 'claude-sonnet-5', 'suggestions': [], 'warnings': []})
    analysis.run_analysis(job['id'], token)
    fresh = store.load_job(job['id'], token)
    assert fresh['status'] == 'review' and fresh['artifact'] is None
    assert fresh['analysis']['hermes']['required'] is True
    with pytest.raises(store.StoreError):
        workflow_files.render_copy(fresh, {'version': fresh['version']})


def test_unset_requirement_retains_independent_local_mode(monkeypatch):
    monkeypatch.delenv('GARIMI_REQUIRE_UPSTAGE')
    monkeypatch.delenv('UPSTAGE_API_KEY')
    created = workflow.execute('create', create_args(False))
    assert created['job']['aiEnabled'] is False
    result = workflow.execute('auto-export', {'jobId': created['job']['id'], 'token': created['token'],
        'payload': {'version': created['job']['version'], 'mode': 'local_automatic'}})
    assert result['status'] == 'validated'


def test_health_reports_required_policy_and_key_configuration(monkeypatch):
    from backend.main import health
    result = json.loads(asyncio.run(health()).body)
    assert result['upstageRequired'] is True and result['syntheticOnly'] is False
    assert result['aiConfigured'] is True
    monkeypatch.delenv('UPSTAGE_API_KEY')
    assert json.loads(asyncio.run(health()).body)['aiConfigured'] is False
