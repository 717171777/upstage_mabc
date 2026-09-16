import copy
from pathlib import Path
import pytest
from backend import store,analysis,judge,upstage
ROOT=Path(__file__).resolve().parents[1]
@pytest.fixture
def created(tmp_path,monkeypatch):
 monkeypatch.setattr(store,'DATA_DIR',tmp_path)
 source=ROOT/'fixtures/eval_v0/docs/docx-report_minutes-01.docx'
 job,token=store.create_job(source.read_bytes(),source.name,'docx',False)
 return job,token

def successful_result():
 return {'documentType':'report_minutes','stages':{x:{'status':'completed','model':'synthetic-test'} for x in ('parse','classify','extract')},'warnings':[],'extracted':{},'elements':[],'additional':{}}
def analyzing(created):
 job,token=created;job['status']='analyzing';job['aiEnabled']=True;store.save_job(job);return job,token

def test_off_makes_no_external_call(created,monkeypatch):
 monkeypatch.setattr(upstage,'analyze_file',lambda *a,**kw:pytest.fail('must not call'))
 monkeypatch.setattr(judge,'judge_exceptions',lambda *a,**kw:pytest.fail('must not call'))
 j,t=created;analysis.run_analysis(j['id'],t)
 assert store.load_job(j['id'],t)['status']=='review'
def test_progress_and_final_status_actual(created,monkeypatch):
 j,t=analyzing(created)
 def analyze(path,enabled,document_type,progress,sharing_context=None, stage_cache=None, checkpoint=None):
  progress('parse',{'status':'running'})
  assert store.load_job(j['id'],t)['analysis']['parse']['status']=='running'
  return successful_result()
 monkeypatch.setattr(upstage,'analyze_file',analyze)
 seen=[]
 def contextual(payload):
  seen.extend(payload['candidates'])
  assert all(c.get('location',{}).get('evidence') for c in payload['candidates'])
  return {'status':'completed','model':'solar-pro4-260806','suggestions':[],'warnings':[]}
 monkeypatch.setattr(judge,'judge_exceptions',contextual)
 analysis.run_analysis(j['id'],t);r=store.load_job(j['id'],t)
 assert r['status']=='review' and r['analysis']['parse']['status']=='completed'
 assert seen and r['analysis']['hermes']['status']=='completed'
def test_context_routes_actual_candidates_and_preserves_choices(created,monkeypatch):
 j,t=analyzing(created);j['context']['purpose']='합성검증';j['candidates'][0].update(method='keep',confirmed=True);store.save_job(j)
 before=copy.deepcopy(j['candidates']);seen=[]
 monkeypatch.setattr(upstage,'analyze_file',lambda *a,**kw:successful_result())
 def review(p):
  seen.extend(p['candidates']);assert 'units' not in p and 'elements' not in p
  return {'status':'completed','model':'solar-pro4-260806','suggestions':[{'candidateId':p['candidates'][0]['id'],'type':'question','content':'합성질문'}],'warnings':[]}
 monkeypatch.setattr(judge,'judge_exceptions',review)
 analysis.run_analysis(j['id'],t);r=store.load_job(j['id'],t)
 assert seen and r['suggestions'] and r['analysis']['hermes']['status']=='completed'
 assert r['candidates'][0]==before[0] and r['status']=='review'
 assert all(c['method']=='full' and not c['mask'] and c['confirmed'] is False for c in r['candidates'][1:])
def test_deleted_during_network_never_resurrects(created,monkeypatch):
 j,t=analyzing(created)
 def analyze(*a,**kw):store.delete_job(j['id']);return successful_result()
 monkeypatch.setattr(upstage,'analyze_file',analyze)
 analysis.run_analysis(j['id'],t);assert not store.workspace(j['id']).exists()
@pytest.mark.parametrize('stage',['upstage','judge'])
def test_failure_returns_to_review_with_visible_warning(created,monkeypatch,stage):
 j,t=analyzing(created);j['context']['purpose']='검증';store.save_job(j)
 def fail(*a,**kw):raise RuntimeError('private sentinel')
 monkeypatch.setattr(upstage,'analyze_file',fail if stage=='upstage' else lambda *a,**kw:successful_result())
 monkeypatch.setattr(judge,'judge_exceptions',fail)
 analysis.run_analysis(j['id'],t);r=store.load_job(j['id'],t)
 assert r['status']=='review' and r['analysis']['warnings']
 assert 'private sentinel' not in str(r)
 assert r['analysis']['hermes']['status']=='failed'
