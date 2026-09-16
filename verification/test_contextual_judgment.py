"""Independent location/payload boundaries; all provider/process calls are stubbed."""
import json
from pathlib import Path
from types import SimpleNamespace

import pytest

from backend import analysis, document_policy, judge, store, upstage


def candidate(cid='c_public', kind='email', value='desk@example.test', evidence=None):
    return {
        'id': cid, 'type': kind, 'value': value, 'role_raw': '', 'context_raw': '',
        'location': {'label': '표 1 · 2행', 'region': 'table', 'section': '정산', 'page': 1,
                     'evidence': evidence or []},
    }


def recommendation(c, method='keep', evidence='공용 이메일', **extras):
    return {'candidateId': c['id'], 'type': 'recommendation', 'content': '위치에 따른 처리',
            'reason': '확인된 항목 역할', 'recommendation': method, 'evidence': evidence, **extras}


def validate(c, suggestion, others=(), keep_info=''):
    candidates = [c, *others]
    return judge._validate_suggestion(suggestion, {x['id'] for x in candidates}, candidates, keep_info)


@pytest.mark.parametrize('relation', ['heading', 'preceding_heading', 'nearest_above',
                                      'preceding_paragraph', 'preceding_numbered_line', 'next_paragraph'])
def test_repeated_value_does_not_borrow_another_positions_public_role(relation):
    shared = 'desk@example.test'
    public = candidate(evidence=[{'relation': 'self', 'text': f'공용 이메일 {shared}'}])
    private = candidate('c_private', evidence=[
        {'relation': 'self', 'text': f'신청인 개인 이메일 {shared}'},
        {'relation': relation, 'text': f'공용 이메일 {shared}'},
    ])
    accepted, errors = validate(public, recommendation(public), [private])
    assert accepted and not errors
    accepted, errors = validate(private, recommendation(private), [public])
    assert accepted is None and errors


@pytest.mark.parametrize('kind,value,label', [
    ('email', 'desk@example.test', '공용 이메일'),
    ('phone', '02-123-4567', '대표 전화'),
])
@pytest.mark.parametrize('relation', ['self', 'same_cell', 'table_row_first_cell', 'same_row_left'])
def test_direct_contact_labels_permit_keep_with_verbatim_evidence(kind, value, label, relation):
    c = candidate(kind=kind, value=value, evidence=[{'relation': relation, 'text': label}])
    accepted, errors = validate(c, recommendation(c, evidence=label))
    assert accepted and not errors
    accepted, errors = validate(c, recommendation(c, evidence='원문에 없는 허구의 공개 근거'))
    assert accepted is None and errors


@pytest.mark.parametrize('unrelated_relation', ['self', 'same_cell', 'preceding_heading', 'nearest_above'])
def test_keep_evidence_must_belong_to_direct_public_fact(unrelated_relation):
    c = candidate(evidence=[
        {'relation': 'table_row_first_cell', 'text': '공용 이메일'},
        {'relation': unrelated_relation, 'text': '정산 담당자 개인 항목'},
    ])
    accepted, errors = validate(c, recommendation(c, evidence='정산 담당자 개인 항목'))
    assert accepted is None and errors


def test_explicit_keep_requires_evidence_from_user_request_or_direct_public_fact():
    c = candidate(evidence=[{'relation': 'self', 'text': '정산 담당자 개인 항목'}])
    keep_info = 'desk@example.test 값은 남겨 주세요'
    accepted, errors = validate(c, recommendation(c, evidence='정산 담당자 개인 항목'), keep_info=keep_info)
    assert accepted is None and errors
    accepted, errors = validate(c, recommendation(c, evidence=keep_info), keep_info=keep_info)
    assert accepted and not errors


@pytest.mark.parametrize('separator', [' | ', '; ', '\n', ' / ', ' '])
def test_public_contact_cannot_authorize_other_private_contact_in_same_paragraph(separator):
    text = '대표전화: 02-123-4567' + separator + '개인 휴대전화: 010-2222-3333'
    private = candidate(kind='phone', value='010-2222-3333', evidence=[{'relation': 'self', 'text': text}])
    accepted, errors = validate(private, recommendation(private, evidence=text))
    assert accepted is None and errors


