import base64
import os
import threading
import tempfile
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

from . import store, plans, upstage, workflow_files
from .processing_requirements import require_creation_mode, require_api_key, upstage_required
from .sharing_context import validate_context

EXECUTOR = ThreadPoolExecutor(max_workers=2)
CAPACITY = threading.BoundedSemaphore(4)

_KOREAN_UNSUPPORTED_SCAN = "스캔된 문서 또는 이미지 기반 PDF는 텍스트 추출이 지원되지 않습니다. 텍스트가 포함된 PDF 또는 DOCX 파일을 업로드해 주세요."
_KOREAN_TRACKED_CHANGES = "변경 추적이 포함된 문서는 처리할 수 없습니다. 변경 내용을 모두 적용한 후 다시 시도해 주세요."
_KOREAN_AI_ONLY_SYNTHETIC = "현재 서버는 합성 예시 문서만 외부 분석하도록 설정되어 있습니다. 일반 문서 이용을 위해 서버의 문서 전송 설정을 확인해 주세요."
_KOREAN_WORKER_ERROR = "문서 처리 중 내부 오류가 발생했습니다. 잠시 후 다시 시도해 주세요."


def _worker(jid, token):
    try:
        from .analysis import run_analysis
        run_analysis(jid, token)
    except Exception:
        with store.locked(jid):
            try:
                job = store.load_job(jid, token)
            except Exception:
                pass
            else:
                job['status'] = "review"
                for stage in ('parse', 'classify', 'extract', 'hermes'):
                    if job['analysis'].get(stage, {}).get('status') in ('running', 'pending'):
                        job['analysis'][stage] = {**job['analysis'][stage], 'status': 'failed', 'errorCode': 'WORKER_FAILED'}
                job['analysis']['warnings'].append(_KOREAN_WORKER_ERROR)
                store.save_job(job)
    finally:
        CAPACITY.release()


def _assert_accepting():
    if (store.DATA_DIR.parent / '.draining').exists():
        raise store.StoreError(503, 'MAINTENANCE', '진행 중인 작업을 마친 뒤 서버를 업데이트합니다. 잠시 후 시작해 주세요.')


def _create(args):
    _assert_accepting()
    if not isinstance(args, dict):
        raise store.StoreError(422, "INVALID_ARGS", "잘못된 요청입니다.")
    context = validate_context(args.get('context', {}))

    data = args.get("data")
    if not isinstance(data, str):
        raise store.StoreError(422, "INVALID_DATA", "데이터는 문자열이어야 합니다.")

    if len(data) > 14 * 1024 * 1024:
        raise store.StoreError(422, "DATA_TOO_LARGE", "데이터가 너무 큽니다.")

    try:
        decoded = base64.b64decode(data, validate=True)
    except Exception:
        raise store.StoreError(422, "INVALID_BASE64", "데이터 형식이 올바르지 않습니다.")

    if len(decoded) == 0:
        raise store.StoreError(422, "EMPTY_DATA", "데이터가 비어 있습니다.")
    if len(decoded) > 10 * 1024 * 1024:
        raise store.StoreError(422, "DATA_TOO_LARGE", "데이터가 너무 큽니다.")

    fmt = args.get("format")
    if fmt not in ("pdf", "docx"):
        raise store.StoreError(422, "INVALID_FORMAT", "지원하지 않는 형식입니다.")

    ai_enabled = args.get("aiEnabled")
    if not isinstance(ai_enabled, bool):
        raise store.StoreError(422, "INVALID_AI_ENABLED", "AI 설정은 불리언이어야 합니다.")
    require_creation_mode(ai_enabled)

    filename = args.get("filename")
    if not isinstance(filename, str):
        raise store.StoreError(422, "INVALID_FILENAME", "파일명은 문자열이어야 합니다.")
    if not (1 <= len(filename) <= 160):
        raise store.StoreError(422, "INVALID_FILENAME", "파일명 길이가 올바르지 않습니다.")
    if "/" in filename or "\\" in filename:
        raise store.StoreError(422, "INVALID_FILENAME", "파일명에 경로 구분자가 포함될 수 없습니다.")
    if any(ord(c) < 32 or ord(c) == 127 for c in filename):
        raise store.StoreError(422, "INVALID_FILENAME", "파일명에 제어 문자가 포함될 수 없습니다.")

    lower = filename.lower()
    if fmt == "pdf" and not lower.endswith(".pdf"):
        raise store.StoreError(422, "INVALID_FILENAME", "파일 확장자가 일치하지 않습니다.")
    if fmt == "docx" and not lower.endswith(".docx"):
        raise store.StoreError(422, "INVALID_FILENAME", "파일 확장자가 일치하지 않습니다.")

    try:
        job, token = store.create_job(decoded, filename, fmt, ai_enabled, context=context)
    except ValueError as e:
        msg = str(e)
        if any(k in msg for k in ("스캔", "텍스트 층", "이미지 기반")):
            raise store.StoreError(422, "UNSUPPORTED_SCAN", _KOREAN_UNSUPPORTED_SCAN)
        if any(k in msg for k in ("변경 추적", "수정 이력", "tracked")):
            raise store.StoreError(422, "TRACKED_CHANGES", _KOREAN_TRACKED_CHANGES)
        raise store.StoreError(422, "CREATE_FAILED", "문서 생성에 실패했습니다.")

    acquired = False
    submitted = False

    try:
        if ai_enabled:
            api_key = os.environ.get("UPSTAGE_API_KEY", "")
            if not api_key:
                store.delete_job(job['id'])
                require_api_key()

            if not upstage.can_analyze_document(store.source_path(job)):
                store.delete_job(job['id'])
                raise store.StoreError(422, "AI_ONLY_SYNTHETIC", _KOREAN_AI_ONLY_SYNTHETIC)

            if not CAPACITY.acquire(blocking=False):
                store.delete_job(job['id'])
                raise store.StoreError(503, "SERVICE_UNAVAILABLE", "AI 서비스가 현재 이용할 수 없습니다.")
            acquired = True

            job['status'] = "analyzing"
            job['analysis'] = {
                "parse": {"status": "pending"},
                "classify": {"status": "pending"},
                "extract": {"status": "pending"},
                "hermes": {"status": "pending"},
                "warnings": []
            }
            store.save_job(job)

            try:
                EXECUTOR.submit(_worker, job['id'], token)
                submitted = True
            except Exception:
                store.delete_job(job['id'])
                raise store.StoreError(503, "SERVICE_UNAVAILABLE", "AI 서비스가 현재 이용할 수 없습니다.")
    except store.StoreError:
        store.delete_job(job['id'])
        raise
    except Exception:
        store.delete_job(job['id'])
        raise store.StoreError(503, "SERVICE_UNAVAILABLE", "AI 서비스가 현재 이용할 수 없습니다.")
    finally:
        if acquired and not submitted:
            CAPACITY.release()

    return {"job": store.public_job(job), "token": token}


