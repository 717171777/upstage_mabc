"""Transparent local rules, not a local LLM. Never performs external requests."""
import re
from .document_policy import candidate_for_judge
from .judge import _validate_suggestion


def classify_local(inspection):
    # Only document opening text; appendix labels do not override a clear title.
    units=[u['text'].strip() for u in inspection.get('units',[]) if u.get('text','').strip()
           and not any(x in u.get('locator',{}).get('part','') for x in ('header','footer'))]
    rules=[('transaction_settlement',r'정산|지출결의|급여명세|영수증|출장비'),
           ('contract_agreement',r'계약서|협약서|동의서'),
           ('personnel_roster',r'참가자\s*명단|참여자\s*명부|인사기록|이력서|등록신청'),
           ('case_record',r'상담일지|민원기록|인터뷰'),
           ('report_minutes',r'보고서|회의록|기획서')]
    for title in units[:4]:
        for kind,pattern in rules:
            if re.search(pattern,title):return kind
    return 'other'


def recommendations(job):
    """Full mask by default, literal public-contact exception."""
    out=[]
    for c in job.get('candidates',[]):
        if not c.get('locationResolved'):continue
        payload=candidate_for_judge(c)
        facts=payload.get('location',{}).get('evidence',[])
        direct=[f['text'] for f in facts if f['relation'] in
                ('self','same_cell','table_row_first_cell','table_first_row','same_row_left')]
        evidence=(direct[0] if direct else c.get('context_raw') or c.get('role_raw') or '')
        if not evidence:continue
        method='full'
        reason='개인정보로 확인한 값은 전체 가림을 기본으로 합니다.'
        if job.get('documentType')=='transaction_settlement':
            reason='등록·정산에 필요한 금액과 일정을 보존하고, 개인을 식별하는 이 항목은 가립니다.'
        suggestion={'candidateId':c['id'],'type':'recommendation','content':'위치와 항목명에 따른 로컬 추천',
                    'reason':reason,'recommendation':method,'evidence':evidence,'source':'local_rules'}
        # Only a validator-proven direct public contact can be kept automatically.
        for text in direct:
            proposal={**suggestion,'recommendation':'keep','evidence':text,
                      'reason':'이 위치에 기관의 공용 연락처 역할이 직접 표시되어 있습니다.'}
            valid,errors=_validate_suggestion(proposal,{c['id']},[payload],'')
            if not errors:
                suggestion=valid;break
        valid,errors=_validate_suggestion(suggestion,{c['id']},[payload],job.get('context',{}).get('keepInfo') or '')
        if not errors:
            valid['source']='local_rules';out.append(valid)
    return out


def refresh(job):
    if job.get('aiEnabled'):return
    if not job.get('documentType'):
        job['documentType']=classify_local(job['_inspection'])
        job['documentTypeSource']='local_rules'
    job.setdefault('documentTypeSource','local_rules')
    job['suggestions']=recommendations(job)
    job['localCoverage']='12종 항목명·위치 규칙과 형식 탐지. 표현이 불명확한 정보는 직접 확인이 필요합니다.'
