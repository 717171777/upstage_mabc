"""Independent checks of saved-copy state and byte-level download binding."""
import base64
import hashlib
from pathlib import Path
import pytest
from backend import store, workflow_files as wf

ROOT = Path(__file__).resolve().parents[1]


@pytest.fixture(params=['docx', 'pdf'])
def job(request, tmp_path, monkeypatch):
    monkeypatch.setattr(store, 'DATA_DIR', tmp_path)
    fmt = request.param
    name = 'docx-report_minutes-01.docx' if fmt == 'docx' else 'report_minutes-01.pdf'
    job, token = store.create_job((ROOT / 'fixtures/eval_v0/docs' / name).read_bytes(), '합성 예시.' + fmt, fmt, False)
    return job


def ready(job):
    for c in job['candidates']:
        c.update(method='full', mask=[], confirmed=True)
    job['metadataReviewed'] = True
    store.save_job(job)


def test_real_render_ack_download_and_invalidation(job):
    with pytest.raises(store.StoreError):
        wf.download(job)
    with pytest.raises(store.StoreError):
        wf.render_copy(job, {'version': job['version']})
    ready(job)
    original = store.source_path(job).read_bytes()
    result = wf.render_copy(job, {'version': job['version']})
    assert result['status'] == 'validated' and result['artifact']
    assert not result['acknowledged']
    assert store.source_path(job).read_bytes() == original
    preview = wf.preview(job, 'copy')
    assert preview['format'] == job['format']
    assert all(m['value'] == '' for m in preview['metadata']), 'Deleted metadata remained'
    with pytest.raises(store.StoreError):
        wf.download(job)
    art = result['artifact']
    with pytest.raises(store.StoreError):
        wf.acknowledge(job, dict(version=job['version'], validationVersion=art['validationVersion'], sha256='0' * 64))
    wf.acknowledge(job, dict(version=job['version'], validationVersion=art['validationVersion'], sha256=art['sha256']))
    binary = wf.download(job, '검토한 공유본.' + job['format'])
    data = base64.b64decode(binary['binary'])
    assert hashlib.sha256(data).hexdigest() == art['sha256']
    assert data != original
    assert '_tokenHash' not in result
    store.invalidate(job)
    with pytest.raises(store.StoreError):
        wf.download(job)


def test_artifact_tampering_blocked(job):
    ready(job)
    wf.render_copy(job, {'version': job['version']})
    path = wf.artifact_path(job)
    path.write_bytes(path.read_bytes() + b'altered')
    with pytest.raises(store.StoreError):
        wf.artifact_path(job)
    with pytest.raises(store.StoreError):
        wf.preview(job, 'copy')


def test_failed_render_discards_partial_file(job, monkeypatch):
    from backend import engine
    ready(job)
    def fail(source, destination, *args):
        Path(destination).write_bytes(b'partial synthetic output')
        raise RuntimeError('private sentinel should not escape')
    monkeypatch.setattr(engine, 'render_document', fail)
    with pytest.raises(store.StoreError) as ex:
        wf.render_copy(job, {'version': job['version']})
    assert 'private sentinel' not in str(ex.value)
    assert job['status'] == 'failed' and job['artifact'] is None
    assert not list(store.workspace(job['id']).glob('copy-*'))


def test_download_filename_validation(job):
    ready(job)
    wf.render_copy(job, {'version': job['version']})
    art = job['artifact']
    wf.acknowledge(job, dict(version=job['version'], validationVersion=art['validationVersion'], sha256=art['sha256']))
    for filename in ('', '../copy.' + job['format'], 'copy\n.' + job['format'], 'file.txt', ' copy.' + job['format']):
        with pytest.raises(store.StoreError):
            wf.download(job, filename)


def test_preview_variant_and_page_validation(job):
    with pytest.raises(store.StoreError):
        wf.preview(job, '../original')
    if job['format'] == 'pdf':
        png = wf.page_image(job, 1, 'original')
        assert base64.b64decode(png['binary']).startswith(b'\x89PNG')
        for page in (0, -1, True, 999):
            with pytest.raises(store.StoreError):
                wf.page_image(job, page, 'original')
    else:
        with pytest.raises(store.StoreError):
            wf.page_image(job, 1, 'original')
