import copy
import hashlib
import json
from . import store, upstage
from .analysis_merge import merge_extractions
from .location_context import attach_location_context
from .document_policy import candidate_for_judge


def run_analysis(jid, token):
    from .judge import judge_exceptions

    # 1. lock 안에서 작업 읽기, 조건 체크
    with store.locked(jid):
        try:
            job = store.load_job(jid, token)
        except store.StoreError:
            return
        if not job.get('aiEnabled') or job.get('status') != 'analyzing':
            return
        snapshot = copy.deepcopy(job)
        version = job['version']
        path = store.source_path(job)

    requested_type = snapshot['documentType'] if snapshot.get('documentTypeSource') != 'upstage' else None
    cache_key = hashlib.sha256(json.dumps({
        'revision': 1, 'source': hashlib.sha256(path.read_bytes()).hexdigest(),
        'context': snapshot['context'], 'documentType': requested_type,
    }, sort_keys=True, ensure_ascii=False).encode()).hexdigest()
    saved_cache = snapshot.get('_analysisCheckpoint', {})
    stage_cache = saved_cache.get('stages', {}) if saved_cache.get('key') == cache_key else {}

    def checkpoint(stage, record):
        with store.locked(jid):
            current = store.load_job(jid, token)
            if current.get('version') != version or current.get('status') != 'analyzing':
                raise RuntimeError('Analysis cancelled')
            saved = current.setdefault('_analysisCheckpoint', {})
            if saved.get('key') != cache_key:
                saved.clear()
                saved.update(key=cache_key, stages={})
            saved['stages'][stage] = copy.deepcopy(record)
            store.save_job(current)

    # 2. progress 콜백 (parse/classify/extract만, lock 안에서 최신 작업 갱신)
    def progress(stage, record):
        if stage not in ('parse', 'classify', 'extract'):
            return
        with store.locked(jid):
            try:
                cur = store.load_job(jid, token)
            except store.StoreError:
                return
            if cur.get('version') != version or cur.get('status') != 'analyzing':
                return
            # record는 {'status','model'}이므로 stage in record 조건은 항상 False
            # 정상 진행 시 cur['analysis'][stage]에 record 내용 저장
            cur['analysis'][stage] = {
                'status': record.get('status'),
                'model': record.get('model'),
            }
            store.save_job(cur)

    # 3. Upstage 분석 (lock 밖, 네트워크 호출)
    try:
        result = upstage.analyze_file(
            path, True,
            document_type=requested_type,
            progress=progress,
            sharing_context=snapshot['context'],
            stage_cache=stage_cache, checkpoint=checkpoint,
        )
    except Exception:
        with store.locked(jid):
            try:
                job = store.load_job(jid, token)
            except store.StoreError:
                return
            if job.get('version') != version:
                return
            # analyze_file 실패: 분석 단계들을 failed로 명시, 고정 warnings, review 저장
            job['status'] = 'review'
            job['analysis'] = job.get('analysis', {})
            for stage in ('parse', 'classify', 'extract', 'hermes'):
                if job['analysis'].get(stage, {}).get('status') != 'completed':
                    job['analysis'][stage] = {'status': 'failed', 'errorCode': 'WORKER_FAILED'}
            job['analysis']['warnings'] = ['분석 중 오류가 발생했습니다.']
            store.save_job(job)
        return

    # 결과 반영: 각 단계, warnings, documentType
    snapshot['analysis'] = snapshot.get('analysis', {})
    for stage in ('parse', 'classify', 'extract'):
        if stage in result.get('stages', {}):
            snapshot['analysis'][stage] = result['stages'][stage]
    # warnings는 snapshot['analysis']['warnings']에 저장 (snapshot['warnings'] 아님)
    snapshot['analysis']['warnings'] = result.get('warnings', [])
    snapshot['documentType'] = result.get('documentType', snapshot['documentType'])
    if snapshot.get('documentTypeSource') != 'user' and result.get('stages', {}).get('classify', {}).get('status') == 'completed':
        snapshot['documentTypeSource'] = 'upstage'

    merge_extractions(snapshot, result)
    with store.locked(jid):
        try:
            fresh = store.load_job(jid, token)
        except store.StoreError:
            return
        if fresh.get('version') != version or fresh.get('status') != 'analyzing':
            return
        attach_location_context(path, snapshot['_inspection'], snapshot['candidates'])
        snapshot['_analysisCheckpoint'] = copy.deepcopy(fresh.get('_analysisCheckpoint', {}))
        store.save_job(snapshot)  # Persist merged candidates before the recommendation request.

    # 4. 예외검토 후보 선별 - merge 후 snapshot['candidates']를 순회
    candidates = snapshot.get('candidates', [])
    if not isinstance(candidates, list):
        candidates = []

    # Every detected item gets a binary decision; masking methods stay user-controlled.
    judge_candidates = [candidate_for_judge(c) for c in candidates if isinstance(c, dict)]

    need_review = len(judge_candidates) > 0

    if need_review:
        # 진행단계 hermes running 저장 (실제 호출 직전)
        with store.locked(jid):
            try:
                cur = store.load_job(jid, token)
            except store.StoreError:
                return
            if cur.get('version') != version or cur.get('status') != 'analyzing':
                return
            cur['analysis']['hermes'] = {'status': 'running', 'required': True}
            store.save_job(cur)

        def recommendation_progress(record, batch_key=None, suggestions=None):
            with store.locked(jid):
                current = store.load_job(jid, token)
                if current.get('version') != version or current.get('status') != 'analyzing':
                    raise RuntimeError('Analysis cancelled')
                if batch_key is not None and suggestions is not None:
                    current.setdefault('_recommendationBatches', {})[batch_key] = copy.deepcopy(suggestions)
                current['analysis']['hermes'] = {'status': 'running', 'required': True, **record}
                store.save_job(current)
                snapshot['_recommendationBatches'] = copy.deepcopy(current.get('_recommendationBatches', {}))
                snapshot['analysis']['hermes'] = copy.deepcopy(current['analysis']['hermes'])

        payload = {
            'context': snapshot['context'],
            'documentType': snapshot['documentType'],
            'candidates': judge_candidates,
            'jobWorkspace': str(store.workspace(jid)),
            '_cacheScope': cache_key,
            '_cachedBatches': snapshot.get('_recommendationBatches', {}),
            '_progress': recommendation_progress,
        }

        try:
            jres = judge_exceptions(payload)
        except Exception:
            # judge 실패: snapshot 분석결과 보존, hermes failed, 한국어 경고 추가
            snapshot['analysis']['hermes'] = {
                'status': 'failed',
                'required': True,
                'warning': '예외검토 중 오류가 발생했습니다.',
            }
            existing = snapshot['analysis'].get('warnings', [])
            if isinstance(existing, list):
                existing.append('예외검토 중 오류가 발생했습니다.')
            else:
                snapshot['analysis']['warnings'] = ['예외검토 중 오류가 발생했습니다.']
            # 최종 review 저장으로 진행 (중간 return 금지)
            snapshot['status'] = 'review'
            with store.locked(jid):
                try:
                    cur = store.load_job(jid, token)
                except store.StoreError:
                    return
                if cur.get('version') != version or cur.get('status') != 'analyzing':
                    return
                store.save_job(snapshot)
            return

        if jres and 'suggestions' in jres:
            # 네트워크 결과를 snapshot에만 합침 (job 중간저장 후 옛 snapshot로 덮는 버그 금지)
            snapshot['suggestions'] = jres['suggestions']
            if jres.get('status') == 'completed':
                apply_binary_defaults(snapshot)
            snapshot['analysis']['hermes'] = {
                **snapshot['analysis'].get('hermes', {}),
                'status': jres.get('status', 'not_needed'),
                'errorCode': jres.get('errorCode'),
                'model': jres.get('model'),
                'required': True,
            }
            jres_warnings = jres.get('warnings', [])
            if isinstance(jres_warnings, list):
                existing = snapshot['analysis'].get('warnings', [])
                if isinstance(existing, list):
                    snapshot['analysis']['warnings'] = existing + jres_warnings
                else:
                    snapshot['analysis']['warnings'] = jres_warnings
        else:
            # Required judge returned no usable contract: never mark it skipped.
            snapshot['analysis']['hermes'] = {
                'status': 'failed',
                'model': None,
                'required': True,
            }

        # 최종: snapshot['status']='review' 반드시 설정, current.status=='analyzing' 확인 후 저장
        snapshot['status'] = 'review'
        with store.locked(jid):
            try:
                cur = store.load_job(jid, token)
            except store.StoreError:
                return
            if cur.get('version') != version or cur.get('status') != 'analyzing':
                return
            store.save_job(snapshot)
    else:
        # 예외검토 불필요: status not_needed, model None, 호출 없음
        snapshot['analysis']['hermes'] = {
            'status': 'not_needed',
            'model': None,
            'required': False,
        }
        snapshot['status'] = 'review'
        with store.locked(jid):
            try:
                cur = store.load_job(jid, token)
            except store.StoreError:
                return
            if cur.get('version') != version or cur.get('status') != 'analyzing':
                return
            store.save_job(snapshot)


def apply_binary_defaults(job):
    """Validated AI choices are drafts, never overwrite confirmed user selections."""
    from .judge import _validate_suggestion
    from .document_policy import candidate_for_judge
    keep_info = job.get('context', {}).get('keepInfo') or ''
    for candidate in job.get('candidates', []):
        if candidate.get('confirmed') and candidate.get('decisionSource') not in ('ai_automatic', 'local_automatic'):
            continue
        valid = []
        for suggestion in job.get('suggestions', []):
            if suggestion.get('candidateId') != candidate['id']:
                continue
            checked, errors = _validate_suggestion(suggestion, {candidate['id']}, [candidate_for_judge(candidate)], keep_info)
            if not errors:
                valid.append(checked['recommendation'])
        candidate.update(method='keep' if valid == ['keep'] else 'full', mask=[], confirmed=False, decisionSource='ai_default')
