"""Independent operation-dispatch tests; external calls are forbidden here."""
import base64
import copy
from pathlib import Path
import pytest
from backend import store, workflow, upstage

ROOT = Path(__file__).resolve().parents[1]
SOURCE = ROOT / 'fixtures/eval_v0/docs/docx-report_minutes-01.docx'


@pytest.fixture(autouse=True)
def isolate(tmp_path, monkeypatch):
    monkeypatch.setattr(store, 'DATA_DIR', tmp_path)
    def forbidden(*args, **kwargs):
        raise AssertionError('Unexpected external analysis invocation')
    monkeypatch.setattr(upstage, 'analyze_file', forbidden)
    monkeypatch.delenv('UPSTAGE_API_KEY', raising=False)


def create_args(ai=False):
    return dict(data=base64.b64encode(SOURCE.read_bytes()).decode(), filename='합성 예시.docx', format='docx', aiEnabled=ai)


def test_ai_off_full_dispatch_flow():
    created = workflow.execute('create', create_args())
    job, token = created['job'], created['token']
    assert job['status'] == 'review' and job['candidates'] and job['aiEnabled'] is False
    args = dict(jobId=job['id'], token=token)
    for bad in (None, '', [], 'wrong'):
        with pytest.raises(store.StoreError):
            workflow.execute('get', {**args, 'token': bad})
    assert workflow.execute('get', args)['id'] == job['id']
    plan = dict(version=job['version'], candidates=[dict(id=c['id'], method='full', mask=[], confirmed=True) for c in job['candidates']], metadataReviewed=True)
    job = workflow.execute('plan', {**args, 'payload': plan})
    with pytest.raises(store.StoreError) as ex:
        workflow.execute('plan', {**args, 'payload': plan})
    assert ex.value.status == 409
    job = workflow.execute('render', {**args, 'payload': {'version': job['version']}})
    assert job['status'] == 'validated'
    with pytest.raises(store.StoreError):
        workflow.execute('download', args)
    ack = dict(version=job['version'], sha256=job['artifact']['sha256'], validationVersion=job['artifact']['validationVersion'])
    job = workflow.execute('ack', {**args, 'payload': ack})
    assert job['acknowledged'] is True
    downloaded = workflow.execute('download', args)
    assert base64.b64decode(downloaded['binary']).startswith(b'PK')
    assert workflow.execute('delete', args)['deleted'] is True
    with pytest.raises(store.StoreError):
        workflow.execute('get', args)


def test_ai_on_missing_key_does_not_leave_job():
    with pytest.raises(store.StoreError) as ex:
        workflow.execute('create', create_args(True))
    assert ex.value.status == 503
    assert not list(store.DATA_DIR.iterdir())


def test_ai_on_nonallowlisted_content_rejected_before_worker(monkeypatch):
    monkeypatch.setenv('UPSTAGE_API_KEY', 'synthetic-test-key')
    monkeypatch.setattr(upstage, 'is_synthetic', lambda path: False)
    with pytest.raises(store.StoreError) as ex:
        workflow.execute('create', create_args(True))
    assert ex.value.status == 422
    assert not list(store.DATA_DIR.iterdir())


@pytest.mark.parametrize('field,value', [('aiEnabled', 'false'), ('format', 'txt'), ('filename', '../copy.docx'), ('filename', ''), ('data', 'not-base64')])
def test_bad_create_input_rejected(field, value):
    args = create_args()
    args[field] = value
    with pytest.raises(store.StoreError):
        workflow.execute('create', args)
    assert not list(store.DATA_DIR.iterdir())


def test_context_preserves_manual_choices_and_invalidates_artifact():
    created = workflow.execute('create', create_args())
    job = created['job']
    args = dict(jobId=job['id'], token=created['token'])
    candidate = job['candidates'][0]
    job = workflow.execute('plan', {**args, 'payload': dict(version=job['version'], candidates=[dict(id=candidate['id'], method='keep', mask=[], confirmed=True)])})
    before = copy.deepcopy(job['candidates'])
    job = workflow.execute('context', {**args, 'payload': dict(version=job['version'], context={'purpose': '외부 제출'})})
    assert job['candidates'] == before
    assert job['context']['purpose'] == '외부 제출'
    assert job['status'] == 'review'


def test_unknown_operation_and_missing_auth_are_rejected():
    for operation, args in [('erase_all', {}), ('get', {}), ('render', {'jobId': 'a'*32, 'token': None})]:
        with pytest.raises(store.StoreError):
            workflow.execute(operation, args)

@pytest.mark.parametrize('failure', ['inspect', 'save', 'store_error', 'submit', 'capacity'])
def test_ai_create_failures_release_capacity_and_remove_unreturned_jobs(monkeypatch, failure):
    import threading
    semaphore = threading.BoundedSemaphore(1)
    monkeypatch.setattr(workflow, 'CAPACITY', semaphore)
    monkeypatch.setenv('UPSTAGE_API_KEY', 'synthetic-test-key')
    monkeypatch.setattr(upstage, 'is_synthetic', lambda path: True)
    if failure == 'inspect':
        def fail(*args, **kwargs): raise ValueError('문서를 읽을 수 없습니다')
        monkeypatch.setattr(store, 'inspect_document', fail)
    elif failure == 'save':
        def fail(*args, **kwargs): raise OSError('private sentinel')
        monkeypatch.setattr(store, 'save_job', fail)
    elif failure == 'store_error':
        def fail(*args, **kwargs): raise store.StoreError(503, 'synthetic_failure', 'synthetic failure')
        monkeypatch.setattr(store, 'save_job', fail)
    elif failure == 'submit':
        def fail(*args, **kwargs): raise RuntimeError('private sentinel')
        monkeypatch.setattr(workflow.EXECUTOR, 'submit', fail)
    else:
        assert semaphore.acquire(blocking=False)
    with pytest.raises(store.StoreError):
        workflow.execute('create', create_args(True))
    assert not list(store.DATA_DIR.iterdir())
    if failure == 'capacity':
        assert not semaphore.acquire(blocking=False)
        semaphore.release()
    assert semaphore.acquire(blocking=False), 'AI capacity leaked after failed create'
    semaphore.release()
