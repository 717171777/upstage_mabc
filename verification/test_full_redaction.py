"""User policy and actual saved-copy checks; synthetic data, no external AI."""
import base64
import copy
from collections import Counter
from pathlib import Path
from zipfile import ZipFile

import fitz
import pytest

from backend import analysis, analysis_merge, auto_export, engine, plans, store, workflow, workflow_files
from backend.full_redaction import assert_policy
from backend.local_detection import detect_local
from verification.test_local_detection import docx, paragraph


ROWS = [
    ('참가자', '김가람', 'name'), ('연락처', '010-2345-6789', 'phone'),
    ('이메일', 'garam@example.test', 'email'),
    ('주소', '서울특별시 중구 가상로 123', 'address'),
    ('생년월일', '1990-02-03', 'dob'), ('주민등록번호', '900203-1234567', 'resident_id'),
    ('외국인등록번호', '900203-5234567', 'foreign_id'), ('여권번호', 'M12345678', 'passport'),
    ('운전면허번호', '11-22-123456-78', 'driver_license'), ('계좌번호', '123-456789-12345', 'account'),
    ('카드번호', '1234 5678 1234 5678', 'card'), ('사번', 'EMP-2026-001', 'management_id'),
]


@pytest.fixture(params=['docx', 'pdf'])
def document(request, tmp_path):
    lines = [f'{label}: {value}' for label, value, _ in ROWS]
    lines += ['문서번호: DOC-2026-009', '출장일: 2020-01-02', '담당자 검토 메모']
    if request.param == 'docx':
        return docx(tmp_path, ''.join(paragraph(line) for line in lines))
    path = tmp_path / 'synthetic.pdf'
    with fitz.open() as pdf:
        page = pdf.new_page()
        for i, line in enumerate(lines):
            page.insert_text((40, 50 + 25 * i), line, fontname='korea', fontsize=11)
        pdf.set_metadata({'author': '합성 작성자'})
        pdf.save(path)
    return path


@pytest.fixture
def job(document, tmp_path, monkeypatch):
    monkeypatch.setattr(store, 'DATA_DIR', tmp_path / 'jobs')
    store.DATA_DIR.mkdir()
    monkeypatch.delenv('GARIMI_REQUIRE_UPSTAGE', raising=False)
    job, token = store.create_job(document.read_bytes(), document.name, document.suffix[1:], False)
    job['candidates'] = detect_local(store.source_path(job), job['_inspection'])
    # Deliberately mix previous keep, partial and full choices.
    for i, c in enumerate(job['candidates']):
        c.update(method='keep' if i % 2 == 0 else 'partial',
                 mask=[] if i % 2 == 0 else [[1, 2]], confirmed=True, decisionSource='user')
    for m in job['metadata']:
        m['action'] = 'keep'
    job['metadataReviewed'] = True
    store.save_job(job)
    return job, token


def enable(job, token):
    workflow.execute('plan', {'jobId': job['id'], 'token': token,
                             'payload': {'version': job['version'], 'fullRedaction': True}})
    return store.load_job(job['id'], token)


def test_all_twelve_types_detected_and_removed_from_actual_copy(job):
    job, token = job
    expected = Counter((typ, value) for _, value, typ in ROWS)
    assert Counter((c['type'], c['value']) for c in job['candidates']) == expected
    original = store.source_path(job).read_bytes()
    job = enable(job, token)
    assert job['fullRedaction'] is True
    assert_policy(job)
    assert store.public_job(store.load_job(job['id'], token))['fullRedaction'] is True
    workflow_files.render_copy(job, {'version': job['version']})
    preview = workflow_files.preview(job, 'copy')
    text = '\n'.join(u['text'] for u in preview['units'])
    for _, value, _ in ROWS:
        assert value not in text
    assert 'DOC-2026-009' in text and '2020-01-02' in text
    assert all(not m['value'] for m in preview['metadata'])
    artifact = job['artifact']
    workflow_files.acknowledge(job, {'version': job['version'],
        'validationVersion': artifact['validationVersion'], 'sha256': artifact['sha256']})
    data = base64.b64decode(workflow_files.download(job)['binary'])
    assert data != original and store.source_path(job).read_bytes() == original
    if job['format'] == 'docx':
        with ZipFile(workflow_files.artifact_path(job)) as archive:
            xml = '\n'.join(archive.read(n).decode('utf-8') for n in archive.namelist() if n.endswith('.xml'))
        assert all(value not in xml for _, value, _ in ROWS)
    else:
        with fitz.open(stream=data, filetype='pdf') as pdf:
            assert all(not page.search_for(value) for page in pdf for _, value, _ in ROWS)


