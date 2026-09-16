# backend/store.py
import hashlib
import hmac
import json
import os
import secrets
import shutil
import threading
import time
from contextlib import contextmanager
from datetime import datetime, timezone, timedelta
from pathlib import Path

import copy

from .engine import inspect_document
from .local_detection import detect_local

DATA_DIR = Path(os.environ.get('GARIMI_DATA_DIR',
                               Path(__file__).resolve().parent.parent / 'var' / 'jobs')).resolve()
TTL_SECONDS = 3600
MAX_JOBS = 100
LOCK_COUNT = 64
CREATION_LOCK = threading.Lock()
_ACTIVE_COUNTER = 0

def _mkdir700(p: Path) -> None:
    p.mkdir(mode=0o700, parents=True, exist_ok=True)


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat(timespec='seconds')


def _sha256_hex(b: bytes) -> str:
    return hashlib.sha256(b).hexdigest()


class StoreError(Exception):
    def __init__(self, status: int, code: str, message: str):
        self.status = status
        self.code = code
        self.message = message
        super().__init__(message)


def _valid_jid(jid: str) -> bool:
    if not isinstance(jid, str):
        return False
    if len(jid) != 32:
        return False
    for c in jid:
        if c not in '0123456789abcdef':
            return False
    return True


def _lock_index(jid: str) -> int:
    return int(jid, 16) % LOCK_COUNT


_CREATION_LOCKS = [threading.RLock() for _ in range(LOCK_COUNT)]


def workspace(jid: str) -> Path:
    if not _valid_jid(jid):
        raise StoreError(400, 'invalid_job_id',
                         '작업 ID는 32자리 소문자 hex여야 합니다.')
    p = DATA_DIR / jid
    if p.is_symlink():
        raise StoreError(400, 'invalid_job_id', '심볼릭 디렉토리는 허용되지 않습니다.')
    return p


def locked(jid: str):
    if not _valid_jid(jid):
        raise StoreError(400, 'invalid_job_id',
                         '작업 ID는 32자리 소문자 hex여야 합니다.')
    idx = _lock_index(jid)
    return _CREATION_LOCKS[idx]


@contextmanager
def _job_lock(jid: str):
    with locked(jid):
        yield


def source_path(job: dict) -> Path:
    fmt = job.get('format')
    if fmt not in ('pdf', 'docx'):
        raise StoreError(400, 'invalid_format', '형식은 pdf 또는 docx만 허용됩니다.')
    return workspace(job['id']) / ('original.' + fmt)


def _count_active() -> int:
    n = 0
    if not DATA_DIR.exists():
        return 0
    for entry in DATA_DIR.iterdir():
        if entry.is_dir() and not entry.is_symlink():
            n += 1
    return n


def create_job(data: bytes, filename: str, fmt: str, ai_enabled: bool, context=None):
    from .processing_requirements import require_creation_mode
    from .sharing_context import validate_context
    initial_context = validate_context({} if context is None else context)
    require_creation_mode(ai_enabled)
    if len(data) > 10 * 1024 * 1024:
        raise StoreError(400, 'payload_too_large', '업로드 데이터는 10MB를 초과할 수 없습니다.')
    if fmt not in ('pdf', 'docx'):
        raise StoreError(400, 'invalid_format', '지원 형식은 pdf와 docx입니다.')

    with CREATION_LOCK:
        if _count_active() >= MAX_JOBS:
            raise StoreError(503, 'service_unavailable',
                             '최대 작업 수를 초과했습니다.')

        jid = secrets.token_hex(16)
        token = secrets.token_urlsafe(32)
        token_hash = _sha256_hex(token.encode('utf-8'))
        ws = DATA_DIR / jid

        created = False
        try:
            ws.mkdir(mode=0o700, exist_ok=False)
            created = True
            src = ws / ('original.' + fmt)
            src.write_bytes(data)
            os.chmod(src, 0o600)
        except Exception:
            if created:
                shutil.rmtree(ws, ignore_errors=True)
            raise

    try:
        insp = inspect_document(str(src))
        cand = detect_local(src, insp)
        expires_at = datetime.now(timezone.utc) + timedelta(seconds=TTL_SECONDS)
        job = {
            'id': jid,
            'version': 1,
            'status': 'review',
            'fileName': filename,
            'format': fmt,
            'aiEnabled': ai_enabled,
            'expiresAt': expires_at.isoformat(timespec='seconds'),
            'units': copy.deepcopy(insp.get('units', [])),
            'metadata': copy.deepcopy(insp.get('metadata', [])),
            'uninspected': list(insp.get('uninspected', [])),
            'candidates': [copy.deepcopy(c) for c in cand],
            'documentType': None,
            'suggestions': [],
            'analysis': {
                'parse': {'status': 'disabled'},
                'classify': {'status': 'disabled'},
                'extract': {'status': 'disabled'},
                'hermes': {'status': 'disabled'},
                'warnings': [],
            },
            'artifact': None,
            'acknowledged': False,
            'metadataReviewed': False,
            'context': initial_context,
            '_tokenHash': token_hash,
            '_expiresEpoch': expires_at.timestamp(),
            '_inspection': insp,
            '_artifactName': None,
        }
        from .local_policy import refresh
        refresh(job)
        _write_job_atomic(job)
        return job, token
    except Exception:
        shutil.rmtree(ws, ignore_errors=True)
        raise


