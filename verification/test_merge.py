import copy, importlib.util
from pathlib import Path
import pytest
from backend.engine import candidate_id
from backend import analysis_merge as merge

def job(text='담당 김가상, 김가상',fmt='docx'):
 return {'format':fmt,'_inspection':{'units':[{'id':'u1','text':text,'page':1 if fmt=='pdf' else None}]},'candidates':[],'analysis':{'warnings':[]}}
def result(value='김가상',role='담당',context='담당 김가상'):
 return {'extracted':{'name':[{'raw_value':value,'role_raw':role,'context_raw':context}]},'additional':{},'elements':[{'id':1,'page':1,'category':'paragraph','text':'담당 김가상'}]}
def test_deduplicates_within_and_across_runs():
 j=job();r=result();r['extracted']['name']*=2
 merge.merge_extractions(j,r)
 assert len(j['candidates'])==2 and len({c['id'] for c in j['candidates']})==2
 c=j['candidates'][0];c.update(method='keep',confirmed=True)
 merge.merge_extractions(j,r);assert len(j['candidates'])==2
 assert c['method']=='keep' and c['confirmed'] and isinstance(c['source'],str)
 assert 'name' not in c['alternativeTypes']
def test_verified_context_and_parse_evidence():
 j=job();merge.merge_extractions(j,result())
 c=j['candidates'][0];assert c['role_raw']=='담당' and c['context_raw']=='담당 김가상'
 assert c['structureEvidence'][0]['category']=='paragraph'
 j=job();merge.merge_extractions(j,result(role='기관 대표전화',context='허구 문장'))
 assert not j['candidates'][0].get('role_raw')
def test_unresolved_dedup_and_no_fake_locator():
 j=job();r=result(value='없는합성값')
 merge.merge_extractions(j,r);merge.merge_extractions(j,r)
 assert len(j['candidates'])==1
 c=j['candidates'][0];assert c['unitId'] is None and c['locationResolved'] is False and not c.get('role_raw')
def test_incoming_overlap_is_unresolved_and_preserves_user():
 j=job('합성값ABC');j['candidates']=[{'id':'c_old','type':'name','unitId':'u1','start':0,'end':2,'value':'합성','page':None,'locationResolved':True,'method':'keep','mask':[],'confirmed':True,'source':'manual','alternativeTypes':[]}]
 merge.merge_extractions(j,result('합성값'))
 assert len(j['candidates'])==2 and j['candidates'][0]['confirmed']
 assert not j['candidates'][1]['locationResolved']
def test_incoming_duplicates_different_types_same_position():
 j=job('가상123');r=result('가상123');r['extracted']['management_id']=copy.deepcopy(r['extracted']['name'])
 merge.merge_extractions(j,r)
 assert len(j['candidates'])==1
 assert j['candidates'][0]['alternativeTypes']==['management_id']
def test_pdf_structure_requires_same_page():
 j=job(fmt='pdf');r=result();r['elements'][0]['page']=2
 merge.merge_extractions(j,r);assert not j['candidates'][0].get('structureEvidence')


@pytest.mark.parametrize('text,value', [
 ('사번 EMP-2023-9562', 'M'),
 ('사번 EMP-M12345678-01', 'M12345678'),
 ('번호 XM12345678', 'M12345678'),
 ('여권번호 M123', 'M123'),
])
def test_passport_fragments_remain_unresolved(text, value):
 j=job(text)
 r={'extracted': {'passport': [{'raw_value': value}]}}
 merge.merge_extractions(j,r)
 merge.merge_extractions(j,r)
 assert len(j['candidates']) == 1
 c=j['candidates'][0]
 assert c['type'] == 'passport' and c['method'] == 'full'
 assert c['locationResolved'] is False and c['unitId'] is None


@pytest.mark.parametrize('value', ['M12345678', 'M123A4567'])
def test_passport_only_matches_complete_identifier(value):
 text=f'사번 EMP-{value}-01 여권번호: {value}'
 j=job(text)
 merge.merge_extractions(j, {'extracted': {'passport': [{'raw_value': value}]}})
 assert len(j['candidates']) == 1
 c=j['candidates'][0]
 assert c['locationResolved'] is True and c['start'] == text.rindex(value)


def email_job():
 j=job('연락 test@example.com · test@example.com')
 for start in (3,22):
  j['candidates'].append({'id':str(start),'type':'email','value':'test@example.com','unitId':'u1','start':start,'end':start+16,'locationResolved':True,'method':'partial','mask':[[0,4]],'confirmed':True,'source':'rule','alternativeTypes':[]})
 return j

def email_result(value='test@example.co m',kind='email'):
 return {'extracted':{kind:[{'raw_value':value,'role_raw':'연락','context_raw':'없는 문장'}]}}

def test_extraction_email_wrap_links_verified_original_and_preserves_choices():
 j=email_job();merge.merge_extractions(j,email_result())
 assert len(j['candidates'])==2
 for c in j['candidates']:
  assert c['value']=='test@example.com' and c['method']=='partial' and c['confirmed']
  assert c['mask']==[[0,4]] and c['extractionVariants']==['test@example.co m']
  assert 'upstage' in c['evidenceSources'] and not c.get('context_raw')

def test_email_wrap_does_not_invent_location_or_normalize_other_types():
 for fault in ('missing','wrong_type','stale_range','different_value'):
  j=email_job();r=email_result()
  if fault=='missing':j['candidates']=[]
  if fault=='wrong_type':r=email_result(kind='name')
  if fault=='stale_range':
   for c in j['candidates']:c['start']+=1
  if fault=='different_value':r=email_result('not-test@example.co m')
  merge.merge_extractions(j,r)
  assert not j['candidates'][-1]['locationResolved']
  assert j['candidates'][-1]['unitId'] is None

def test_email_reanalysis_repairs_only_unconfirmed_extraction_duplicates():
 j=email_job();bad={'id':'old','type':'email','value':'test@example.co m','locationResolved':False,'source':'upstage','confirmed':False,'proposedLocation':[]}
 j['candidates'] += [bad,dict(bad,id='manual',source='manual'),dict(bad,id='confirmed',confirmed=True)]
 merge.merge_extractions(j,email_result())
 assert [c['id'] for c in j['candidates']]==['3','22','manual','confirmed']

def test_literal_spaced_email_is_not_rewritten():
 j=email_job();j['_inspection']['units'][0]['text']+=' · test@example.co m'
 merge.merge_extractions(j,email_result())
 assert j['candidates'][-1]['value']=='test@example.co m'
 assert j['candidates'][-1]['locationResolved']
