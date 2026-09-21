"""Persistent user policy, independent of AI recommendations and presets."""
from .store import StoreError


def enforce(job):
    if job.get('fullRedaction') is not True:
        return
    for candidate in job.get('candidates', []):
        candidate.update(method='full', mask=[],
                         confirmed=candidate.get('locationResolved') is True,
                         decisionSource='user')
    for metadata in job.get('metadata', []):
        metadata['action'] = 'delete'
    job['metadataReviewed'] = True


def assert_policy(job):
    if job.get('fullRedaction') is not True:
        return
    if job.get('uninspected'):
        raise StoreError(422, 'uninspected_remain',
                         '전체 가림 전에 검사하지 못한 영역을 확인해야 합니다.')
    if any(c.get('method') != 'full' or c.get('mask') != []
           or c.get('locationResolved') is not True or c.get('confirmed') is not True
           for c in job.get('candidates', [])):
        raise StoreError(422, 'full_redaction_required',
                         '모든 개인정보의 위치를 확인하고 전체 가림해야 합니다.')
    if any(m.get('action') != 'delete' for m in job.get('metadata', [])):
        raise StoreError(422, 'full_redaction_required',
                         '전체 가림 중에는 문서 속성도 삭제해야 합니다.')
