"""Local policy and saved-copy export contracts; every external route is blocked."""
import copy
import hashlib
import io
from pathlib import Path
from xml.sax.saxutils import escape
from zipfile import ZipFile

import pytest

from backend import auto_export, engine, judge, local_policy, store, upstage, workflow, workflow_files

ROOT = Path(__file__).resolve().parents[1]


@pytest.fixture(autouse=True)
def offline_only(tmp_path, monkeypatch):
    monkeypatch.setattr(store, 'DATA_DIR', tmp_path)
    monkeypatch.delenv('UPSTAGE_API_KEY', raising=False)
    monkeypatch.delenv('GARIMI_ALLOW_USER_DOCUMENTS', raising=False)
    def deny(*args, **kwargs):
        pytest.fail('Local policy/export attempted an external provider or Hermes call')
    monkeypatch.setattr(upstage, '_post_with_retry', deny)
    monkeypatch.setattr(upstage, 'analyze_file', deny)
    monkeypatch.setattr(judge, 'judge_exceptions', deny)
    monkeypatch.setattr(judge.subprocess, 'run', deny)


def candidate(cid='c_personal', kind='email', value='hong@example.test', label='개인 이메일', relation='self'):
    return {'id': cid, 'type': kind, 'value': value, 'locationResolved': True,
            'method': 'full', 'mask': [], 'confirmed': False,
            'locationContext': {'label': '본문 · 표 1 · 2행 2열', 'region': 'body',
                                'evidence': [{'relation': relation, 'text': label}]}}


def local_job(candidates, document_type='transaction_settlement', recipient='내부 담당자'):
    return {'aiEnabled': False, 'documentType': document_type, 'candidates': candidates,
            'context': {'recipient': recipient, 'purpose': '정산 확인', 'keepInfo': ''},
            '_inspection': {'units': [{'text': '출장 등록 및 정산', 'locator': {'part': 'word/document.xml'}}]}}


@pytest.mark.parametrize('title,expected', [
    ('출장 등록 및 정산', 'transaction_settlement'), ('개인정보 동의서', 'contract_agreement'),
    ('참가자 명단', 'personnel_roster'), ('상담일지', 'case_record'),
    ('회의록', 'report_minutes'), ('작성일 2026-09-16', 'other'),
    ('출장기간 2026-09-16 ~ 2026-09-18', 'other'), ('총액 120,000원', 'other'),
])
def test_classify_title_without_converting_dates_or_amounts(title, expected):
    assert local_policy.classify_local({'units': [{'text': title}]}) == expected


def test_clear_title_wins_over_later_opening_topic_and_header():
    inspection = {'units': [
        {'text': '정산 부서', 'locator': {'part': 'word/header1.xml'}},
        {'text': '회의록', 'locator': {'part': 'word/document.xml'}},
        {'text': '작성일: 2026-09-16', 'locator': {'part': 'word/document.xml'}},
        {'text': '안건: 출장비 정산 절차 개선', 'locator': {'part': 'word/document.xml'}},
    ]}
    assert local_policy.classify_local(inspection) == 'report_minutes'


def test_refresh_is_local_labeled_and_preserves_candidates():
    c = candidate(); c.update(method='keep', confirmed=True)
    job = local_job([c], document_type=None)
    before = copy.deepcopy(job['candidates'])
    local_policy.refresh(job)
    assert job['documentType'] == 'transaction_settlement'
    assert job['documentTypeSource'] == 'local_rules'
    assert job['candidates'] == before
    assert job['suggestions'] and all(s['source'] == 'local_rules' for s in job['suggestions'])
    assert '규칙' in job['localCoverage']


def test_refresh_does_not_replace_external_analysis_state():
    job = local_job([candidate()]); job['aiEnabled'] = True
    job['suggestions'] = [{'source': 'external-existing'}]
    before = copy.deepcopy(job)
    local_policy.refresh(job)
    assert job == before


def test_email_default_is_full_for_all_audiences():
    c = candidate()
    suggestion = local_policy.recommendations(local_job([c]))[0]
    assert suggestion['recommendation'] == 'full'
    assert 'presetId' not in suggestion
    assert suggestion['evidence'] == '개인 이메일'
    assert local_policy.recommendations(local_job([c], recipient='불특정다수'))[0]['recommendation'] == 'full'


