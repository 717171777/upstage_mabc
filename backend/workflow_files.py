from pathlib import Path
import hashlib
import base64
import os
import re
import fitz
from . import store, plans, engine, pdf_io
from .document_layout import build_layout


def artifact_path(job, ack_required=False):
    from .processing_requirements import assert_upstage_complete
    assert_upstage_complete(job)
    if job['status'] != 'validated':
        raise store.StoreError(409, 'invalid_status', '문서가 검증되지 않았습니다.')

    artifact = job.get('artifact')
    if not artifact:
        raise store.StoreError(409, 'missing_artifact', '아티팩트 정보가 없습니다.')

    if artifact['version'] != job['version']:
        raise store.StoreError(409, 'version_mismatch', '버전 정보가 일치하지 않습니다.')

    if artifact['validationVersion'] != job['version']:
        raise store.StoreError(409, 'validation_version_mismatch', '검증 버전 정보가 일치하지 않습니다.')

    expected_name = f"copy-v{job['version']}.{job['format']}"
    if job.get('_artifactName') != expected_name:
        raise store.StoreError(409, 'artifact_name_mismatch', '아티팩트 이름이 일치하지 않습니다.')

    workspace = store.workspace(job['id'])
    path = workspace / expected_name

    if not path.exists():
        raise store.StoreError(409, 'missing_file', '아티팩트 파일이 존재하지 않습니다.')

    if not path.is_file() or path.is_symlink():
        raise store.StoreError(409, 'invalid_file', '아티팩트 파일이 유효하지 않습니다.')

    sha256 = hashlib.sha256()
    with open(path, 'rb') as f:
        for chunk in iter(lambda: f.read(8192), b''):
            sha256.update(chunk)
    actual_sha = sha256.hexdigest()

    if actual_sha != artifact['sha256']:
        raise store.StoreError(409, 'sha_mismatch', '파일 해시 값이 일치하지 않습니다.')

    if ack_required and not job.get('acknowledged'):
        raise store.StoreError(409, 'not_acknowledged', '문서가 승인되지 않았습니다.')

    return path


def render_copy(job, payload):
    plans.check_version(job, payload.get('version'))
    plans.assert_ready(job)

    job['status'] = 'rendering'
    store.save_job(job)

    destination = store.workspace(job['id']) / f"copy-v{job['version']}.{job['format']}"

    try:
        source = store.source_path(job)
        inspection = job['_inspection']
        candidates = job.get('candidates', [])
        metadata_actions = {m['id']: m['action'] for m in job.get('metadata', [])}

        report = engine.render_document(source, destination, inspection, candidates, metadata_actions)

        if not report['passed']:
            raise ValueError('렌더링 검증이 통과되지 않았습니다.')

        if not destination.is_file() or destination.is_symlink():
            raise ValueError('렌더링 결과 파일이 유효하지 않습니다.')

        sha256 = hashlib.sha256()
        with open(destination, 'rb') as f:
            for chunk in iter(lambda: f.read(8192), b''):
                sha256.update(chunk)
        actual_sha = sha256.hexdigest()

        if actual_sha != report['sha256']:
            raise ValueError('렌더링 결과 해시 값이 일치하지 않습니다.')

        os.chmod(destination, 0o600)

        job['artifact'] = {
            'sha256': report['sha256'],
            'version': job['version'],
            'validationVersion': job['version'],
            'checks': report['checks'],
        }
        job['_artifactName'] = f"copy-v{job['version']}.{job['format']}"
        job['acknowledged'] = False
        job['status'] = 'validated'
        store.save_job(job)
        return store.public_job(job)

    except Exception:
        if destination.exists():
            try:
                destination.unlink()
            except OSError:
                pass
        job['artifact'] = None
        job['_artifactName'] = None
        job['acknowledged'] = False
        job['status'] = 'failed'
        warnings = job.get('analysis', {}).get('warnings', [])
        warnings.append('렌더링 처리 중 오류가 발생했습니다.')
        job.setdefault('analysis', {})['warnings'] = warnings
        store.save_job(job)
        raise store.StoreError(422, 'render_failed', '렌더링에 실패했습니다.')


