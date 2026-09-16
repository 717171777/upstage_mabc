"""Independent storage review; tests must not be edited by the implementation agent."""
from copy import deepcopy
from pathlib import Path
import json
import time
import os
import threading
import pytest

ROOT=Path(__file__).resolve().parents[1]

@pytest.fixture
def store(tmp_path,monkeypatch):
    from backend import store as module
    monkeypatch.setattr(module,'DATA_DIR',tmp_path)
    return module

def create(store):
    data=(ROOT/'fixtures/eval_v0/docs/docx-report_minutes-01.docx').read_bytes()
    return store.create_job(data,'합성 예시.docx','docx',False)

def test_private_original_and_public_projection(store):
    job,token=create(store)
    public=store.public_job(job)
    assert not any(k.startswith('_') for k in public)
    serialized=json.dumps(public,ensure_ascii=False)
    assert token not in serialized and str(store.DATA_DIR) not in serialized
    saved=json.loads((store.workspace(job['id'])/'job.json').read_text())
    assert token not in json.dumps(saved)
    assert store.source_path(job).read_bytes()==(ROOT/'fixtures/eval_v0/docs/docx-report_minutes-01.docx').read_bytes()
    assert store.load_job(job['id'],token)['id']==job['id']
    with pytest.raises(store.StoreError):store.load_job(job['id'],'wrong-token')

def test_actions_do_not_mutate_original_inspection(store):
    job,_=create(store);original=deepcopy(job['_inspection'])
    job['metadata'][0]['action']='keep'
    assert job['_inspection']==original
    public=store.public_job(job);public['metadata'][0]['action']='delete'
    assert job['metadata'][0]['action']=='keep'

def test_path_traversal_rejected(store):
    for jid in ('../x','/tmp/x','x'*32,'0'*31,'0'*33):
        with pytest.raises((store.StoreError,ValueError)):store.workspace(jid)

def test_expiry_deletes_files(store):
    job,token=create(store);path=store.workspace(job['id'])
    job['_expiresEpoch']=time.time()-1;store.save_job(job)
    with pytest.raises(store.StoreError) as error:store.load_job(job['id'],token)
    assert error.value.status==410
    assert not path.exists()

def test_invalidation_binds_new_version(store):
    job,_=create(store);path=store.workspace(job['id'])/'copy-v1.docx';path.write_bytes(b'SYNTHETIC')
    job['_artifactName']=path.name;job['artifact']={'sha256':'example'};job['acknowledged']=True
    before=job['version'];choices=deepcopy(job['candidates'])
    store.invalidate(job)
    assert job['version']==before+1 and job['artifact'] is None and not job['acknowledged']
    assert job['candidates']==choices and not path.exists()

def test_restart_does_not_fabricate_completion(store):
    job,token=create(store);job['status']='rendering';store.save_job(job)
    before=job['version'];store.recover_jobs();recovered=store.load_job(job['id'],token)
    assert recovered['status']=='failed' and recovered['artifact'] is None
    assert recovered['version']>before and recovered['analysis']['warnings']

def test_immediate_deletion(store):
    job,token=create(store);store.delete_job(job['id'])
    assert not store.workspace(job['id']).exists()
    with pytest.raises(store.StoreError):store.load_job(job['id'],token)

def test_cleanup_keeps_valid_unexpired_job(store):
    job,token=create(store);ws=store.workspace(job['id'])
    old=time.time()-store.TTL_SECONDS-100;os.utime(ws,(old,old))
    store.cleanup_expired()
    assert store.load_job(job['id'],token)['id']==job['id']

def test_cleanup_never_deletes_unrelated_folder(store):
    folder=store.DATA_DIR/'unrelated';folder.mkdir()
    old=time.time()-store.TTL_SECONDS-100;os.utime(folder,(old,old))
    store.cleanup_expired();assert folder.exists()

def test_create_write_failure_removes_partial_directory(store,monkeypatch):
    def fail(*args,**kwargs):raise OSError('synthetic disk failure')
    monkeypatch.setattr(Path,'write_bytes',fail)
    with pytest.raises(OSError):create(store)
    assert list(store.DATA_DIR.iterdir())==[]

def test_delete_obeys_job_lock(store):
    job,_=create(store);started=threading.Event();done=threading.Event()
    def delete():
        started.set();store.delete_job(job['id']);done.set()
    with store.locked(job['id']):
        thread=threading.Thread(target=delete);thread.start()
        assert started.wait(1)
        assert not done.wait(.15), 'Deletion bypassed active job lock'
    thread.join(2);assert done.is_set()

def test_missing_old_artifact_can_be_invalidated(store):
    job,_=create(store);job['_artifactName']='copy-v1.docx';job['artifact']={'sha256':'missing'}
    store.invalidate(job)
    assert job['artifact'] is None and not job['acknowledged']


@pytest.mark.parametrize('expiry', [None, 'tomorrow', True, float('nan'), float('inf'), []])
def test_corrupt_expiry_does_not_keep_old_workspace(store, expiry):
    job,_=create(store)
    ws=store.workspace(job['id'])
    job['_expiresEpoch']=expiry
    store.save_job(job)
    old=time.time()-store.TTL_SECONDS-100
    os.utime(ws,(old,old))
    store.cleanup_expired()
    assert not ws.exists()
