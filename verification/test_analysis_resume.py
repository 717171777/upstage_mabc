"""Resume/retry contracts using synthetic files and mocked providers only."""
import copy
import json
import threading
from pathlib import Path
from types import SimpleNamespace

import pytest
from backend import analysis, judge, store, upstage, workflow
from backend.processing_requirements import assert_upstage_complete

SOURCE = Path(__file__).resolve().parents[1]/'fixtures/eval_v0/docs/docx-report_minutes-01.docx'


@pytest.fixture
def job(tmp_path, monkeypatch):
    monkeypatch.setattr(store, 'DATA_DIR', tmp_path/'jobs')
    store.DATA_DIR.mkdir()
    monkeypatch.setenv('GARIMI_DATA_DIR', str(store.DATA_DIR))
    monkeypatch.setenv('GARIMI_REQUIRE_UPSTAGE', '1')
    monkeypatch.setenv('GARIMI_ALLOW_USER_DOCUMENTS', '1')
    monkeypatch.setenv('UPSTAGE_API_KEY', 'never-sent-test-key')
    def denied(*a, **kw): pytest.fail('Unexpected external call')
    monkeypatch.setattr(upstage, '_post_with_retry', denied)
    monkeypatch.setattr(judge.subprocess, 'run', denied)
    j,t=store.create_job(SOURCE.read_bytes(), '합성.docx','docx',True,
                         context={'recipient':'내부동료','purpose':'합성 검증','keepInfo':None})
    j['status']='analyzing'
    j['analysis']={k:{'status':'pending'} for k in ('parse','classify','extract','hermes')}
    j['analysis']['warnings']=[]
    store.save_job(j)
    return j,t


def fake_stages(monkeypatch):
    calls=[]
    def parse(*args): calls.append('parse');return {'status':'completed','model':'parse','elements':[],'content':{}}
    def classify(*args): calls.append('classify');return {'status':'completed','model':'classify','class':'other'}
    def extract(*args,**kwargs): calls.append('extract');return {'status':'completed','model':'extract','extracted':{}}
    monkeypatch.setattr(upstage,'_run_parse',parse)
    monkeypatch.setattr(upstage,'_run_classify',classify)
    monkeypatch.setattr(upstage,'_run_extract',extract)
    return calls


def test_persisted_document_results_survive_failed_judge_and_restart(job,monkeypatch):
    j,t=job;calls=fake_stages(monkeypatch)
    monkeypatch.setattr(judge,'judge_exceptions',lambda payload: {'status':'failed','model':judge.MODEL,'suggestions':[],'warnings':['연결 실패']})
    analysis.run_analysis(j['id'],t)
    failed=store.load_job(j['id'],t)
    assert len(calls)==3 and set(failed['_analysisCheckpoint']['stages'])=={'parse','classify','extract'}
    assert failed['analysis']['hermes']['status']=='failed'
    assert all(not key.startswith('_') for key in store.public_job(failed))
    with pytest.raises(store.StoreError): assert_upstage_complete(failed)
    failed['status']='analyzing';failed['analysis']['hermes']['status']='running';store.save_job(failed)
    store.recover_jobs()
    recovered=store.load_job(j['id'],t)
    assert recovered['analysis']['hermes']['errorCode']=='INTERRUPTED'
    assert recovered['analysis']['parse']['status']=='completed'
    assert recovered['_analysisCheckpoint']==failed['_analysisCheckpoint']
    monkeypatch.setattr(judge,'judge_exceptions',lambda payload: {'status':'completed','model':judge.MODEL,'suggestions':[],'warnings':[]})
    recovered['status']='analyzing';store.save_job(recovered)
    analysis.run_analysis(j['id'],t)
    assert len(calls)==3
    assert_upstage_complete(store.load_job(j['id'],t))


