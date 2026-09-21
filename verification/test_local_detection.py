"""Deterministic field detection only. No network or model requests."""
from collections import Counter
from pathlib import Path
from xml.sax.saxutils import escape
from zipfile import ZipFile
import socket
import unicodedata

import pytest

from backend.engine import TYPES, candidate_id, inspect_document
from backend.local_detection import detect_local

W = 'http://schemas.openxmlformats.org/wordprocessingml/2006/main'


@pytest.fixture(autouse=True)
def no_network(monkeypatch):
    def forbidden(*args, **kwargs):
        raise AssertionError('Local detection must not open a network connection')
    monkeypatch.setattr(socket, 'create_connection', forbidden)


def paragraph(text):
    return f'<w:p><w:r><w:t xml:space="preserve">{escape(text)}</w:t></w:r></w:p>'


def table(rows):
    return '<w:tbl>' + ''.join('<w:tr>'+''.join('<w:tc>'+paragraph(text)+'</w:tc>' for text in row)+'</w:tr>' for row in rows) + '</w:tbl>'


def docx(tmp_path, body):
    path = tmp_path/'local-labelled.docx'
    with ZipFile(path, 'w') as archive:
        archive.writestr('[Content_Types].xml', '<Types xmlns="http://schemas.openxmlformats.org/package/2006/content-types"/>')
        archive.writestr('word/document.xml', f'<w:document xmlns:w="{W}"><w:body>{body}</w:body></w:document>')
    return path


def checked(path, inspection):
    found = detect_local(path, inspection)
    units = {unit['id']: unit for unit in inspection['units']}
    assert len({candidate['id'] for candidate in found}) == len(found)
    for candidate in found:
        unit = units[candidate['unitId']]
        exact = candidate['value'] == unit['text'][candidate['start']:candidate['end']]
        assert exact, 'Every candidate must match its unchanged literal source'
        assert candidate['id'] == candidate_id(candidate['type'], candidate['unitId'], candidate['start'], candidate['end'])
        assert candidate['locationResolved'] and not candidate['confirmed']
        assert candidate['method'] == 'full' and candidate['mask'] == []
        evidence = candidate.get('labelEvidence')
        if evidence:
            assert candidate['source'] == 'local_label'
            source = units[evidence['unitId']]
            exact = evidence['text'] == source['text'][evidence['start']:evidence['end']]
            assert exact, 'Label evidence must be a literal source range'
    for index, candidate in enumerate(found):
        assert not any(other['unitId'] == candidate['unitId'] and other['start'] < candidate['end'] and candidate['start'] < other['end'] for other in found[index+1:])
    return found


def test_all_twelve_explicit_types_and_non_personal_values_stay_separate(tmp_path):
    rows = [
        ('항목', '값'), ('이름', '김가람'), ('휴대전화', '010-2345-6789'),
        ('이메일', 'garam@example.test'), ('주소', '서울특별시 중구 가상로 123, 가상아파트 101동 202호'),
        ('생년월일', '1990-02-03'), ('주민등록번호', '900203-1234567'),
        ('외국인등록번호', '900203-5234567'), ('여권번호', 'M12345678'),
        ('운전면허번호', '11-22-123456-78'), ('계좌번호', '123-456789-12345'),
        ('사용 카드번호', '1234 5678 1234 5678'), ('사번', 'EMP-2026-001'),
        ('문서번호', 'DOC-2026-009'), ('주문번호', 'ORD-123456789'),
        ('출장일', '2020-01-02'), ('금액', '320,000원'), ('은행명', '가상은행'),
    ]
    path = docx(tmp_path, table(rows))
    inspection = inspect_document(path)
    candidates = checked(path, inspection)
    assert Counter(candidate['type'] for candidate in candidates) == Counter(TYPES)
    excluded = {value for label, value in rows[-5:]}
    assert not excluded.intersection(candidate['value'] for candidate in candidates)


def test_unlabelled_shapes_and_prose_mentions_do_not_become_context_detections(tmp_path):
    body = ''.join(paragraph(text) for text in [
        '김가람', '서울특별시 중구 가상로 123', '1990-02-03', 'M12345678',
        '11-22-123456-78', '123-456789-12345', '1234 5678 1234 5678', 'EMP-2026-001',
        '담당자 검토 메모', '생년월일은 중요한 개인정보입니다.', '출장일: 1990-02-03',
        '문서번호: DOC-2026-009', '주문번호: ORD-123456789', '금액: 320000',
    ])
    path = docx(tmp_path, body)
    assert checked(path, inspect_document(path)) == []


def test_inline_explicit_fields_calendar_validation_and_empty_markers(tmp_path):
    path = docx(tmp_path, paragraph('이름: 김가람 | 생년월일: 1990년 2월 3일 | 사번: EMP-001')+
                paragraph('생년월일: 2023-02-29')+paragraph('여권번호: 해당 없음')+
                table([('이름', '해당 없음'), ('이름', '미발급'), ('생 년 월 일', '1990.2.3')]))
    candidates = checked(path, inspect_document(path))
    assert Counter(candidate['type'] for candidate in candidates) == {'name': 1, 'dob': 2, 'management_id': 1}


