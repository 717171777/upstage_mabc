"""Binary runtime contract, coverage, safe defaults and manual overrides."""
import copy
import pytest
from backend import judge, analysis, hermes_worker


def candidate(cid='a', public=True):
    return {'id':cid,'type':'phone','value':'010-0000-0000','role_raw':'대표 전화' if public else '개인 휴대전화',
            'context_raw':'','location':{'evidence':[]}, 'method':'partial','mask':[[4,8]],'confirmed':False}


def compact(cid='a', method='keep', evidence='대표 전화'):
    return {'candidateId':cid,'recommendation':method,'evidence':evidence}


def test_runtime_receives_only_binary_options_no_masking_presets():
    payload={'candidates':[candidate(),candidate('b',False)],'context':{},'documentType':'other'}
    result, errors=judge._validate_payload(payload)
    assert not errors
    assert result['candidates'][0]['allowedRecommendations']==['full','keep']
    assert result['candidates'][1]['allowedRecommendations']==['full']
    assert all('allowedPresets' not in c and 'mask' not in c for c in result['candidates'])


def test_compact_output_normalized_before_grounding():
    normalized=hermes_worker._validate_suggestions([compact()])
    checked=judge._checked_batch(normalized,[candidate()],'')
    assert checked[0]['recommendation']=='keep'
    assert checked[0]['evidence']=='대표 전화'
    assert 'presetId' not in checked[0]
    full=hermes_worker._validate_suggestions([compact(method='full',evidence='')])
    assert judge._checked_batch(full,[candidate()],'')[0]['type']=='question'


@pytest.mark.parametrize('bad', [compact(method='partial'),compact(method='delete'),compact(evidence=''),
                                {**compact(),'presetId':'phone_suffix4'}, {**compact(),'reason':'extra'}])
def test_worker_rejects_methods_or_extra_output(bad):
    with pytest.raises(ValueError):hermes_worker._validate_suggestions([bad])


@pytest.mark.parametrize('kind',['missing','duplicate','other_id','fabricated_keep','private_keep'])
def test_binary_coverage_and_grounding_fail_closed(kind):
    c=candidate(public=kind!='private_keep')
    suggestion=compact()
    if kind=='other_id':suggestion['candidateId']='unknown'
    if kind=='fabricated_keep':suggestion['evidence']='허구'
    rows=hermes_worker._validate_suggestions([suggestion])
    if kind=='missing':rows=[]
    if kind=='duplicate':rows*=2
    with pytest.raises(judge.BatchFailure):judge._checked_batch(rows,[c],'')


def test_default_full_keep_and_manual_partial_preservation():
    public=candidate(); private=candidate('private',False); manual=candidate('manual')
    # Real stored candidates use locationContext, not the judge's location adapter.
    for c in (public,private,manual):c['locationContext']=c.pop('location')
    manual.update(confirmed=True,decisionSource='user')
    original=copy.deepcopy(manual)
    job={'candidates':[public,private,manual],'context':{},'suggestions':hermes_worker._validate_suggestions([compact()])}
    analysis.apply_binary_defaults(job)
    assert public['method']=='keep' and public['mask']==[] and public['confirmed'] is False
    assert private['method']=='full' and private['mask']==[]
    assert manual==original


def test_old_partial_recommendation_never_becomes_new_default():
    c=candidate(); job={'candidates':[c],'context':{},'suggestions':[{
        'candidateId':'a','type':'recommendation','recommendation':'partial','presetId':'phone_suffix4',
        'content':'old','reason':'old','evidence':'대표 전화'}]}
    analysis.apply_binary_defaults(job)
    assert c['method']=='full' and c['mask']==[]


def test_question_roundtrip_keeps_grounding_and_defaults_full():
    raw={**compact(method='full'),'question':'이 연락처를 공유본에 남길까요?'}
    rows=hermes_worker._validate_suggestions([raw])
    result=judge._checked_batch(rows,[candidate()],'')
    assert result[0]['question']==raw['question']
    assert result[0]['type']=='question' and result[0]['recommendation']=='full'
    c=candidate()
    analysis.apply_binary_defaults({'candidates':[c],'context':{},'suggestions':result})
    assert c['method']=='full' and not c['confirmed']


@pytest.mark.parametrize('change',[{'question':''},{'question':' '*3},{'question':'가'*121},
                                   {'recommendation':'keep'},{'evidence':''},{'question':None}])
def test_invalid_question_contract_rejected(change):
    raw={**compact(method='full'),'question':'이 연락처를 공유본에 남길까요?',**change}
    with pytest.raises(ValueError):hermes_worker._validate_suggestions([raw])


def test_question_with_fabricated_evidence_is_rejected():
    raw={**compact(method='full',evidence='문서에 없는 연락처 역할'),'question':'공유본에 남길까요?'}
    with pytest.raises(judge.BatchFailure):
        judge._checked_batch(hermes_worker._validate_suggestions([raw]),[candidate()],'')