@pytest.mark.parametrize('kind,value,label,expected', [
    ('phone', '02-123-4567', '대표 전화', 'keep'),
    ('email', 'desk@example.test', '공용 이메일', 'keep'),
    ('phone', '010-1234-5678', '개인 휴대전화', 'full'),
    ('name', '가상인', '대표자', 'full'),
    ('account', '123-456-7890', '정산 계좌', 'full'),
    ('resident_id', '000000-0000000', '주민등록번호', 'full'),
])
def test_direct_role_controls_keep_or_conservative_full(kind, value, label, expected):
    c = candidate(kind=kind, value=value, label=label)
    result = local_policy.recommendations(local_job([c]))
    assert len(result) == 1 and result[0]['recommendation'] == expected


def test_other_positions_public_heading_cannot_turn_personal_phone_into_keep():
    c = candidate(kind='phone', value='010-1234-5678', label='신청인 휴대전화')
    c['locationContext']['evidence'].append({'relation': 'preceding_heading', 'text': '공용 문의창구'})
    assert local_policy.recommendations(local_job([c]))[0]['recommendation'] == 'full'


def seed(fmt='docx'):
    name = 'docx-report_minutes-01.docx' if fmt == 'docx' else 'report_minutes-01.pdf'
    source = ROOT/'fixtures/eval_v0/docs'/name
    return (*store.create_job(source.read_bytes(), source.name, fmt, False), source)


@pytest.mark.parametrize('fmt', ['docx', 'pdf'])
def test_local_automatic_uses_actual_saved_file_and_preserves_manual_choice(fmt):
    job, token, source = seed(fmt)
    email = next(c for c in job['candidates'] if c['type'] == 'email' and len(c['value'].split('@')[0]) > 1)
    manual = next(c for c in job['candidates'] if c['type'] == 'phone')
    manual.update(method='keep', mask=[], confirmed=True)
    before_manual = copy.deepcopy(manual)
    before_original = hashlib.sha256(store.source_path(job).read_bytes()).hexdigest()
    local_policy.refresh(job)
    result = auto_export.run(job, {'version': job['version'], 'mode': 'local_automatic'})
    after = {c['id']: c for c in result['candidates']}
    assert all(after[manual['id']][key] == value for key, value in before_manual.items())
    assert after[email['id']]['method'] == 'full'
    assert after[email['id']]['mask'] == []
    assert result['status'] == 'validated' and result['acknowledged'] is True
    assert result['acknowledgmentMode'] == 'local_automatic'
    assert result['artifact']['validationVersion'] == result['version']
    assert result['autoExport']['manualPreserved'] >= 1
    assert all(c['passed'] for c in result['artifact']['checks'])
    saved = workflow_files.artifact_path(job, ack_required=True)
    assert hashlib.sha256(saved.read_bytes()).hexdigest() == result['artifact']['sha256']
    assert hashlib.sha256(store.source_path(job).read_bytes()).hexdigest() == before_original
    assert source.read_bytes() == store.source_path(job).read_bytes()
    actual = engine.inspect_document(saved)
    text = '\n'.join(unit['text'] for unit in actual['units'])
    assert manual['value'] in text
    assert email['value'] not in text
    if fmt == 'docx':
        assert email['value'][0] + '**' + email['value'][email['value'].index('@'):] not in text
    fresh = store.load_job(job['id'], token)
    assert fresh['artifact'] == job['artifact'] and fresh['acknowledged'] is True


@pytest.mark.parametrize('fault', ['uninspected', 'unresolved', 'incomplete', 'wrong_mode', 'stale'])
def test_local_automatic_blocks_before_mutation_or_saved_file(fault):
    job, _, _ = seed()
    payload = {'version': job['version'], 'mode': 'local_automatic'}
    if fault == 'uninspected': job['uninspected'] = ['그림/그리기']
    if fault == 'unresolved': job['candidates'][0]['locationResolved'] = False
    if fault == 'incomplete': job['analysis']['incomplete'] = True
    if fault == 'wrong_mode': job['aiEnabled'] = True
    if fault == 'stale': payload['version'] -= 1
    before = copy.deepcopy(job)
    with pytest.raises(store.StoreError):
        auto_export.run(job, payload)
    assert job == before
    assert not list(store.workspace(job['id']).glob('copy-*'))