def test_switch_invalidates_copy_and_off_does_not_reveal_previous_choices(job):
    job, token = job
    job = enable(job, token)
    workflow_files.render_copy(job, {'version': job['version']})
    old_copy = workflow_files.artifact_path(job)
    result = workflow.execute('plan', {'jobId': job['id'], 'token': token,
        'payload': {'version': job['version'], 'fullRedaction': False}})
    assert result['fullRedaction'] is False and result['artifact'] is None
    assert not old_copy.exists() and not result['acknowledged']
    assert all(c['method'] == 'full' for c in result['candidates'])
    candidate = result['candidates'][0]
    result = workflow.execute('plan', {'jobId': job['id'], 'token': token,
        'payload': {'version': result['version'], 'candidates': [
            {'id': candidate['id'], 'method': 'keep', 'mask': [], 'confirmed': True}]}})
    assert result['candidates'][0]['method'] == 'keep'


@pytest.mark.parametrize('mode', ['local_automatic', 'ai_automatic'])
def test_ai_and_client_cannot_relax_full_redaction(job, mode):
    job, token = job
    job = enable(job, token)
    candidate = job['candidates'][0]
    plans.apply_plan(job, {'version': job['version'], 'candidates': [
        {'id': candidate['id'], 'method': 'keep', 'mask': [], 'confirmed': True}],
        'metadataActions': {m['id']: 'keep' for m in job['metadata']}})
    job['suggestions'] = [{'candidateId': candidate['id'], 'type': 'recommendation', 'recommendation': 'keep'}]
    analysis.apply_binary_defaults(job)
    assert_policy(job)
    if mode == 'ai_automatic':
        job['aiEnabled'] = True
        job['analysis'] = {stage: {'status': 'completed'} for stage in ('parse', 'classify', 'extract', 'hermes')}
    result = auto_export.run(job, {'version': job['version'], 'mode': mode})
    assert result['autoExport']['full'] == len(ROWS)
    assert result['autoExport']['partial'] == result['autoExport']['keep'] == 0


def test_unresolved_extraction_blocks_render_and_download(job):
    job, token = job
    job = enable(job, token)
    analysis_merge.merge_extractions(job, {'extracted': {'passport': [{'raw_value': 'M'}]}})
    unresolved = [c for c in job['candidates'] if not c['locationResolved']]
    assert unresolved and all(c['method'] == 'full' and not c['confirmed'] for c in unresolved)
    with pytest.raises(store.StoreError):
        workflow_files.render_copy(job, {'version': job['version']})
    with pytest.raises(store.StoreError):
        workflow_files.download(job)


def test_uninspected_and_tampered_decisions_fail_closed(job):
    job, token = job
    job = enable(job, token)
    job['uninspected'] = ['합성 미검사 영역']
    with pytest.raises(store.StoreError, match='검사하지 못한'):
        workflow_files.render_copy(job, {'version': job['version']})
    job['uninspected'] = []
    workflow_files.render_copy(job, {'version': job['version']})
    job['candidates'][0]['method'] = 'keep'
    with pytest.raises(store.StoreError, match='전체 가림'):
        workflow_files.artifact_path(job)


@pytest.mark.parametrize('value', ['true', 1, None])
def test_invalid_switch_values_are_atomic(job, value):
    job, _ = job
    original = copy.deepcopy(job)
    with pytest.raises(store.StoreError):
        plans.apply_plan(job, {'version': job['version'], 'fullRedaction': value})
    assert job == original


def test_new_manual_candidate_inherits_policy(job):
    job, token = job
    job = enable(job, token)
    unit = next(u for u in job['_inspection']['units'] if 'DOC-2026-009' in u['text'])
    start = unit['text'].index('DOC-2026-009')
    result = workflow.execute('manual', {'jobId': job['id'], 'token': token, 'payload': {
        'version': job['version'], 'type': 'management_id', 'unitId': unit['id'],
        'start': start, 'end': start + len('DOC-2026-009')}})
    assert_policy(result)
    assert len(result['candidates']) == len(ROWS) + 1


def test_resolving_new_extraction_applies_full_policy(job):
    job, token = job
    job = enable(job, token)
    analysis_merge.merge_extractions(job, {'extracted': {'name': [{'raw_value': '없는합성이름'}]}})
    unresolved = next(c for c in job['candidates'] if not c['locationResolved'])
    store.save_job(job)
    target = next(c for c in job['candidates'] if c['type'] == 'name' and c['locationResolved'])
    result = workflow.execute('resolve', {'jobId': job['id'], 'token': token, 'payload': {
        'version': job['version'], 'candidateId': unresolved['id'],
        'unitId': target['unitId'], 'start': target['start'], 'end': target['end']}})
    assert_policy(result)


def test_full_mode_does_not_bypass_required_analysis(job, monkeypatch):
    job, token = job
    job = enable(job, token)
    monkeypatch.setenv('GARIMI_REQUIRE_UPSTAGE', '1')
    with pytest.raises(store.StoreError):
        workflow_files.render_copy(job, {'version': job['version']})


def test_stale_toggle_cannot_change_current_policy(job):
    job, token = job
    old_version = job['version']
    current = enable(job, token)
    with pytest.raises(store.StoreError) as error:
        workflow.execute('plan', {'jobId': job['id'], 'token': token,
            'payload': {'version': old_version, 'fullRedaction': False}})
    assert error.value.status == 409
    assert store.load_job(job['id'], token) == current
