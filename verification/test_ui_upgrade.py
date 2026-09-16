"""Independent UI-upgrade backend checks. AI responses stubbed: no live API claims."""
import copy
import hashlib
import threading
from pathlib import Path
import pytest
from backend import store, plans, workflow_files, engine, upstage

ROOT=Path(__file__).resolve().parents[1]
SOURCE=ROOT/'fixtures/eval_v0/docs/docx-report_minutes-01.docx'

@pytest.fixture(autouse=True)
def isolated(tmp_path,monkeypatch):
    monkeypatch.setattr(store,'DATA_DIR',tmp_path)
    monkeypatch.setenv('UPSTAGE_API_KEY','independent-test-only')


def seed():
    job,token=store.create_job(SOURCE.read_bytes(),'합성 문서.docx','docx',True)
    job['analysis']={s:{'status':'completed'} for s in ('parse','classify','extract','hermes')}
    job['analysis']['warnings']=[]
    return job,token


def ready():
    job,token=seed()
    plans.apply_plan(job,{'version':job['version'],'candidates':[{'id':c['id'],'method':'full','mask':[],'confirmed':True} for c in job['candidates']],'metadataReviewed':True})
    store.invalidate(job);store.save_job(job)
    workflow_files.render_copy(job,{'version':job['version']})
    return job,token


def rec(c,method,preset=None):
    s={'candidateId':c['id'],'type':'recommendation','content':'합성 검증 제안','reason':'공유 목적에 따른 합성 검증','recommendation':method,'evidence':c['value']}
    if preset:s['presetId']=preset
    return s


def test_partial_policy_exact_exclusive_offsets():
    from backend.masking_policy import recommended_presets as p
    assert p({'type':'phone','value':'010-2450-7300'})['phone_suffix4']==[[0,9]]
    assert p({'type':'phone','value':'02 123 4567'})['phone_suffix2']==[[0,9]]
    assert p({'type':'email','value':'person@example.test'})['email_local_part']==[[0,6]]
    assert p({'type':'address','value':'서울시  강남구\t테스트로  12'})['address_city']==[[8,17]]
    assert p({'type':'dob','value':'2000.01.02'})['dob_year_only']==[[4,10]]
    assert p({'type':'account','value':'123-456-7890'})['id_suffix4']==[[0,8]]
    assert p({'type':'account','value':'123-456-7890'})['id_suffix2']==[[0,10]]
    assert not p({'type':'email','value':'e\u0301@example.test'})


def test_layout_preserves_unit_ids_order_and_tables():
    from backend.document_layout import build_layout
    info=engine.inspect_document(SOURCE)
    blocks=build_layout(SOURCE,info['units'])
    def flatten(bs):
        for b in bs:
            if b['kind']=='paragraph':yield b['unitId']
            else:
                for row in b['rows']:
                    for cell in row:yield from flatten(cell['blocks'])
    ids=list(flatten(blocks))
    assert len(ids)==len(set(ids))==len(info['units'])
    assert set(ids)=={u['id'] for u in info['units']}
    assert any(b['kind']=='table' for b in blocks)
    job,_=ready()
    original=workflow_files.preview(job,'original');saved=workflow_files.preview(job,'copy')
    assert original['blocks'] and saved['blocks']
    assert any(u['text']!=v['text'] for u,v in zip(original['units'],saved['units']))


def test_automatic_partial_keep_and_manual_preservation(monkeypatch):
    from backend import auto_export
    job,_=seed()
    phones=[c for c in job['candidates'] if c['type']=='phone']
    a,b=phones[:2]
    manual=next(c for c in job['candidates'] if c not in (a,b))
    manual.update(method='delete',mask=[],confirmed=True)
    job['context']['keepInfo']=a['value']+' '+b['value']
    job['suggestions']=[rec(a,'partial','phone_suffix4'),rec(b,'keep')]
    result=auto_export.run(job,{'version':job['version'],'mode':'ai_automatic'})
    byid={c['id']:c for c in result['candidates']}
    assert byid[a['id']]['method']=='full' and byid[a['id']]['mask']==[]
    assert byid[b['id']]['method']=='keep'
    assert byid[manual['id']]['method']=='delete'
    assert result['acknowledged'] and result['acknowledgmentMode']=='ai_automatic'
    assert result['autoExport']['manualPreserved']==1
    assert all(c['passed'] for c in result['artifact']['checks'])
    path=workflow_files.artifact_path(job,True)
    assert hashlib.sha256(path.read_bytes()).hexdigest()==result['artifact']['sha256']
    copytext='\n'.join(u['text'] for u in engine.inspect_document(path)['units'])
    assert a['value'] not in copytext
    assert not any(c['value'] in copytext for c in result['candidates'] if c['method']=='delete')


@pytest.mark.parametrize('fault',['unresolved','incomplete','failed_stage','off','uninspected','wrong_version','no_consent'])
def test_automatic_blocked_before_file_changes(fault):
    from backend import auto_export
    job,_=seed();payload={'version':job['version'],'mode':'ai_automatic'}
    if fault=='unresolved':job['candidates'][0]['locationResolved']=False
    if fault=='incomplete':job['analysis']['incomplete']=True
    if fault=='failed_stage':job['analysis']['extract']['status']='failed'
    if fault=='off':job['aiEnabled']=False
    if fault=='uninspected':job['uninspected']=['이미지']
    if fault=='wrong_version':payload['version']+=1
    if fault=='no_consent':payload['mode']='manual'
    old=copy.deepcopy(job)
    with pytest.raises(store.StoreError):auto_export.run(job,payload)
    assert job==old
    assert not list(store.workspace(job['id']).glob('copy-*'))


