import copy
import os
import secrets
import shutil
import tempfile
from pathlib import Path

from . import store, plans, upstage, workflow_files
from .copy_review_worker import run_review
from .llm_config import configured


def start(job, payload, token, executor, capacity):
    plans.check_version(job, payload.get('version'))

    if job.get('aiEnabled') is not True or not os.environ.get('UPSTAGE_API_KEY') or not configured():
        raise store.StoreError(400, 'ai_disabled', 'AI 검토가 비활성화되었거나 API 키가 설정되지 않았습니다.')

    if not upstage.can_analyze_document(store.source_path(job)):
        raise store.StoreError(403, 'not_synthetic', '실제 개인정보 문서는 AI 검토 대상이 아닙니다.')

    saved = workflow_files.artifact_path(job)

    if payload.get('sha256') != job['artifact']['sha256']:
        raise store.StoreError(409, 'sha_mismatch', '요청된 해시와 저장된 사본의 해시가 일치하지 않습니다.')

    if job.get('aiReview', {}).get('status') == 'running':
        raise store.StoreError(409, 'already_running', '이미 AI 검토가 진행 중입니다.')

    if not capacity.acquire(blocking=False):
        raise store.StoreError(503, 'capacity_full', '현재 검토 용량이 가득 찼습니다.')

    temp = None
    try:
        temp = tempfile.TemporaryDirectory(prefix='copy-review-', dir=store.workspace(job['id']))
        dest = Path(temp.name) / ('saved.' + job['format'])
        shutil.copyfile(saved, dest)
        os.chmod(dest, 0o600)

        request_id = secrets.token_hex(16)
        job['aiReview'] = {
            'status': 'running',
            'requestId': request_id,
            'version': job['version'],
            'sha256': job['artifact']['sha256'],
            'findings': [],
            'warnings': [],
            'stages': {
                'extract': {'status': 'pending'},
                'hermes': {'status': 'pending'},
            },
        }

        snapshot = copy.deepcopy(job)
        store.save_job(job)

        executor.submit(run_review, job['id'], token, snapshot, temp, capacity)
        return store.public_job(job)

    except Exception:
        try:
            if temp is not None:
                temp.cleanup()
        finally:
            capacity.release()
        if 'aiReview' in job:
            job['aiReview'] = {
                'status': 'failed',
                'requestId': job['aiReview'].get('requestId', ''),
                'version': job['version'],
                'sha256': job['artifact']['sha256'],
                'findings': [],
                'warnings': ['AI 검토 시작 중 오류가 발생했습니다.'],
                'stages': {
                    'extract': {'status': 'failed'},
                    'hermes': {'status': 'failed'},
                },
            }
            store.save_job(job)
        raise store.StoreError(503, 'review_start_failed', 'AI 검토 시작에 실패했습니다.')