def test_checkpoint_written_before_judge_process_crash(job,monkeypatch):
    j,t=job;calls=fake_stages(monkeypatch)
    def crash(payload): raise KeyboardInterrupt()
    monkeypatch.setattr(judge,'judge_exceptions',crash)
    with pytest.raises(KeyboardInterrupt): analysis.run_analysis(j['id'],t)
    before=store.load_job(j['id'],t)
    assert all(before['analysis'][s]['status']=='completed' for s in ('parse','classify','extract'))
    store.recover_jobs()
    current=store.load_job(j['id'],t);current['status']='analyzing';store.save_job(current)
    monkeypatch.setattr(judge,'judge_exceptions',lambda payload: {'status':'completed','model':judge.MODEL,'suggestions':[],'warnings':[]})
    analysis.run_analysis(j['id'],t)
    assert len(calls)==3


def test_context_change_invalidates_cached_stages(job,monkeypatch):
    j,t=job;calls=fake_stages(monkeypatch)
    monkeypatch.setattr(judge,'judge_exceptions',lambda p: {'status':'completed','model':judge.MODEL,'suggestions':[],'warnings':[]})
    analysis.run_analysis(j['id'],t)
    current=store.load_job(j['id'],t);current['context']['purpose']='다른 목적';current['status']='analyzing';store.save_job(current)
    analysis.run_analysis(j['id'],t)
    assert len(calls)==6


def test_partial_stage_cache_reruns_only_missing_stage(job,monkeypatch):
    j,t=job;calls=fake_stages(monkeypatch);cache={}
    monkeypatch.setattr(upstage,'_run_classify',lambda *a: {'status':'failed'})
    first=upstage.analyze_file(store.source_path(j),True,checkpoint=lambda k,v:cache.update({k:v}))
    assert first['stages']['classify']['status']=='failed'
    calls.clear()
    monkeypatch.setattr(upstage,'_run_classify',lambda *a: {'status':'completed','class':None})
    second=upstage.analyze_file(store.source_path(j),True,stage_cache=cache)
    assert second['stages']['classify']['status']=='completed' and calls==[]


def batch_payload(j):
    return {'jobWorkspace':str(store.workspace(j['id'])),'documentType':'other',
            'context':{'recipient':'내부동료','purpose':'합성','keepInfo':None},
            'candidates':[{'id':f'c{i}','type':'phone','value':'010-0000-0000','role_raw':'','context_raw':''} for i in range(41)]}


def suggestion(c):
    return {'candidateId':c['id'],'type':'question','content':'확인이 필요합니다','reason':'근거 부족','recommendation':'full','evidence':''}


def test_retry_only_failed_batch_after_real_checkpoint_reload(job,monkeypatch):
    j,t=job;payload=batch_payload(j);saved={};events=[];calls=[]
    def run(args,**kw):
        request=json.loads(kw['input']);ids=[c['id'] for c in request['candidates']];calls.append(ids)
        if ids[0]=='c20' and len(calls)<4:
            return SimpleNamespace(returncode=1,stdout=json.dumps({'diagnostics':{'errorCode':'AUTH_ERROR'}}))
        return SimpleNamespace(returncode=0,stdout=json.dumps({'status':'completed','model':judge.MODEL,'suggestions':[suggestion(c) for c in request['candidates']]}))
    monkeypatch.setattr(judge.subprocess,'run',run)
    def progress(record,key=None,suggestions=None):
        events.append(record)
        if key:saved[key]=suggestions
    payload.update(_progress=progress,_cachedBatches=saved)
    first=judge.judge_exceptions(payload)
    assert first['status']=='failed' and len(calls)==3 and len(saved)==2
    # Simulate persisted JSON being loaded by a replacement process.
    payload['_cachedBatches']=json.loads(json.dumps(saved))
    second=judge.judge_exceptions(payload)
    assert second['status']=='completed' and len(calls)==4 and calls[-1][0]=='c20'
    assert len(second['suggestions'])==41 and events[-1]['completedBatches']==3
    assert not list(store.workspace(j['id']).glob('hermes-*'))