def _write_job_atomic(job: dict) -> None:
    ws = workspace(job['id'])
    if not ws.exists():
        raise StoreError(410, 'job_not_found', '작업 디렉토리가 없습니다.')
    jf = ws / 'job.json'
    tmp = ws / ('.tmp.' + secrets.token_hex(4))
    try:
        fd = os.open(str(tmp), os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
        try:
            with os.fdopen(fd, 'w', encoding='utf-8') as f:
                json.dump(job, f, ensure_ascii=False, indent=2)
                f.flush()
                os.fsync(f.fileno())
        except Exception:
            try:
                os.close(fd)
            except Exception:
                pass
            raise
        os.replace(str(tmp), str(jf))
        os.chmod(jf, 0o600)
    except Exception:
        try:
            tmp.unlink(missing_ok=True)
        except Exception:
            pass
        raise


def _read_job_raw(jid: str) -> dict:
    jf = workspace(jid) / 'job.json'
    if not jf.exists():
        raise StoreError(404, 'job_not_found', '작업 데이터가 없습니다.')
    try:
        with jf.open('r', encoding='utf-8') as f:
            return json.load(f)
    except Exception:
        raise StoreError(400, 'corrupt_job', '작업 데이터가 손상되었습니다.')


def load_job(jid: str, token: str = None):
    with _job_lock(jid):
        jf = workspace(jid) / 'job.json'
        if not jf.exists():
            raise StoreError(404, 'job_not_found', '작업을 찾을 수 없습니다.')
        try:
            job = json.loads(jf.read_text(encoding='utf-8'))
        except Exception:
            raise StoreError(400, 'corrupt_job', '작업 데이터가 손상되었습니다.')

        now = time.time()
        if job.get('_expiresEpoch', 0) < now:
            delete_job(jid)
            raise StoreError(410, 'job_expired', '작업이 만료되었습니다.')

        if token is not None:
            supplied = _sha256_hex(token.encode('utf-8'))
            stored = job.get('_tokenHash', '')
            if not hmac.compare_digest(supplied, stored):
                raise StoreError(403, 'forbidden', '인증 토큰이 유효하지 않습니다.')

        return job


def public_job(job: dict) -> dict:
    out = {}
    for k, v in job.items():
        if k.startswith('_'):
            continue
        if isinstance(v, (dict, list)):
            out[k] = copy.deepcopy(v)
        else:
            out[k] = v
    saved_stages = job.get('_analysisCheckpoint', {}).get('stages', {})
    out['analysisResume'] = {'completedStages': [k for k in ('parse', 'classify', 'extract') if saved_stages.get(k, {}).get('status') == 'completed'], 'savedBatches': len(job.get('_recommendationBatches', {}))}
    from .processing_requirements import blocked_reason, upstage_required
    if upstage_required():
        out['upstageRequired'] = True
        reason = blocked_reason(job)
        if reason:
            out['processingBlocked'] = reason[1]
            out['upstageReanalysisRequired'] = job.get('aiEnabled') is not True or job.get('status') == 'validated'
            # The saved legacy job remains untouched; never present its old
            # locally validated artifact as completed under the new policy.
            if out.get('status') == 'validated':
                out['status'] = 'review'
                out['artifact'] = None
                out['acknowledged'] = False
                out.pop('acknowledgmentMode', None)
                out.pop('autoExport', None)
    return out


def save_job(job: dict) -> None:
    with _job_lock(job['id']):
        ws = workspace(job['id'])
        if not ws.exists():
            raise StoreError(410, 'job_not_found', '작업 디렉토리가 없습니다.')
        _write_job_atomic(job)


def delete_job(jid: str) -> None:
    with _job_lock(jid):
        ws = workspace(jid)
        if not ws.exists():
            return
        shutil.rmtree(ws)


def invalidate(job: dict) -> None:
    for key in ('aiReview', 'autoExport', 'acknowledgmentMode'):
        job.pop(key, None)
    job['version'] += 1
    art_name = job.get('_artifactName')
    if art_name and job.get('artifact') is not None:
        if not isinstance(art_name, str):
            raise StoreError(400, 'invalid_artifact', '아티팩트 이름이 유효하지 않습니다.')
        if '/' in art_name or '\\' in art_name or art_name.startswith('/'):
            raise StoreError(400, 'invalid_artifact', '아티팩트 이름에 경로 구분자가 포함될 수 없습니다.')
        if art_name.startswith('..'):
            raise StoreError(400, 'invalid_artifact', '아티팩트 이름이 유효하지 않습니다.')
        if not art_name.startswith('copy-') or not art_name.endswith('.' + job.get('format', '')):
            raise StoreError(400, 'invalid_artifact', '아티팩트 이름이 유효하지 않습니다.')
        ws = workspace(job['id'])
        art_path = ws / art_name
        if not art_path.exists():
            pass
        else:
            if art_path.is_symlink():
                raise StoreError(400, 'invalid_artifact', '심볼릭 링크는 삭제할 수 없습니다.')
            try:
                art_path.unlink()
            except Exception:
                raise StoreError(500, 'artifact_delete_failed', '아티팩트 삭제에 실패했습니다.')
    job['artifact'] = None
    job['_artifactName'] = None
    job['acknowledged'] = False
    job['status'] = 'review'


def cleanup_expired() -> None:
    if not DATA_DIR.exists():
        return
    now = time.time()
    for entry in list(DATA_DIR.iterdir()):
        if entry.is_symlink():
            continue
        if not entry.is_dir():
            continue
        jid = entry.name
        if not _valid_jid(jid):
            continue
        try:
            with _job_lock(jid):
                ws = workspace(jid)
                if not ws.exists():
                    continue
                jf = ws / 'job.json'
                skip_mtime = False
                if jf.exists():
                    try:
                        job = json.loads(jf.read_text(encoding='utf-8'))
                        if isinstance(job, dict):
                            exp = job.get('_expiresEpoch')
                            # only a finite int/float (not bool) is a valid timestamp
                            if isinstance(exp, (int, float)) and not isinstance(exp, bool):
                                import math
                                if math.isfinite(exp):
                                    if exp < now:
                                        delete_job(jid)
                                        continue
                                    skip_mtime = True
                    except Exception:
                        pass
                if not skip_mtime:
                    mtime = entry.stat().st_mtime
                    if now - mtime > TTL_SECONDS:
                        shutil.rmtree(ws)
        except Exception:
            pass



def recover_jobs() -> None:
    # A single analysis server owns this volume; prior process admissions died.
    for marker in DATA_DIR.parent.glob('.admission-*'):
        if marker.is_file() or marker.is_symlink():
            marker.unlink(missing_ok=True)
    cleanup_expired()
    if not DATA_DIR.exists():
        return
    now = time.time()
    for entry in list(DATA_DIR.iterdir()):
        if entry.is_symlink():
            continue
        if not entry.is_dir():
            continue
        jid = entry.name
        if not _valid_jid(jid):
            continue
        try:
            with _job_lock(jid):
                ws = workspace(jid)
                if not ws.exists():
                    continue
                jf = ws / 'job.json'
                if not jf.exists():
                    continue
                try:
                    job = json.loads(jf.read_text(encoding='utf-8'))
                except Exception:
                    continue
                if job.get('_expiresEpoch', 0) < now:
                    continue
                if (job.get('aiReview') or {}).get('status') == 'running':
                    job['aiReview']['status'] = 'failed'
                    job['aiReview']['warnings'] = ['서버가 재시작되어 AI 검토가 중단되었습니다. 다시 검토할 수 있습니다.']
                    _write_job_atomic(job)
                status = job.get('status', '')
                if status in ('analyzing', 'rendering'):
                    invalidate(job)
                    if status == 'rendering':
                        job['status'] = 'failed'
                    w = job.get('analysis', {})
                    if status == 'analyzing':
                        for stage in ('parse', 'classify', 'extract', 'hermes'):
                            if w.get(stage, {}).get('status') in ('pending', 'running'):
                                w[stage] = {**w[stage], 'status': 'failed', 'errorCode': 'INTERRUPTED'}
                    if isinstance(w, dict):
                        warns = list(w.get('warnings', []))
                        warns.append('서버 재시작으로 처리가 중단됐습니다. 저장된 결과부터 이어서 진행할 수 있습니다.')
                        w['warnings'] = warns
                        job['analysis'] = w
                    _write_job_atomic(job)
        except Exception:
            pass


_mkdir700(DATA_DIR)
