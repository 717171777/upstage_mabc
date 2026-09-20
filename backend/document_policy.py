"""Document purpose hints for document APIs and the sole runtime Claude judge.

These are service rules, never instructions obtained from an uploaded document.
No automatic keep decision is made by this module.
"""
PROFILES = {
 'report_minutes': '활동·성과·결정을 전달하는 문서. 본문의 작성자·인용 인물과 부록의 참여자 명단을 구분한다. 일시·장소·건수·사업 금액 자체는 개인정보로 단정하지 않는다.',
 'contract_agreement': '권리·의무·동의를 정하는 문서. 당사자/서명자와 기관 담당자를 구분한다. 계약서라는 이유만으로 개인 이름·서명 정보·식별번호를 유지하지 않는다.',
 'transaction_settlement': '청구·지급·출장·비용 정산 문서. 개인 정산 행의 예금주·계좌·카드·식별번호와 기관 공용 문의 정보를 구분한다. 일반 금액·영수증 총액·출장 일정은 개인정보 종류로 바꾸지 않는다.',
 'personnel_roster': '등록·선발·인사·참여 명단. 각 개인 행의 이름·연락처·관리번호와 표 밖 기관 연락처를 구분한다. 표 행 순번·참가 인원은 개인 식별 코드가 아니다.',
 'case_record': '개인의 상황·발언·처리 경과. 인물 연결과 재식별 가능성이 높으므로 이름·연락처·생년월일·상세 주소는 보수적으로 검토한다.',
 'other': '업무 목적이 불분명하거나 여러 독립 문서가 섞임. 원문에 확인된 구간/표 항목별로 판단하며 분류명을 공개 허가로 사용하지 않는다.',
}

TYPE_FOCUS = {
 'name':'성명·신청인·출장자·서명자·예금주·보호자·담당자 항목. 기관명과 사람 이름을 구분.',
 'phone':'개인 휴대전화/직통과 기관 대표·공용 연락처를 모두 찾고 해당 항목의 역할 원문을 함께 기록.',
 'email':'개인/업무 계정과 공용 메일을 모두 찾는다. 도메인만으로 공용 여부를 판단하지 않는다.',
 'address':'개인 주소·상세 호수와 기관/행사/출장 장소를 역할 근거로 구분.',
 'dob':'생년월일·출생일 항목을 확인. 작성일·출장기간·정산일·영수일은 생년월일로 추출하지 않는다.',
 'resident_id':'주민등록번호 역할과 실제 표기, 오류/가상/부분 가림에도 누락하지 않는다.',
 'foreign_id':'외국인등록번호 항목과 실제 표기를 주민등록번호와 분리.',
 'passport':'여권번호/여권 식별 항목. 일반 문서번호와 구분.',
 'driver_license':'운전면허번호 항목. 차량번호·날짜와 구분.',
 'account':'정산/환급/지급 계좌와 예금주 항목. 일반 금액·날짜를 계좌로 바꾸지 않는다.',
 'card':'개인 결제카드 번호 항목. 영수증 번호/금액과 구분.',
 'management_id':'사번·학번·고객/참여자/접수 코드 항목. 표 순번·건수·일반 문서번호와 구분.',
}


def profile(document_type):
    return PROFILES.get(document_type, PROFILES['other'])


def candidate_for_judge(candidate):
    facts = candidate.get('locationContext') or {}
    evidence = facts.get('evidence') or []
    return {
        'id': candidate['id'], 'type': candidate['type'], 'value': candidate['value'],
        'role_raw': str(candidate.get('role_raw') or '')[:300],
        'context_raw': str(candidate.get('context_raw') or '')[:300],
        'location': {
            'label': str(facts.get('label') or '')[:180],
            'region': str(facts.get('region') or '')[:40],
            'section': str(facts.get('section') or '')[:200],
            'page': candidate.get('page'),
            'evidence': [{'relation': str(e.get('relation') or '')[:40],
                          'text': str(e.get('text') or '')[:300]}
                         for e in evidence[:8]],
        },
    }