def test_invalid_or_cross_candidate_recommendations_never_keep():
    from backend import auto_export
    job,_=seed();first=job['candidates'][0]
    job['suggestions']=[rec(first,'keep'),{'type':'recommendation','candidateId':'unknown'},None]
    result=auto_export.run(job,{'version':job['version'],'mode':'ai_automatic'})
    assert all(c['method']=='full' for c in result['candidates'])


class CapturedExecutor:
    def __init__(self): self.task=None
    def submit(self,fn,*args):self.task=(fn,args)
    def run(self): self.task[0](*self.task[1])


def test_copy_ai_review_uses_saved_bytes_and_preserves_ack(monkeypatch):
    from backend import copy_review, copy_review_worker
    job,token=ready(); expected=workflow_files.artifact_path(job).read_bytes()
    executor=CapturedExecutor();capacity=threading.BoundedSemaphore(1);seen=[]
    def extract(path,key,dt):
        assert path.read_bytes()==expected and path.read_bytes()!=SOURCE.read_bytes()
        seen.append('extract')
        return {'status':'completed','model':'information-extract-260904','extracted':{},'additional':{}}
    monkeypatch.setattr(upstage,'_run_extract',extract)
    monkeypatch.setattr(copy_review_worker,'judge_exceptions',lambda payload: {'status':'completed','model':'solar-pro4-260806','suggestions':[],'warnings':[]})
    pub=copy_review.start(job,{'version':job['version'],'sha256':job['artifact']['sha256']},token,executor,capacity)
    assert pub['id']==job['id'] and pub['aiReview']['status']=='running'
    assert not capacity.acquire(False)
    fresh=store.load_job(job['id'],token);fresh['acknowledged']=True;store.save_job(fresh)
    executor.run(); done=store.load_job(job['id'],token)
    assert seen==['extract'] and done['aiReview']['status']=='completed'
    assert done['acknowledged'] is True
    assert done['candidates']==job['candidates']
    assert done['artifact']==job['artifact']
    assert capacity.acquire(False);capacity.release()
    assert not list(store.workspace(job['id']).glob('copy-review-*'))


def test_copy_review_stale_result_discarded(monkeypatch):
    from backend import copy_review, copy_review_worker
    job,token=ready();executor=CapturedExecutor();capacity=threading.BoundedSemaphore(1)
    monkeypatch.setattr(upstage,'_run_extract',lambda *a:{'status':'completed','extracted':{},'additional':{}})
    monkeypatch.setattr(copy_review_worker,'judge_exceptions',lambda p:{'status':'not_needed','suggestions':[]})
    copy_review.start(job,{'version':job['version'],'sha256':job['artifact']['sha256']},token,executor,capacity)
    fresh=store.load_job(job['id'],token);store.invalidate(fresh);store.save_job(fresh)
    executor.run();done=store.load_job(job['id'],token)
    assert not done.get('aiReview') and done['artifact'] is None
    assert capacity.acquire(False);capacity.release()


@pytest.mark.parametrize('fault',['off','unapproved','wrong_hash','tampered','stale'])
def test_copy_review_rejects_before_external_call(monkeypatch,fault):
    from backend import copy_review, copy_review_worker
    job,token=ready();executor=CapturedExecutor();capacity=threading.BoundedSemaphore(1)
    payload={'version':job['version'],'sha256':job['artifact']['sha256']}
    if fault=='off':job['aiEnabled']=False
    if fault=='unapproved':monkeypatch.setattr(upstage,'is_synthetic',lambda p:False)
    if fault=='wrong_hash':payload['sha256']='x'*64
    if fault=='stale':payload['version']-=1
    if fault=='tampered':workflow_files.artifact_path(job).write_bytes(b'tampered')
    with pytest.raises(store.StoreError):copy_review.start(job,payload,token,executor,capacity)
    assert executor.task is None
    assert capacity.acquire(False);capacity.release()


def test_obsolete_partial_is_not_reinterpreted_and_confirmed_choice_is_preserved():
    from backend import auto_export
    job,_=seed()
    phones=[c for c in job['candidates'] if c['type']=='phone']
    old_recommendation, manual=phones[:2]
    manual.update(method='partial',mask=[[4,8]],confirmed=True)
    job['suggestions']=[rec(old_recommendation,'partial','phone_middle_digits')]
    result=auto_export.run(job,{'version':job['version'],'mode':'ai_automatic'})
    byid={c['id']:c for c in result['candidates']}
    assert byid[old_recommendation['id']]['method']=='full'
    assert byid[manual['id']]['method']=='partial'
    assert byid[manual['id']]['mask']==[[4,8]]
    assert all(c['passed'] for c in result['artifact']['checks'])


def test_unanswered_question_masks_full_and_answer_stays_manual():
    from backend import auto_export
    job,_=seed()
    a,b=[c for c in job['candidates'] if c['type']=='phone'][:2]
    job['suggestions']=[{**rec(c,'full'),'type':'question','question':'이 연락처를 공유본에 남길까요?'} for c in (a,b)]
    plans.apply_plan(job,{'version':job['version'],'candidates':[{'id':b['id'],'method':'keep','mask':[],'confirmed':True}]})
    result=auto_export.run(job,{'version':job['version'],'mode':'ai_automatic'})
    current={c['id']:c for c in result['candidates']}
    assert current[a['id']]['method']=='full'
    assert current[b['id']]['method']=='keep' and current[b['id']]['decisionSource']=='user'
    assert all(c['passed'] for c in result['artifact']['checks'])
    text='\n'.join(u['text'] for u in engine.inspect_document(workflow_files.artifact_path(job,True))['units'])
    assert a['value'] not in text and b['value'] in text
