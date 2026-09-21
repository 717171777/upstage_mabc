"""Check the actual Hermes registry path, without calling a language model."""
import base64
import json
import hashlib
import io
import zipfile
from pathlib import Path
import pytest
from backend import store, upstage
from backend import hermes_extension as extension
from backend.llm_config import MODEL

ROOT = Path(__file__).resolve().parents[1]


@pytest.fixture(autouse=True)
def isolate(tmp_path, monkeypatch):
    monkeypatch.setattr(store, 'DATA_DIR', tmp_path)
    def forbidden(*args, **kwargs):
        raise AssertionError('External AI called during offline registry test')
    monkeypatch.setattr(upstage, 'analyze_file', forbidden)
    monkeypatch.delenv('UPSTAGE_API_KEY', raising=False)


def test_registration_and_real_dispatch(monkeypatch):
    extension.register_operations()
    name = 'garimi_document_operation'
    assert name in extension.registry.get_all_tool_names()
    assert extension.registry.get_toolset_for_tool(name) == 'garimi_document'
    original = extension.registry.dispatch
    seen = []
    def spy(name, args, **kwargs):
        seen.append((name, args['operation']))
        return original(name, args, **kwargs)
    monkeypatch.setattr(extension.registry, 'dispatch', spy)
    source = ROOT / 'fixtures/eval_v0/docs/docx-report_minutes-01.docx'
    created = extension.call('create', dict(data=base64.b64encode(source.read_bytes()).decode(), filename='합성.docx', format='docx', aiEnabled=False))
    assert created['job']['runtime']['platform'] == 'Hermes'
    assert created['job']['runtime']['llmUsed'] is False
    args = dict(jobId=created['job']['id'], token=created['token'])
    job = extension.call('get', args)
    assert job['id'] == args['jobId']
    assert seen == [(name, 'create'), (name, 'get')]
    assert '_tokenHash' not in job
    extension.call('delete', args)


def test_handler_sanitizes_unexpected_exception(monkeypatch):
    from backend import workflow
    def fail(*args, **kwargs):
        raise RuntimeError('private document and token sentinel')
    monkeypatch.setattr(workflow, 'execute', fail)
    extension.register_operations()
    raw = extension.registry.dispatch('garimi_document_operation', {'operation': 'get', 'args': {}})
    assert isinstance(raw, str)
    data = json.loads(raw)
    assert not data['ok'] and data['status'] == 500
    assert 'sentinel' not in raw


def test_registry_revalidates_missing_auth():
    extension.register_operations()
    with pytest.raises(store.StoreError):
        extension.call('get', {'jobId': 'a' * 32})


@pytest.mark.parametrize('fmt', ['pdf', 'docx'])
def test_optional_review_through_registry_to_saved_download(fmt, monkeypatch):
    """Exercise the production dispatch boundary and real file engine; AI results are seeded."""
    monkeypatch.setenv('GARIMI_REQUIRE_UPSTAGE', '0')
    filename = 'report_minutes-01.pdf' if fmt == 'pdf' else 'docx-report_minutes-01.docx'
    original = (ROOT / 'fixtures/eval_v0/docs' / filename).read_bytes()
    created = extension.call('create', dict(data=base64.b64encode(original).decode(), filename='합성.' + fmt, format=fmt, aiEnabled=False))
    args = dict(jobId=created['job']['id'], token=created['token'])
    job = store.load_job(args['jobId'], args['token'])
    job.update(aiEnabled=True, status='review', analysis={
        **{stage: {'status': 'completed'} for stage in ('parse', 'classify', 'extract')},
        'hermes': {'status': 'completed', 'required': True, 'model': MODEL}, 'warnings': [],
    })
    assert len(job['candidates']) >= 3
    deleted = next(c for c in job['candidates'] if c['type'] == 'phone')
    partial = next(c for c in job['candidates'] if c['type'] == 'email')
    retained = next(c for c in job['candidates'] if c['type'] == 'phone' and c['id'] != deleted['id'])
    assert len(partial['value']) > 1
    for candidate in job['candidates']:
        candidate.update(method='full', mask=[], confirmed=False, decisionSource='ai_default')
    store.save_job(job)
    monkeypatch.setenv('GARIMI_REQUIRE_UPSTAGE', '1')
    assert 'prepare-export' in extension.SCHEMA['properties']['operation']['enum']
    prepared = extension.call('prepare-export', {**args, 'payload': {'version': job['version'], 'candidates': [
        dict(id=deleted['id'], method='delete', mask=[], confirmed=True),
        dict(id=partial['id'], method='partial', mask=[[1, len(partial['value'])]], confirmed=True),
        dict(id=retained['id'], method='keep', mask=[], confirmed=True),
    ]}})
    assert prepared['runtime']['execution'] == 'registered_extension'
    assert all(c['confirmed'] for c in prepared['candidates'])
    by_id = {c['id']: c for c in prepared['candidates']}
    assert [by_id[c['id']]['method'] for c in [deleted, partial, retained]] == ['delete', 'partial', 'keep']
    planned = extension.call('plan', {**args, 'payload': {'version': prepared['version'], 'metadataReviewed': True}})
    rendered = extension.call('render', {**args, 'payload': {'version': planned['version']}})
    assert rendered['status'] == 'validated'
    assert all(check['passed'] for check in rendered['artifact']['checks'])
    assert extension.call('preview', {**args, 'variant': 'original'})['format'] == fmt
    assert extension.call('preview', {**args, 'variant': 'copy'})['format'] == fmt
    with pytest.raises(store.StoreError):
        extension.call('download', args)
    artifact = rendered['artifact']
    extension.call('ack', {**args, 'payload': {'version': rendered['version'], 'validationVersion': artifact['validationVersion'], 'sha256': artifact['sha256']}})
    downloaded = extension.call('download', {**args, 'filename': '검증한-공유본.' + fmt})
    output = base64.b64decode(downloaded['binary'])
    assert output != original
    assert hashlib.sha256(output).hexdigest() == artifact['sha256']
    if fmt == 'pdf':
        import fitz
        with fitz.open(stream=output, filetype='pdf') as document:
            assert len(document) > 0
            text = ''.join(page.get_text() for page in document)
            assert document[0].get_pixmap().width > 0
    else:
        with zipfile.ZipFile(io.BytesIO(output)) as document:
            assert document.testzip() is None
            assert 'word/document.xml' in document.namelist()
            from lxml import etree
            text = ''.join(etree.fromstring(document.read('word/document.xml')).itertext())
    normalized = ''.join(text.split())
    assert ''.join(retained['value'].split()) in normalized
    kept_values = {c['value'] for c in prepared['candidates'] if c['method'] == 'keep'}
    for candidate in prepared['candidates']:
        if candidate['method'] != 'keep' and candidate['value'] not in kept_values:
            assert ''.join(candidate['value'].split()) not in normalized, 'Hidden original survived in downloaded text'