def _single_paragraph_docx(text):
    """Minimal real DOCX package, inspected and rendered by the service engine."""
    data = io.BytesIO()
    with ZipFile(data, 'w') as archive:
        archive.writestr('[Content_Types].xml',
            '<Types xmlns="http://schemas.openxmlformats.org/package/2006/content-types">'
            '<Default Extension="rels" ContentType="application/vnd.openxmlformats-package.relationships+xml"/>'
            '<Default Extension="xml" ContentType="application/xml"/>'
            '<Override PartName="/word/document.xml" ContentType="application/vnd.openxmlformats-officedocument.wordprocessingml.document.main+xml"/>'
            '</Types>')
        archive.writestr('_rels/.rels',
            '<Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships">'
            '<Relationship Id="rId1" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/officeDocument" Target="word/document.xml"/>'
            '</Relationships>')
        archive.writestr('word/document.xml',
            '<w:document xmlns:w="http://schemas.openxmlformats.org/wordprocessingml/2006/main">'
            '<w:body><w:p><w:r><w:t xml:space="preserve">'+escape(text)+
            '</w:t></w:r></w:p></w:body></w:document>')
    return data.getvalue()


@pytest.mark.parametrize('separator', [' | ', '; ', '\n'])
def test_mixed_paragraph_keeps_public_phone_but_removes_personal_phone(separator):
    public_phone, personal_phone = '02-123-4567', '010-2222-3333'
    original = _single_paragraph_docx(f'대표전화: {public_phone}{separator}개인 휴대전화: {personal_phone}')
    job, token = store.create_job(original, '혼합 연락처.docx', 'docx', False)
    assert len(job['candidates']) == 2
    ids = {c['value']: c['id'] for c in job['candidates']}
    recs = {s['candidateId']: s['recommendation'] for s in job['suggestions']}
    assert recs[ids[public_phone]] == 'keep'
    assert recs[ids[personal_phone]] == 'full'
    result = workflow.execute('auto-export', {'jobId': job['id'], 'token': token,
        'payload': {'version': job['version'], 'mode': 'local_automatic'}})
    assert result['status'] == 'validated' and result['acknowledged']
    fresh = store.load_job(job['id'], token)
    saved = workflow_files.artifact_path(fresh, ack_required=True)
    actual_text = '\n'.join(u['text'] for u in engine.inspect_document(saved)['units'])
    assert public_phone in actual_text
    assert personal_phone not in actual_text
    assert store.source_path(fresh).read_bytes() == original


def test_repeat_auto_export_reconsiders_automatic_email_and_preserves_manual_partial():
    job, token, source = seed('docx')
    args = {'jobId': job['id'], 'token': token}
    first = workflow.execute('auto-export', {**args, 'payload': {
        'version': job['version'], 'mode': 'local_automatic'}})
    first_hash = first['artifact']['sha256']
    partial_emails = {c['value']: c for c in first['candidates']
                      if c['type'] == 'email' and c['method'] == 'full'}
    assert len(partial_emails) >= 2
    manual, automatic = list(partial_emails.values())[:2]
    assert manual['decisionSource'] == automatic['decisionSource'] == 'local_automatic'
    manual_mask = [[0, min(2, manual['value'].index('@'))]]
    chosen = workflow.execute('plan', {**args, 'payload': {
        'version': first['version'], 'candidates': [{'id': manual['id'],
        'method': 'partial', 'mask': manual_mask, 'confirmed': True}]}})
    assert next(c for c in chosen['candidates'] if c['id'] == manual['id'])['decisionSource'] == 'user'
    changed = workflow.execute('context', {**args, 'payload': {
        'version': chosen['version'], 'context': {'recipient': '불특정다수'}}})
    assert changed['artifact'] is None and not changed['acknowledged']
    recommendations = {s['candidateId']: s['recommendation'] for s in changed['suggestions']}
    assert recommendations[automatic['id']] == recommendations[manual['id']] == 'full'
    second = workflow.execute('auto-export', {**args, 'payload': {
        'version': changed['version'], 'mode': 'local_automatic'}})
    current = {c['id']: c for c in second['candidates']}
    assert current[automatic['id']]['method'] == 'full' and current[automatic['id']]['mask'] == []
    assert current[automatic['id']]['decisionSource'] == 'local_automatic'
    assert current[manual['id']]['method'] == 'partial' and current[manual['id']]['mask'] == manual_mask
    assert current[manual['id']]['decisionSource'] == 'user'
    assert second['autoExport']['manualPreserved'] == 1
    assert second['status'] == 'validated' and second['acknowledged']
    assert second['artifact']['sha256'] != first_hash
    fresh = store.load_job(job['id'], token)
    saved = workflow_files.artifact_path(fresh, ack_required=True)
    actual_text = '\n'.join(u['text'] for u in engine.inspect_document(saved)['units'])
    assert automatic['value'] not in actual_text
    assert '**' + manual['value'][manual_mask[0][1]:] in actual_text
    assert store.source_path(fresh).read_bytes() == source.read_bytes()
