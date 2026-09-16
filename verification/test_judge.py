import json,copy,subprocess
from pathlib import Path
from types import SimpleNamespace
import pytest
from backend import judge

@pytest.fixture
def payload(tmp_path,monkeypatch):
 data=tmp_path/'jobs';data.mkdir();ws=data/('a'*32);ws.mkdir()
 monkeypatch.setenv('GARIMI_DATA_DIR',str(data));monkeypatch.setenv('UPSTAGE_API_KEY','synthetic-test-key')
 return {'jobWorkspace':str(ws),'context':{'recipient':None,'purpose':'','keepInfo':None},'documentType':'other','candidates':[{'id':'c_'+'a'*24,'type':'phone','value':'010-0000-0000','role_raw':'','context_raw':''}]}
def response(suggestions,**kwargs):
 return SimpleNamespace(returncode=0,stdout=json.dumps({'status':'completed','model':judge.MODEL,'suggestions':suggestions,**kwargs}))
def suggestion(cid,**kwargs):
 return {'candidateId':cid,'type':'question','content':'이 값을 가릴까요?','reason':'근거가 부족합니다','recommendation':'full','evidence':'',**kwargs}
def test_batching_real_id_contract_and_cleanup(payload,monkeypatch):
 base=payload['candidates'][0];payload['candidates']=[{**base,'id':f'c_{i:024d}'} for i in range(41)]
 seen=[]
 def run(args,**kwargs):
  d=json.loads(kwargs['input']);seen.append(d)
  assert len(d['candidates'])<=20 and 'jobWorkspace' not in d
  assert Path(kwargs['env']['HERMES_HOME']).is_dir()
  return response([suggestion(c['id']) for c in d['candidates']])
 monkeypatch.setattr(judge.subprocess,'run',run)
 r=judge.judge_exceptions(payload)
 assert r['status']=='completed' and len(r['suggestions'])==41
 assert [len(x['candidates']) for x in seen]==[20,20,1]
 assert not list(Path(payload['jobWorkspace']).iterdir())
@pytest.mark.parametrize('kind',['unknown','fake_evidence','unfounded_keep','wrong_model','nonzero','excess'])
def test_invalid_worker_output_never_becomes_success(payload,monkeypatch,kind):
 cid=payload['candidates'][0]['id'];s=suggestion(cid)
 if kind=='unknown':s['candidateId']='invented'
 if kind=='fake_evidence':s['evidence']='허구원문'
 if kind=='unfounded_keep':s.update(recommendation='keep',evidence='010-0000-0000')
 r=response([s]*3 if kind=='excess' else [s],**({'model':'other-model'} if kind=='wrong_model' else {}))
 if kind=='nonzero':r.returncode=1
 monkeypatch.setattr(judge.subprocess,'run',lambda *a,**kw:r)
 result=judge.judge_exceptions(payload)
 assert result['status']=='failed' and result['suggestions']==[]
def test_explicit_user_keep_evidence(payload,monkeypatch):
 c=payload['candidates'][0];payload['context']['keepInfo']=c['value']+'는 남겨주세요'
 monkeypatch.setattr(judge.subprocess,'run',lambda *a,**kw:response([suggestion(c['id'],recommendation='keep',evidence=payload['context']['keepInfo'])]))
 assert judge.judge_exceptions(payload)['status']=='completed'
def test_empty_does_not_start_process(monkeypatch):
 monkeypatch.delenv('UPSTAGE_API_KEY',raising=False)
 monkeypatch.setattr(judge.subprocess,'run',lambda *a,**kw:pytest.fail('must not call'))
 assert judge.judge_exceptions({'candidates':[]})['status']=='not_needed'

@pytest.mark.parametrize('ctype,role,allowed',[
 ('phone','기관 대표 전화: 010-0000-0000',True),
 ('phone','개인 휴대전화: 010-0000-0000',False),
 ('phone','대표 전화: 02-000-0000; 개인 휴대전화: 010-0000-0000',False),
 ('name','대표자: 010-0000-0000',False),
 ('address','기관 공용: 010-0000-0000',False),
])
def test_model_receives_only_server_valid_keep_grounds(payload,ctype,role,allowed):
 c=payload['candidates'][0];c.update(type=ctype,role_raw=role)
 c['allowedRecommendations']=['keep'];c['allowedKeepEvidence']=['client invented evidence']
 clean,errors=judge._validate_payload(payload)
 assert not errors
 hint=clean['candidates'][0]
 assert ('keep' in hint['allowedRecommendations']) is allowed
 assert bool(hint['allowedKeepEvidence']) is allowed
 assert 'client invented evidence' not in hint['allowedKeepEvidence']
 for evidence in hint['allowedKeepEvidence']:
  _,errs=judge._validate_suggestion(suggestion(c['id'],recommendation='keep',evidence=evidence),{c['id']},[hint],'')
  assert not errs

def test_explicit_keep_hint_uses_exact_value(payload):
 c=payload['candidates'][0];payload['context']['keepInfo']=c['value']+'는 남겨주세요'
 clean,errors=judge._validate_payload(payload)
 assert not errors and clean['candidates'][0]['allowedKeepEvidence']==[c['value']]