@pytest.mark.parametrize('code,expected',[('UPSTREAM_TIMEOUT',2),('NETWORK_ERROR',2),('RATE_LIMIT',2),('PROVIDER_UNAVAILABLE',2),('AUTH_ERROR',1),('RESPONSE_INVALID',1)])
def test_bounded_transient_retries_only(job,monkeypatch,code,expected):
    j,t=job;payload=batch_payload(j);payload['candidates']=payload['candidates'][:1];calls=[]
    def run(*a,**kw):
        calls.append(1)
        return SimpleNamespace(returncode=1,stdout=json.dumps({'diagnostics':{'errorCode':code}}))
    monkeypatch.setattr(judge.subprocess,'run',run);monkeypatch.setattr(judge.time,'sleep',lambda s:None)
    result=judge.judge_exceptions(payload)
    assert len(calls)==expected and result['status']=='failed' and result['errorCode']==code


def test_batch_cache_does_not_reuse_different_context(job,monkeypatch):
    j,t=job;payload=batch_payload(j);payload['candidates']=payload['candidates'][:1];saved={};calls=[]
    def run(*a,**kw):
        calls.append(1);request=json.loads(kw['input'])
        return SimpleNamespace(returncode=0,stdout=json.dumps({'status':'completed','model':judge.MODEL,'suggestions':[suggestion(c) for c in request['candidates']]}))
    def save(r,k=None,s=None):
        if k:saved[k]=s
    monkeypatch.setattr(judge.subprocess,'run',run);payload.update(_progress=save,_cachedBatches=saved)
    judge.judge_exceptions(payload);payload['context']['purpose']='변경';judge.judge_exceptions(payload)
    assert len(calls)==2


def test_retry_operation_version_busy_auth_and_maintenance(job,monkeypatch):
    j,t=job;submitted=[]
    monkeypatch.setattr(workflow,'EXECUTOR',SimpleNamespace(submit=lambda *a:submitted.append(a)))
    monkeypatch.setattr(workflow,'CAPACITY',threading.BoundedSemaphore(4))
    args={'jobId':j['id'],'token':t,'payload':{'version':j['version']}}
    with pytest.raises(store.StoreError,match='이미 처리'): workflow.execute('retry-analysis',args)
    j['status']='review';store.save_job(j)
    with pytest.raises(store.StoreError): workflow.execute('retry-analysis',{**args,'token':'bad'})
    marker=store.DATA_DIR.parent/'.draining';marker.touch()
    with pytest.raises(store.StoreError,match='업데이트'): workflow.execute('retry-analysis',args)
    marker.unlink()
    result=workflow.execute('retry-analysis',args)
    assert result['status']=='analyzing' and len(submitted)==1
    with pytest.raises(store.StoreError): workflow.execute('retry-analysis',args)
    assert len(submitted)==1


def test_admission_is_visible_before_drain_check_and_removed_on_rejection(job,monkeypatch):
    j,t=job
    def in_flight(op,args):
        assert list(store.DATA_DIR.parent.glob('.admission-*'))
        return 'ok'
    monkeypatch.setattr(workflow,'_execute',in_flight)
    assert workflow.execute('create',{})=='ok'
    assert not list(store.DATA_DIR.parent.glob('.admission-*'))
    (store.DATA_DIR.parent/'.draining').touch()
    with pytest.raises(store.StoreError,match='업데이트'):workflow.execute('create',{})
    assert not list(store.DATA_DIR.parent.glob('.admission-*'))


def test_restart_cleans_orphaned_admissions(job):
    j,t=job;marker=store.DATA_DIR.parent/'.admission-old';marker.touch()
    store.recover_jobs()
    assert not marker.exists()
    assert store.load_job(j['id'],t)['analysis']['hermes']['errorCode']=='INTERRUPTED'
