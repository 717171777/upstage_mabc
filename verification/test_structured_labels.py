"""Shape-constrained labels in prose, without guessing unlabelled values."""
import pytest
from backend.engine import inspect_document
from verification.test_local_detection import docx, paragraph, checked


@pytest.mark.parametrize('text,kind,value', [
    ('입금 계좌: 카카오뱅크 644-552539-29-629', 'account', '644-552539-29-629'),
    ('을의 계좌(하나 593-687780-06-394, 예금주 김가람)', 'account', '593-687780-06-394'),
    ('법인카드 정산: 5223-6943-2079-2446 (승인번호 30291837 은 카드번호 아님)', 'card', '5223-6943-2079-2446'),
    ('외국인 참가자 김가람 / 여권 M93330292', 'passport', 'M93330292'),
    ('운전 가능자: 김가람 (면허 27-48-383620-99)', 'driver_license', '27-48-383620-99'),
    ('사번 CUS-2025-6202   성명 김가람', 'management_id', 'CUS-2025-6202'),
    ('을: 김가람 (생년월일 2001-07-07)', 'dob', '2001-07-07'),
    ('을의 주소: 부산광역시 금정구 달구벌대로 2310 1203호', 'address', '부산광역시 금정구 달구벌대로 2310 1203호'),
])
def test_explicit_structured_labels(tmp_path, text, kind, value):
    path = docx(tmp_path, paragraph(text))
    found = checked(path, inspect_document(path))
    expected = {(kind, value)}
    # These explicitly labelled names used to be missed; they are now also
    # expected, with their own exact non-overlapping source ranges.
    if '예금주 김가람' in text or '성명 김가람' in text or text.startswith('을: 김가람'):
        expected.add(('name', '김가람'))
    assert {(c['type'], c['value']) for c in found} == expected


@pytest.mark.parametrize('text', [
    '여권 M', '여권 EMP-M12345678-01', '여권 M123456789012345',
    '여권 ABCDEF', '생년월일 2023-02-29', '사번 검토중',
    '계좌 확인을 요청합니다.', '계좌: 123-45', '카드번호: 해당 없음',
    'M12345678', '김가람', '생년월일은 중요한 개인정보입니다.',
])
def test_fragments_and_prose_are_not_identifiers(tmp_path, text):
    path = docx(tmp_path, paragraph(text))
    assert checked(path, inspect_document(path)) == []
