"""Optional review accepts displayed defaults; it does not bypass file validation."""
import copy
from pathlib import Path
import pytest
from backend import plans, store, workflow
from backend.llm_config import MODEL


def completed_analysis():
    return {**{stage: {'status': 'completed'} for stage in ('parse', 'classify', 'extract')},
            'hermes': {'status': 'completed', 'required': True, 'model': MODEL}, 'warnings': []}


@pytest.fixture
def job(monkeypatch):
    monkeypatch.setenv('GARIMI_REQUIRE_UPSTAGE', '1')
    c = dict(id='a', type='name', value='가상인', unitId='u', start=0, end=3, method='full',
             mask=[], confirmed=False, locationResolved=True, decisionSource='ai_default')
    return dict(version=1, status='review', aiEnabled=True, analysis=completed_analysis(),
                candidates=[c, {**copy.deepcopy(c), 'id': 'b', 'method': 'keep', 'start': 4, 'end': 7},
                            {**copy.deepcopy(c), 'id': 'c', 'method': 'partial', 'mask': [[1, 2]],
                             'confirmed': True, 'decisionSource': 'user', 'start': 8, 'end': 11}],
                metadata=[], metadataReviewed=False, artifact=None, acknowledged=False,
                suggestions=[{'candidateId': 'a', 'type': 'question', 'question': '남길까요?'}])


def test_no_individual_confirmation_required(job):
    original = [(c['method'], c['mask']) for c in job['candidates']]
    assert plans.prepare_export(job, {'version': 1})
    assert all(c['confirmed'] for c in job['candidates'])
    assert [(c['method'], c['mask']) for c in job['candidates']] == original
    assert [c['decisionSource'] for c in job['candidates']] == ['ai_automatic', 'ai_automatic', 'user']
    assert not job['acknowledged'] and job['artifact'] is None and not job['metadataReviewed']


def test_pending_edit_is_applied_and_manual_exception_preserved(job):
    assert plans.prepare_export(job, {'version': 1, 'candidates': [dict(id='a', method='delete', mask=[], confirmed=True)]})
    assert job['candidates'][0]['method'] == 'delete'
    assert job['candidates'][0]['decisionSource'] == 'user'
    assert job['candidates'][1]['method'] == 'keep'
    assert job['candidates'][2]['mask'] == [[1, 2]]


def test_prepared_plan_is_idempotent_and_preserves_existing_copy(job):
    plans.prepare_export(job, {'version': 1})
    job.update(status='validated', artifact={'sha256': 'existing'}, acknowledged=True)
    before = copy.deepcopy(job)
    assert plans.prepare_export(job, {'version': 1}) is False
    assert job == before


@pytest.mark.parametrize('fault', ['unresolved', 'incomplete', 'failed', 'busy', 'stale', 'legacy', 'blocked', 'invalid_edit'])
def test_required_processing_guards_remain_atomic(job, fault):
    payload = {'version': 1}
    if fault == 'unresolved': job['candidates'][0]['locationResolved'] = False
    elif fault == 'incomplete': job['analysis']['incomplete'] = True
    elif fault == 'failed': job['analysis']['hermes']['status'] = 'failed'
    elif fault == 'busy': job['status'] = 'rendering'
    elif fault == 'stale': payload['version'] = 0
    elif fault == 'legacy': job['aiEnabled'] = False
    elif fault == 'blocked': job['processingBlocked'] = 'blocked'
    else: payload['candidates'] = [dict(id='a', method='partial', mask=[[0, 3]], confirmed=True)]
    before = copy.deepcopy(job)
    with pytest.raises(store.StoreError): plans.prepare_export(job, payload)
    assert job == before


@pytest.mark.parametrize('edits', [None, {}, [dict(id='a', method='keep', mask=[], confirmed=False)], [42]])
def test_invalid_overrides_rejected(job, edits):
    before = copy.deepcopy(job)
    with pytest.raises(store.StoreError): plans.prepare_export(job, {'version': 1, 'candidates': edits})
    assert job == before


def test_optional_review_then_real_docx_render_still_requires_final_ack(tmp_path, monkeypatch):
    monkeypatch.setattr(store, 'DATA_DIR', tmp_path)
    monkeypatch.setenv('GARIMI_REQUIRE_UPSTAGE', '0')
    source = Path(__file__).resolve().parents[1] / 'fixtures/eval_v0/docs/docx-report_minutes-01.docx'
    created, token = store.create_job(source.read_bytes(), '합성문서.docx', 'docx', False)
    monkeypatch.setenv('GARIMI_REQUIRE_UPSTAGE', '1')
    created.update(aiEnabled=True, status='review', analysis=completed_analysis())
    assert created['candidates']
    store.save_job(created)
    args = dict(jobId=created['id'], token=token)
    prepared = workflow.execute('prepare-export', {**args, 'payload': {'version': created['version']}})
    assert all(c['confirmed'] for c in prepared['candidates'])
    assert all(c['decisionSource'] == 'ai_automatic' for c in prepared['candidates'])
    with pytest.raises(store.StoreError): workflow.execute('download', args)
    planned = workflow.execute('plan', {**args, 'payload': {'version': prepared['version'], 'metadataReviewed': True}})
    rendered = workflow.execute('render', {**args, 'payload': {'version': planned['version']}})
    assert rendered['status'] == 'validated'
    with pytest.raises(store.StoreError): workflow.execute('download', args)
    artifact = rendered['artifact']
    workflow.execute('ack', {**args, 'payload': {'version': rendered['version'], 'sha256': artifact['sha256'], 'validationVersion': artifact['validationVersion']}})
    assert workflow.execute('download', args)['binary']
