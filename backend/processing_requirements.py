"""Deployment policy for mandatory Upstage analysis; no network operations."""
import os


def upstage_required() -> bool:
    return os.environ.get('GARIMI_REQUIRE_UPSTAGE') == '1'


def require_creation_mode(ai_enabled) -> None:
    if not upstage_required():
        return
    from .store import StoreError
    if ai_enabled is not True:
        raise StoreError(422, 'UPSTAGE_REQUIRED',
            '이 서비스는 모든 문서를 Upstage로 분석합니다. 외부 전송 안내를 확인하고 AI 분석을 켜서 새 작업을 시작해 주세요.')
    require_api_key()


def require_api_key() -> None:
    from .store import StoreError
    if not os.environ.get('UPSTAGE_API_KEY', '').strip():
        raise StoreError(503, 'UPSTAGE_UNAVAILABLE',
            'Upstage API 키가 설정되지 않아 문서를 분석할 수 없습니다. 서버 설정 후 다시 시도해 주세요.')


def blocked_reason(job: dict):
    if not upstage_required():
        return None
    if job.get('aiEnabled') is not True:
        return ('UPSTAGE_REANALYSIS_REQUIRED',
                'Upstage를 거치지 않은 이전 작업입니다. 원본을 다시 올려 Upstage 분석을 완료해 주세요.')
    analysis = job.get('analysis')
    if not isinstance(analysis, dict):
        return ('UPSTAGE_ANALYSIS_INCOMPLETE', 'Upstage 분석 결과가 없어 완료할 수 없습니다.')
    for stage, label in (('parse', '문서 파싱'), ('classify', '문서 분류'), ('extract', '정보 추출')):
        record = analysis.get(stage)
        if not isinstance(record, dict) or record.get('status') != 'completed':
            return ('UPSTAGE_ANALYSIS_INCOMPLETE',
                    f'Upstage {label}가 완료되지 않았습니다. 분석을 완료한 뒤 공유용 사본을 만들 수 있습니다.')
    hermes = analysis.get('hermes')
    if not isinstance(hermes, dict):
        return ('UPSTAGE_ANALYSIS_INCOMPLETE', 'Hermes·SP4 검토 상태를 확인할 수 없습니다.')
    status = hermes.get('status')
    if status == 'completed':
        if hermes.get('model') != 'solar-pro4-260806':
            return ('UPSTAGE_ANALYSIS_INCOMPLETE', '지정된 Solar Pro 4 모델의 검토 완료를 확인할 수 없습니다.')
    elif status != 'not_needed' or hermes.get('required') is not False:
        return ('UPSTAGE_ANALYSIS_INCOMPLETE',
                '필요한 Hermes·SP4 검토가 완료되지 않았습니다. 분석을 다시 실행해 주세요.')
    if analysis.get('incomplete'):
        return ('UPSTAGE_ANALYSIS_INCOMPLETE', '분석이 불완전하여 공유용 사본을 완료할 수 없습니다.')
    return None


def assert_upstage_complete(job: dict) -> None:
    reason = blocked_reason(job)
    if reason:
        from .store import StoreError
        raise StoreError(422, *reason)
