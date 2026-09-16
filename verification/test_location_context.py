"""Grounding tests only: no network/model calls and no masking judgment."""
from copy import deepcopy
from pathlib import Path
from zipfile import ZipFile

from backend.engine import inspect_document, rule_candidates
from backend.location_context import attach_location_context

W = 'http://schemas.openxmlformats.org/wordprocessingml/2006/main'


def paragraph(text, heading=False):
    style = '<w:pPr><w:pStyle w:val="Heading1"/></w:pPr>' if heading else ''
    return f'<w:p>{style}<w:r><w:t>{text}</w:t></w:r></w:p>'


def table(label, value):
    return '<w:tbl><w:tr><w:tc>'+paragraph('항목')+'</w:tc><w:tc>'+paragraph('값')+'</w:tc></w:tr><w:tr><w:tc>'+paragraph(label)+'</w:tc><w:tc>'+paragraph(value)+'</w:tc></w:tr></w:tbl>'


def docx(tmp_path, body, extras=None):
    path = tmp_path/'location.docx'
    with ZipFile(path, 'w') as archive:
        archive.writestr('[Content_Types].xml', '<Types xmlns="http://schemas.openxmlformats.org/package/2006/content-types"/>')
        archive.writestr('word/document.xml', f'<w:document xmlns:w="{W}"><w:body>{body}</w:body></w:document>')
        for name, xml in (extras or {}).items():
            archive.writestr(name, xml)
    return path


def assert_literal(inspection, candidates):
    units = {u['id']: u for u in inspection['units']}
    for candidate in candidates:
        facts = candidate['locationContext']
        assert 1 <= len(facts['evidence']) <= 8
        for item in facts['evidence']:
            unit = units[item['unitId']]
            assert item['text'] == unit['text'][item['start']:item['end']]
            assert item['locator'] == unit['locator']
            assert len(item['text']) <= 300


def test_same_value_has_distinct_row_and_section_evidence(tmp_path):
    number = '02-123-4567'
    path = docx(tmp_path, paragraph('기관 연락처', True)+table('대표전화', number)+paragraph('개인 정산', True)+table('휴대전화', number))
    inspection = inspect_document(path)
    candidates = rule_candidates(inspection)
    candidates[0].update(method='keep', confirmed=True)
    before = deepcopy(candidates)
    attach_location_context(path, inspection, candidates)
    assert len(candidates) == 2
    for candidate, old in zip(candidates, before):
        assert {k: v for k, v in candidate.items() if k != 'locationContext'} == old
    first, second = [c['locationContext'] for c in candidates]
    assert first['section'] == '기관 연락처'
    assert second['section'] == '개인 정산'
    assert first['table'] == {'index': 1, 'row': 2, 'column': 2}
    assert second['table'] == {'index': 2, 'row': 2, 'column': 2}
    assert [e['text'] for e in first['evidence'] if e['relation']=='table_row_first_cell'] == ['대표전화']
    assert [e['text'] for e in second['evidence'] if e['relation']=='table_row_first_cell'] == ['휴대전화']
    assert all('대표전화' not in e['text'] for e in second['evidence'])
    assert_literal(inspection, candidates)


def test_header_footer_keep_separate_evidence_and_stale_candidates_omitted(tmp_path):
    number = '02-123-4567'
    path = docx(tmp_path, paragraph('본문 제목', True)+paragraph(number), {
        'word/header1.xml': f'<w:hdr xmlns:w="{W}">{paragraph("머리말 제목", True)}{paragraph(number)}</w:hdr>',
        'word/footer1.xml': f'<w:ftr xmlns:w="{W}">{paragraph(number)}</w:ftr>',
    })
    inspection = inspect_document(path)
    candidates = rule_candidates(inspection)
    attach_location_context(path, inspection, candidates)
    assert [c['locationContext']['region'] for c in candidates] == ['body', 'footer', 'header']
    assert 'section' not in candidates[1]['locationContext']
    assert candidates[2]['locationContext']['section'] == '머리말 제목'
    candidates[0]['start'] = 1
    candidates[1]['locationResolved'] = False
    attach_location_context(path, inspection, candidates)
    assert 'locationContext' not in candidates[0] and 'locationContext' not in candidates[1]
    assert_literal(inspection, candidates[2:])


def pdf_unit(uid, text, box, page=1):
    return {'id': uid, 'text': text, 'page': page, 'locator': {'page': page, 'bbox': box}}


def test_pdf_neighbours_are_per_occurrence_on_same_page_and_not_semantic_roles():
    number = '02-123-4567'
    units = [
        pdf_unit('public-label', '대표전화', [10, 20, 60, 30]),
        pdf_unit('public-value', number, [80, 20, 150, 30]),
        pdf_unit('private-label', '개인 휴대전화', [10, 200, 60, 210]),
        pdf_unit('private-value', number, [80, 200, 150, 210]),
        pdf_unit('different-page', '대표전화', [10, 200, 60, 210], 2),
    ]
    inspection = {'format': 'pdf', 'units': units}
    candidates = rule_candidates(inspection)
    attach_location_context(Path('not-opened.pdf'), inspection, candidates)
    first, second = [c['locationContext'] for c in candidates]
    assert first['label'] == second['label'] == '1쪽'
    assert [e['text'] for e in first['evidence'] if e['relation'] == 'same_row_left'] == ['대표전화']
    assert [e['text'] for e in second['evidence'] if e['relation'] == 'same_row_left'] == ['개인 휴대전화']
    assert all(e['unitId'] != 'different-page' for e in second['evidence'])
    assert 'role' not in first and 'isPublic' not in first
    assert_literal(inspection, candidates)


def test_long_evidence_is_bounded_and_keeps_original_character_offsets(tmp_path):
    value = 'person@example.test'
    path = docx(tmp_path, paragraph('가'*600 + ' ' + value + ' 나'*600))
    inspection = inspect_document(path)
    candidate = rule_candidates(inspection)[0]
    attach_location_context(path, inspection, [candidate])
    evidence = candidate['locationContext']['evidence'][0]
    assert evidence['start'] > 0 and value in evidence['text']
    assert_literal(inspection, [candidate])


def test_grid_span_does_not_invent_a_column_header(tmp_path):
    body = '<w:tbl><w:tr><w:tc><w:tcPr><w:gridSpan w:val="2"/></w:tcPr>'+paragraph('공용 연락처')+'</w:tc></w:tr><w:tr><w:tc>'+paragraph('개인 전화')+'</w:tc><w:tc>'+paragraph('02-123-4567')+'</w:tc></w:tr></w:tbl>'
    path = docx(tmp_path, body)
    inspection = inspect_document(path)
    candidates = rule_candidates(inspection)
    attach_location_context(path, inspection, candidates)
    evidence = candidates[0]['locationContext']['evidence']
    assert not any(e['relation'] == 'table_first_row' for e in evidence)
    assert all(e['text'] != '공용 연락처' for e in evidence)
    assert_literal(inspection, candidates)