def test_exact_established_names_repeat_with_particles_but_not_inside_words(tmp_path):
    path = docx(tmp_path, table([('이름', '김가람')])+
                paragraph('김가람은 담당자입니다. 김가람의 정산입니다. (김가람)')+
                paragraph('박김가람 김가람동 김가람회사 김가람@example.test'))
    candidates = checked(path, inspect_document(path))
    assert len([candidate for candidate in candidates if candidate['type'] == 'name']) == 4
    assert len([candidate for candidate in candidates if candidate['type'] == 'email']) == 1


def test_table_column_headers_ground_each_independent_name(tmp_path):
    path = docx(tmp_path, table([('성명', '사번'), ('김가람', 'EMP-001'), ('이하늘', 'EMP-002')]))
    candidates = checked(path, inspect_document(path))
    assert Counter(candidate['type'] for candidate in candidates) == {'name': 2, 'management_id': 2}
    assert all(candidate['labelEvidence']['relation'] == 'table_first_row' for candidate in candidates)


@pytest.mark.parametrize('label', ['참가자', '내담자', '상담자'])
def test_additional_name_labels(tmp_path, label):
    path = docx(tmp_path, paragraph(f'{label}: 김가람'))
    assert [(c['type'], c['value']) for c in checked(path, inspect_document(path))] == [('name', '김가람')]


def test_multiple_inline_fields_without_separators(tmp_path):
    path = docx(tmp_path, paragraph('참가자: 김가람 상담자： 이하늘 생 년 월 일: 1990-02-03 연락처: 010-2345-6789'))
    found = checked(path, inspect_document(path))
    assert [(c['type'], c['value']) for c in found] == [
        ('name', '김가람'), ('name', '이하늘'), ('dob', '1990-02-03'), ('phone', '010-2345-6789')]


def test_contact_headers_are_not_names(tmp_path):
    path = docx(tmp_path, table([('성명', '연락처', '이메일'),
                                 ('김가람', '010-2345-6789', 'garam@example.test')]))
    found = checked(path, inspect_document(path))
    assert [c['value'] for c in found if c['type'] == 'name'] == ['김가람']


def test_unknown_delimited_field_terminates_name(tmp_path):
    path = docx(tmp_path, paragraph('이름: 김가람 | 부서: 연구팀'))
    assert [c['value'] for c in checked(path, inspect_document(path))] == ['김가람']


def test_specific_label_disambiguates_phone_shape_but_preserves_resident_id(tmp_path):
    path = docx(tmp_path, table([('계좌번호', '010-2345-6789'), ('계좌번호', '900203-1234567')]))
    candidates = checked(path, inspect_document(path))
    assert [candidate['type'] for candidate in candidates] == ['account', 'resident_id']
    assert candidates[0]['alternativeTypes'] == ['phone']


def pdf_unit(uid, text, box, page=1):
    return {'id': uid, 'text': text, 'page': page, 'locator': {'bbox': box, 'page': page}}


def test_pdf_centre_aligned_address_label_links_exact_lines_and_not_other_page():
    units = [
        pdf_unit('address-label', '주소', [10, 29, 40, 39]),
        pdf_unit('line-one', '서울특별시 중구 가상로 123,', [80, 20, 240, 30]),
        pdf_unit('line-two', '가상아파트 101동 202호', [80, 38, 210, 48]),
        pdf_unit('another-column', '서울특별시 강남구 가상로 45,', [300, 20, 480, 30]),
        pdf_unit('another-detail', '가상빌딩 502호', [300, 38, 420, 48]),
        pdf_unit('different-page', '서울특별시 중구 가상로 123,', [80, 20, 240, 30], 2),
        pdf_unit('distant-line', '가상아파트 305호', [80, 80, 210, 90]),
    ]
    candidates = checked(Path('not-opened.pdf'), {'format': 'pdf', 'units': units})
    assert len(candidates) == 4 and all(candidate['type'] == 'address' for candidate in candidates)
    assert len({candidate['localGroupId'] for candidate in candidates}) == 2
    assert all(candidate['page'] == 1 for candidate in candidates)


def test_pdf_label_requires_same_row_and_exact_value_validation():
    units = [pdf_unit('name-label', '이름', [10, 20, 40, 30]),
             pdf_unit('name', '김가람', [80, 20, 120, 30]),
             pdf_unit('not-same-row', '이하늘', [80, 100, 120, 110]),
             pdf_unit('travel-label', '출장일', [10, 200, 40, 210]),
             pdf_unit('travel-date', '2020-01-02', [80, 200, 160, 210])]
    candidates = checked(Path('not-opened.pdf'), {'format': 'pdf', 'units': units})
    assert [(candidate['type'], candidate['unitId']) for candidate in candidates] == [('name', 'name')]


@pytest.mark.parametrize('extension', ['docx', 'pdf'])
def test_supplied_travel_registration_files_locally(extension):
    directory = Path('/Users/a1717771/Downloads')
    wanted = '가리미_테스트_출장등록및정산.' + extension
    path = next((path for path in directory.iterdir() if unicodedata.normalize('NFC', path.name) == wanted), None) if directory.is_dir() else None
    if path is None:
        pytest.skip('User-provided local document is not on this machine')
    inspection = inspect_document(path)
    candidates = checked(path, inspection)
    counts = Counter(candidate['type'] for candidate in candidates)
    assert set(counts) == set(TYPES), 'All 12 explicit labelled categories should be found in the supplied document'
    assert counts['name'] == 14
    assert counts['address'] == (4 if extension == 'pdf' else 2)
    assert counts['dob'] == counts['management_id'] == counts['passport'] == counts['account'] == counts['card'] == 2