def acknowledge(job, payload):
    artifact_path(job)

    version = payload.get('version')
    if type(version) is not int or version != job['version']:
        raise store.StoreError(409, 'version_mismatch', '버전 정보가 일치하지 않습니다.')

    validation_version = payload.get('validationVersion')
    if type(validation_version) is not int or validation_version != job['artifact']['validationVersion']:
        raise store.StoreError(409, 'validation_version_mismatch', '검증 버전 정보가 일치하지 않습니다.')

    if payload.get('sha256') != job['artifact']['sha256']:
        raise store.StoreError(409, 'sha256_mismatch', 'SHA-256 해시 값이 일치하지 않습니다.')

    job['acknowledged'] = True
    store.save_job(job)
    return store.public_job(job)


def preview(job, variant):
    if variant not in ('original', 'copy'):
        raise store.StoreError(422, 'invalid_variant', '지원하지 않는 미리보기 유형입니다.')

    if variant == 'original':
        inspection = job.get('_inspection', {})
        result = {}
        for key in ('format', 'units', 'metadata', 'uninspected', 'pageSizes'):
            if key in inspection:
                result[key] = inspection[key]
        if job['format'] == 'docx':
            result['blocks'] = build_layout(store.source_path(job), result.get('units', []))
        return result

    path = artifact_path(job)

    if job['format'] == 'pdf':
        info = pdf_io.inspect(path, allow_empty=True)
    elif job['format'] == 'docx':
        info = engine.inspect_document(path)
    else:
        raise store.StoreError(422, 'unsupported_format', '지원하지 않는 문서 형식입니다.')

    result = {}
    for key in ('format', 'units', 'metadata', 'uninspected', 'pageSizes'):
        if key in info:
            result[key] = info[key]
    if job['format'] == 'docx':
        result['blocks'] = build_layout(path, result.get('units', []))
    return result


def page_image(job, page, variant):
    if job.get('format') != 'pdf':
        raise store.StoreError(422, 'invalid_format', '페이지 이미지는 PDF 문서에서만 사용할 수 있습니다.')

    if variant not in ('original', 'copy'):
        raise store.StoreError(422, 'invalid_variant', '변형 유형은 original 또는 copy여야 합니다.')

    if type(page) is not int or page < 1:
        raise store.StoreError(422, 'invalid_page', '페이지 번호는 1 이상의 정수여야 합니다.')

    if variant == 'original':
        path = store.source_path(job)
    else:
        path = artifact_path(job)

    with fitz.open(path) as doc:
        if page > doc.page_count:
            raise store.StoreError(422, 'page_out_of_range', '요청한 페이지가 문서 범위를 벗어났습니다.')

        pixmap = doc.get_page_pixmap(page - 1, matrix=fitz.Matrix(1.25, 1.25), alpha=False)
        png_bytes = pixmap.tobytes("png")

    binary = base64.b64encode(png_bytes).decode('ascii')
    return {'binary': binary, 'mediaType': 'image/png'}


def download(job, filename=None):
    path = artifact_path(job, ack_required=True)

    if filename is None:
        filename = f"{Path(job['fileName']).stem}-공유본.{job['format']}"

    if not isinstance(filename, str) or len(filename) < 1 or len(filename) > 160:
        raise store.StoreError(422, 'invalid_filename', '파일 이름 형식이 유효하지 않습니다.')

    if filename != filename.strip():
        raise store.StoreError(422, 'invalid_filename', '파일 이름에 공백 문자가 포함될 수 없습니다.')

    if re.search(r'[\x00-\x1f\x7f]', filename):
        raise store.StoreError(422, 'invalid_filename', '파일 이름에 제어 문자가 포함될 수 없습니다.')

    if '/' in filename or '\\' in filename:
        raise store.StoreError(422, 'invalid_filename', '파일 이름에 경로 구분자가 포함될 수 없습니다.')

    expected_suffix = f".{job['format']}"
    if not filename.lower().endswith(expected_suffix.lower()):
        raise store.StoreError(422, 'invalid_filename', '파일 이름 확장자가 문서 형식과 일치하지 않습니다.')

    with open(path, 'rb') as f:
        data = f.read()

    binary = base64.b64encode(data).decode('ascii')

    if job['format'] == 'pdf':
        media_type = 'application/pdf'
    elif job['format'] == 'docx':
        media_type = 'application/vnd.openxmlformats-officedocument.wordprocessingml.document'
    else:
        media_type = 'application/octet-stream'

    return {'binary': binary, 'mediaType': media_type, 'filename': filename}