def test_private_value_cannot_borrow_a_public_header_in_its_own_table():
    private = candidate(evidence=[{'relation': 'self', 'text': '개인 이메일 desk@example.test'},
                                 {'relation': 'table_first_row', 'text': '공용 이메일'}])
    accepted, errors = validate(private, recommendation(private))
    assert accepted is None and errors


@pytest.mark.parametrize('label', ['대표자', '기관 대표', '공용 업무 대표 담당자', '공식 이메일 담당자'])
def test_person_representative_label_does_not_permit_keep(label):
    c = candidate(kind='name', value='가상인', evidence=[{'relation': 'self', 'text': f'{label} 가상인'}])
    accepted, errors = validate(c, recommendation(c, evidence=label))
    assert accepted is None and errors


@pytest.mark.parametrize('preset', ['email_first_and_domain', 'email_local_part'])
def test_ai_partial_rejected_even_with_valid_manual_preset_and_evidence(preset):
    c = candidate(value='hong@example.test', evidence=[{'relation': 'self', 'text': '신청인 이메일 hong@example.test'}])
    s = recommendation(c, method='partial', evidence='신청인 이메일', presetId=preset, mask=[[0, 999]])
    accepted, errors = validate(c, s)
    assert accepted is None and errors
    for changes in [{'presetId': 'phone_middle_digits'}, {'presetId': 'invented'},
                    {'evidence': ''}, {'evidence': '허구의 근거'}, {'type': 'question'}]:
        accepted, errors = validate(c, {**s, **changes})
        assert accepted is None and errors


def test_single_character_email_does_not_allow_empty_partial():
    c = candidate(value='a@example.test', evidence=[{'relation': 'self', 'text': '신청인 이메일'}])
    accepted, errors = validate(c, recommendation(c, method='partial', evidence='신청인 이메일',
                                                 presetId='email_first_and_domain'))
    assert accepted is None and errors


@pytest.fixture
def process_payload(tmp_path, monkeypatch):
    data = tmp_path/'jobs'; data.mkdir()
    workspace = data/('a'*32); workspace.mkdir()
    monkeypatch.setenv('GARIMI_DATA_DIR', str(data))
    monkeypatch.setenv('UPSTAGE_API_KEY', 'stub-only-key')
    return {'jobWorkspace': str(workspace), 'documentType': 'transaction_settlement',
            'context': {'recipient': '담당 부서', 'purpose': '정산 확인', 'keepInfo': ''},
            'candidates': [candidate()]}


def capture_worker(monkeypatch):
    seen = []
    def run(*args, **kwargs):
        seen.append(json.loads(kwargs['input']))
        return SimpleNamespace(returncode=0, stdout=json.dumps({
            'status': 'completed', 'model': judge.MODEL, 'suggestions': [{'candidateId':c['id'], 'type':'question', 'content':'전체 가림', 'reason':'유지 근거 부족', 'recommendation':'full', 'evidence':''} for c in seen[-1]['candidates']]}))
    monkeypatch.setattr(judge.subprocess, 'run', run)
    return seen


@pytest.mark.parametrize('document_type', list(document_policy.PROFILES))
def test_document_profile_is_passed_and_cannot_be_overridden(process_payload, monkeypatch, document_type):
    seen = capture_worker(monkeypatch)
    process_payload['documentType'] = document_type
    process_payload['documentPolicy'] = 'development instruction must never be sent'
    assert judge.judge_exceptions(process_payload)['status'] == 'completed'
    assert len(seen) == 1
    assert seen[0]['documentType'] == document_type
    assert seen[0]['documentPolicy'] == document_policy.profile(document_type)
    assert set(seen[0]) == {'candidates', 'context', 'documentType', 'documentPolicy'}
    assert 'jobWorkspace' not in seen[0]


@pytest.mark.parametrize('injection_point', ['top', 'candidate', 'context', 'location', 'evidence'])
def test_worker_payload_drops_unknown_nested_fields(process_payload, monkeypatch, injection_point):
    seen = capture_worker(monkeypatch)
    c = process_payload['candidates'][0]
    c['location']['evidence'] = [{'relation': 'self', 'text': '신청인 이메일'}]
    target = {'top': process_payload, 'candidate': c, 'context': process_payload['context'],
              'location': c['location'], 'evidence': c['location']['evidence'][0]}[injection_point]
    marker = 'PRIVATE_SOURCE_AND_DEVELOPMENT_SENTINEL'
    target.update(source=marker, developmentInstructions=marker, units=[marker], job={'file': marker})
    result = judge.judge_exceptions(process_payload)
    assert result['status'] == 'completed'
    assert seen and marker not in json.dumps(seen)