def execute(operation, args):
    # Publish admission before checking the drain flag. A deployment therefore
    # observes either this marker or the persisted analyzing/rendering job.
    if operation in ('create', 'context', 'retry-analysis', 'render', 'auto-export', 'ai-review'):
        with tempfile.NamedTemporaryFile(prefix='.admission-', dir=store.DATA_DIR.parent):
            _assert_accepting()
            return _execute(operation, args)
    return _execute(operation, args)


def _execute(operation, args):
    if not isinstance(operation, str) or not operation:
        raise store.StoreError(422, "UNKNOWN_OPERATION", "지원하지 않는 작업입니다.")

    ALLOWED = {"create", "get", "plan", "manual", "resolve", "context",
               "render", "preview", "page", "ack", "download", "delete", "auto-export", "ai-review", "retry-analysis"}

    if operation not in ALLOWED:
        raise store.StoreError(422, "UNKNOWN_OPERATION", "지원하지 않는 작업입니다.")

    if not isinstance(args, dict):
        raise store.StoreError(422, "INVALID_ARGS", "잘못된 요청입니다.")

    if operation == "create":
        return _create(args)

    jid = args.get("jobId")
    token = args.get("token")

    if not isinstance(jid, str) or not jid:
        raise store.StoreError(422, "MISSING_JOB_ID", "작업 ID가 필요합니다.")
    if not isinstance(token, str) or not token:
        raise store.StoreError(422, "MISSING_TOKEN", "인증 토큰이 필요합니다.")

    with store.locked(jid):
        job = store.load_job(jid, token)

        if operation == "get":
            return store.public_job(job)

        if operation == "delete":
            store.delete_job(jid)
            return {"deleted": True}

        payload = args.get("payload", {})
        if not isinstance(payload, dict):
            raise store.StoreError(422, "INVALID_PAYLOAD", "페이로드는 딕셔너리여야 합니다.")

        if operation in ('context', 'retry-analysis', 'render', 'auto-export', 'ai-review'):
            _assert_accepting()

        if operation == 'retry-analysis':
            if job.get('status') in ('analyzing', 'rendering') or (job.get('aiReview') or {}).get('status') == 'running':
                raise store.StoreError(409, 'JOB_BUSY', '이미 처리 중입니다.')
            plans.check_version(job, payload.get('version'))
            if not job.get('aiEnabled'):
                raise store.StoreError(422, 'UPSTAGE_REANALYSIS_REQUIRED', '문서를 다시 올려 분석해 주세요.')
            require_api_key()
            if not upstage.can_analyze_document(store.source_path(job)):
                raise store.StoreError(422, 'AI_ONLY_SYNTHETIC', _KOREAN_AI_ONLY_SYNTHETIC)
            if not CAPACITY.acquire(blocking=False):
                raise store.StoreError(503, 'SERVICE_UNAVAILABLE', 'AI 서비스가 현재 이용할 수 없습니다.')
            submitted = False
            try:
                store.invalidate(job)
                job['status'] = 'analyzing'
                job['analysis']['warnings'] = []
                saved = job.get('_analysisCheckpoint', {}).get('stages', {})
                for stage in ('parse', 'classify', 'extract'):
                    record = saved.get(stage, {})
                    job['analysis'][stage] = {'status': 'completed' if record.get('status') == 'completed' else 'pending', 'model': record.get('model')}
                job['analysis']['hermes'] = {'status': 'pending', 'required': True}
                store.save_job(job)
                EXECUTOR.submit(_worker, job['id'], token)
                submitted = True
            finally:
                if not submitted:
                    CAPACITY.release()
                    job['status'] = 'review'
                    job['analysis']['hermes'] = {'status': 'failed', 'required': True, 'errorCode': 'WORKER_FAILED'}
                    store.save_job(job)
            return store.public_job(job)

        if operation == "auto-export":
            from .auto_export import run
            return run(job, payload)

        if operation == "ai-review":
            from .copy_review import start
            return start(job, payload, token, EXECUTOR, CAPACITY)

        if operation == "plan":
            plans.apply_plan(job, payload)
            store.invalidate(job)
            store.save_job(job)
            return store.public_job(job)

        if operation == "manual":
            plans.add_manual(job, payload)
            from .location_context import attach_location_context
            attach_location_context(store.source_path(job), job['_inspection'], job['candidates'])
            from .local_policy import refresh
            refresh(job)
            store.invalidate(job)
            store.save_job(job)
            return store.public_job(job)

        if operation == "resolve":
            plans.resolve_candidate(job, payload)
            from .location_context import attach_location_context
            attach_location_context(store.source_path(job), job['_inspection'], job['candidates'])
            from .local_policy import refresh
            refresh(job)
            store.invalidate(job)
            store.save_job(job)
            return store.public_job(job)

        if operation == "context":
            ai_on = job['aiEnabled']
            if upstage_required() and not ai_on:
                raise store.StoreError(422, 'UPSTAGE_REANALYSIS_REQUIRED',
                    '이전 로컬 작업의 원본을 다시 올려 Upstage 분석을 시작해 주세요.')
            acquired_ctx = False
            submitted_ctx = False
            if ai_on:
                require_api_key()
                if not CAPACITY.acquire(blocking=False):
                    raise store.StoreError(503, "SERVICE_UNAVAILABLE", "AI 서비스가 현재 이용할 수 없습니다.")
                acquired_ctx = True
                try:
                    plans.apply_context(job, payload)
                    job.pop('_analysisCheckpoint', None)
                    job.pop('_recommendationBatches', None)
                    store.invalidate(job)
                    job['status'] = "analyzing"
                    job['analysis'] = {
                        "parse": {"status": "pending"},
                        "classify": {"status": "pending"},
                        "extract": {"status": "pending"},
                        "hermes": {"status": "pending"},
                        "warnings": []
                    }
                    store.save_job(job)
                    try:
                        EXECUTOR.submit(_worker, job['id'], token)
                        submitted_ctx = True
                    except Exception:
                        job['status'] = 'review'
                        for stage in ('parse', 'classify', 'extract', 'hermes'):
                            job['analysis'][stage] = {'status': 'failed', 'errorCode': 'WORKER_FAILED'}
                        job['analysis']['warnings'].append(_KOREAN_WORKER_ERROR)
                        store.save_job(job)
                        raise store.StoreError(503, 'SERVICE_UNAVAILABLE', 'AI 서비스가 현재 이용할 수 없습니다.')
                finally:
                    if acquired_ctx and not submitted_ctx:
                        CAPACITY.release()
            else:
                plans.apply_context(job, payload)
                from .local_policy import refresh
                refresh(job)
                store.invalidate(job)
                store.save_job(job)
            return store.public_job(job)

        if operation == "render":
            return workflow_files.render_copy(job, payload)

        if operation == "ack":
            return workflow_files.acknowledge(job, payload)

        if operation == "preview":
            variant = args.get("variant", "original")
            return workflow_files.preview(job, variant)

        if operation == "page":
            page = args.get("page")
            if not isinstance(page, int) or page < 1:
                raise store.StoreError(422, "INVALID_PAGE", "페이지 번호가 올바르지 않습니다.")
            variant = args.get("variant", "original")
            return workflow_files.page_image(job, page, variant)

        if operation == "download":
            filename = args.get("filename")
            if filename is not None and not isinstance(filename, str):
                raise store.StoreError(422, "INVALID_FILENAME", "파일명이 올바르지 않습니다.")
            return workflow_files.download(job, filename)

    raise store.StoreError(422, "UNKNOWN_OPERATION", "지원하지 않는 작업입니다.")
