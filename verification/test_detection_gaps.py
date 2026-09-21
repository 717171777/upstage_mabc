"""Unseen synthetic layouts, non-PII controls, and real saved-copy regressions."""
import json
import re
from collections import Counter
from pathlib import Path

import pymupdf as fitz
import pytest

from backend.engine import inspect_document, render_document
from backend.local_detection import detect_local
from verification.test_local_detection import checked, docx, paragraph

ROOT = Path(__file__).resolve().parents[1] / 'fixtures/eval_v0'
DOCUMENTS = [json.loads(line) for line in (ROOT / 'documents.jsonl').read_text().splitlines()]
TRUTH = [json.loads(line) for line in (ROOT / 'ground_truth.jsonl').read_text().splitlines()]


@pytest.mark.parametrize('text,expected', [
    ('문의: 남궁하린 (010-5678-9012)', [('name', '남궁하린'), ('phone', '010-5678-9012')]),
    ('문의: 고객센터 010-5678-9012', [('phone', '010-5678-9012')]),
    ('성명: 류다온 부서 연구팀', [('name', '류다온')]),
    ('내담자 황보아린, 거주지 서울특별시 중구 새봄로 17 202호', [('name', '황보아린'), ('address', '서울특별시 중구 새봄로 17 202호')]),
    ('(대표 독고하린)', [('name', '독고하린')]),
    ('가족(배우자) 한다온에게도 안내 요청. 연락처 010-5678-9012', [('name', '한다온'), ('phone', '010-5678-9012')]),
    ('진료 기록 (접수번호 PT-2030-001)', [('management_id', 'PT-2030-001')]),
    ('주문서 (접수번호 ORD-2030-001)', []),
    ('문의: 대표 전화 02-123-4567', [('phone', '02-123-4567')]),
    ('담당자 검토 메모', []), ('참가자 모집', []), ('상담자 확인', []),
    ('성명 정보', []), ('내담자 이해를 돕기 위한 자료', []),
    ('주식회사 새봄 (대표 전화)', []), ('예금주 국민은행', []),
    ('행사 장소: 서울특별시 중구 새봄로 17', []),
    ('회의일: 2030-03-20 문서번호: DOC-2030-7777', []),
])
def test_roles_and_non_personal_controls(tmp_path, text, expected):
    path = docx(tmp_path, paragraph(text))
    found = checked(path, inspect_document(path))
    assert [(c['type'], c['value']) for c in found] == expected


def test_known_name_propagation_keeps_particles_and_substrings(tmp_path):
    path = docx(tmp_path, paragraph('내담자 류다온,') +
                paragraph('류다온은 상담을 마쳤습니다. 류다온의 자료입니다.') +
                paragraph('가류다온 류다온회사 류다온@example.test'))
    found = checked(path, inspect_document(path))
    assert [c['value'] for c in found if c['type'] == 'name'] == ['류다온'] * 3


def pdf_table(tmp_path, order=('name', 'dob', 'management_id'), ending='gap'):
    path = tmp_path / 'independent-table.pdf'
    labels = {'name': '성명', 'dob': '생년월일', 'management_id': '사번'}
    values = {'name': '류다온', 'dob': '1993-11-02', 'management_id': 'STAFF-2030-12'}
    with fitz.open() as pdf:
        page = pdf.new_page()
        for col, kind in enumerate(order):
            page.insert_text((50 + 150 * col, 70), labels[kind], fontname='korea', fontsize=10)
            page.insert_text((50 + 150 * col, 95), values[kind], fontname='korea', fontsize=10)
        if ending == 'heading':
            page.insert_text((50, 120), '아래는 다른 문서입니다', fontname='korea', fontsize=10)
        if ending == 'page':
            page = pdf.new_page()
        y = 120 if ending == 'page' else 150 if ending == 'heading' else 400
        for col, kind in enumerate(order):
            value = {'name': '공지사항', 'dob': '2030-10-15', 'management_id': 'DOC-2030-77'}[kind]
            page.insert_text((50 + 150 * col, y), value, fontname='korea', fontsize=10)
        pdf.save(path)
    return path


@pytest.mark.parametrize('order', [('name', 'dob', 'management_id'), ('management_id', 'name', 'dob')])
@pytest.mark.parametrize('ending', ['gap', 'heading', 'page'])
def test_pdf_columns_reordered_and_do_not_leak_to_other_sections(tmp_path, order, ending):
    path = pdf_table(tmp_path, order, ending)
    found = checked(path, inspect_document(path))
    assert {(c['type'], c['value']) for c in found} == {
        ('name', '류다온'), ('dob', '1993-11-02'), ('management_id', 'STAFF-2030-12')}
    for c in found:
        assert c['page'] == 1 and c['labelEvidence']['relation'] == 'table_column_header'


def test_pdf_invalid_cell_does_not_establish_a_table(tmp_path):
    path = tmp_path / 'invalid.pdf'
    with fitz.open() as pdf:
        page = pdf.new_page()
        for x, label, value in [(50, '성명', '미확인'), (200, '생년월일', '2030-02-31'), (350, '사번', '문서없음')]:
            page.insert_text((x, 70), label, fontname='korea', fontsize=10)
            page.insert_text((x, 95), value, fontname='korea', fontsize=10)
        pdf.save(path)
    assert checked(path, inspect_document(path)) == []


@pytest.mark.parametrize('document', DOCUMENTS, ids=lambda d: d['doc_id'])
def test_benchmark_coverage_and_actual_saved_file(document, tmp_path):
    source = ROOT / document['file']
    if document['doc_id'] == 'docx-contract_agreement-01':
        with pytest.raises(ValueError, match='추적 변경'):
            inspect_document(source)
        return
    inspection = inspect_document(source)
    found = checked(source, inspection)
    expected = Counter((row['type_id'], re.sub(r'^[가-힣]+\s+', '', row['raw_value'])
                        if row['type_id'] == 'account' else row['raw_value'])
                       for row in TRUTH if row['doc_id'] == document['doc_id'])
    detected = Counter((c['type'], c['value']) for c in found)
    assert not expected - detected, 'A labelled benchmark occurrence was missed'
    extra = detected - expected
    assert all(kind in ('phone', 'email') and value in ('051-000-0000', 'club@example.org')
               for kind, value in extra), 'New non-PII false positive'
    original = source.read_bytes()
    for candidate in found:
        candidate.update(method='full', mask=[], confirmed=True)
    destination = tmp_path / ('redacted' + source.suffix)
    report = render_document(source, destination, inspection, found,
                             {m['id']: 'delete' for m in inspection['metadata']})
    assert report['passed'] and source.read_bytes() == original
    saved = inspect_document(destination)
    text = '\n'.join(u['text'] for u in saved['units'])
    assert all(value not in text for _, value in expected), 'Original PII survived in saved copy'
    assert all(not m['value'] for m in saved['metadata'])