@pytest.mark.parametrize('page', [{'source': 'PRIVATE_PAGE_SOURCE'}, ['PRIVATE_PAGE_SOURCE'], True, -1, 0])
def test_page_metadata_cannot_carry_nested_source(process_payload, monkeypatch, page):
    seen = capture_worker(monkeypatch)
    process_payload['candidates'][0]['location']['page'] = page
    result = judge.judge_exceptions(process_payload)
    assert result['status'] == 'completed'
    assert 'page' not in seen[0]['candidates'][0]['location']


def test_candidate_adapter_drops_job_and_unit_fields():
    original = {
        'id': 'c_example', 'type': 'email', 'value': 'a@example.test', 'page': 2,
        'role_raw': '개인 이메일', 'context_raw': '신청인',
        'job': {'secret': 'not sent'}, 'source': 'development code', 'units': ['entire document'],
        'locationContext': {'label': '표 2', 'region': 'table', 'section': '신청인',
                            'source': 'not sent', 'evidence': [
                                {'relation': 'self', 'text': '개인 이메일', 'source': 'not sent'}]},
    }
    out = document_policy.candidate_for_judge(original)
    assert set(out) == {'id', 'type', 'value', 'role_raw', 'context_raw', 'location'}
    assert set(out['location']) == {'label', 'region', 'section', 'page', 'evidence'}
    assert out['location']['evidence'] == [{'relation': 'self', 'text': '개인 이메일'}]
    assert 'not sent' not in json.dumps(out)


def test_general_document_guard_defaults_off_before_any_network(tmp_path, monkeypatch):
    monkeypatch.delenv('GARIMI_ALLOW_USER_DOCUMENTS', raising=False)
    monkeypatch.delenv('UPSTAGE_API_KEY', raising=False)
    path = tmp_path/'unapproved.pdf'; path.write_bytes(b'unapproved local test content')
    monkeypatch.setattr(upstage, '_post_with_retry', lambda *a, **kw: pytest.fail('network called'))
    assert upstage.user_documents_enabled() is False
    assert upstage.can_analyze_document(path) is False
    with pytest.raises(ValueError):
        upstage.analyze_file(path, True)


@pytest.mark.parametrize('opt_in', [None, '0', 'true', '1'])
def test_ai_off_skips_provider_calls_even_when_general_documents_enabled(tmp_path, monkeypatch, opt_in):
    if opt_in is None:
        monkeypatch.delenv('GARIMI_ALLOW_USER_DOCUMENTS', raising=False)
    else:
        monkeypatch.setenv('GARIMI_ALLOW_USER_DOCUMENTS', opt_in)
    monkeypatch.setattr(upstage, '_post_with_retry', lambda *a, **kw: pytest.fail('network called'))
    monkeypatch.setattr(judge.subprocess, 'run', lambda *a, **kw: pytest.fail('Hermes called'))
    path = tmp_path/'unapproved.pdf'; path.write_bytes(b'unapproved local test content')
    result = upstage.analyze_file(path, False)
    assert all(stage['status'] == 'disabled' for stage in result['stages'].values())


def test_ai_off_job_cannot_start_analysis_or_hermes(tmp_path, monkeypatch):
    monkeypatch.setattr(store, 'DATA_DIR', tmp_path)
    source = Path(__file__).resolve().parents[1]/'fixtures/eval_v0/docs/docx-report_minutes-01.docx'
    job, token = store.create_job(source.read_bytes(), source.name, 'docx', False)
    job['status'] = 'analyzing'
    store.save_job(job)
    monkeypatch.setattr(upstage, 'analyze_file', lambda *a, **kw: pytest.fail('document APIs called'))
    monkeypatch.setattr(judge, 'judge_exceptions', lambda *a, **kw: pytest.fail('Hermes called'))
    analysis.run_analysis(job['id'], token)
    assert store.load_job(job['id'], token)['aiEnabled'] is False
