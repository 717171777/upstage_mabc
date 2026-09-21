# backend/auto_export.py
import copy
from . import store, plans, workflow_files
from .judge import _validate_suggestion
from .masking_policy import model_selectable_presets


def run(job, payload):
    from .processing_requirements import assert_upstage_complete
    assert_upstage_complete(job)
    mode = payload.get('mode')
    if mode not in ('ai_automatic', 'local_automatic'):
        raise store.StoreError(400, 'invalid_mode', '지원하지 않는 모드입니다.')
    plans.check_version(job, payload.get('version'))
    if mode == 'ai_automatic' and job.get('aiEnabled') is not True:
        raise store.StoreError(400, 'ai_disabled', 'AI 자동 처리가 비활성화되어 있습니다.')
    if mode == 'local_automatic' and job.get('aiEnabled') is not False:
        raise store.StoreError(400, 'wrong_mode', '현재 작업은 로컬 처리 모드가 아닙니다.')

    analysis = job.get('analysis', {})
    if not isinstance(analysis, dict):
        raise store.StoreError(422, 'invalid_analysis', '분석 정보가 올바르지 않습니다.')
    if mode == 'ai_automatic':
        for stage in ('parse', 'classify', 'extract'):
            st = analysis.get(stage, {}).get('status')
            if st != 'completed':
                raise store.StoreError(422, 'analysis_incomplete', f'분석 단계가 완료되지 않았습니다: {stage}')
        hermes = analysis.get('hermes', {})
        if not isinstance(hermes, dict):
            raise store.StoreError(422, 'invalid_analysis', '분석 정보가 올바르지 않습니다.')
        if hermes.get('status') not in ('completed', 'not_needed'):
            raise store.StoreError(422, 'analysis_incomplete', 'Hermes 분석 상태가 유효하지 않습니다.')
    if analysis.get('incomplete') is True:
        raise store.StoreError(422, 'analysis_incomplete', '분석이 불완전합니다.')

    candidates = job.get('candidates', [])
    if not isinstance(candidates, list):
        raise store.StoreError(422, 'invalid_candidates', '후보자 목록이 올바르지 않습니다.')
    for c in candidates:
        if c.get('locationResolved') is not True:
            raise store.StoreError(422, 'location_unresolved', '모든 후보의 위치가 해결되어야 합니다.')
    uninspected = job.get('uninspected', [])
    if not isinstance(uninspected, list):
        raise store.StoreError(422, 'invalid_uninspected', '미검사 정보가 올바르지 않습니다.')
    if len(uninspected) != 0:
        raise store.StoreError(422, 'uninspected_remain', '미검사 영역이 남아 있어 수동 검토가 필요합니다.')

    manual_confirmed = []
    unconfirmed = []
    for c in candidates:
        if c.get('confirmed') is True and c.get('decisionSource') not in ('ai_automatic', 'local_automatic'):
            manual_confirmed.append(c)
        else:
            unconfirmed.append(c)

    suggestions = job.get('suggestions', [])
    if not isinstance(suggestions, list):
        raise store.StoreError(422, 'invalid_suggestions', '제안 정보가 올바르지 않습니다.')
    recs = [s for s in suggestions if isinstance(s, dict) and s.get('type') == 'recommendation']

    keep_info = ''
    if job.get('context', {}).get('keepInfo'):
        keep_info = job['context']['keepInfo']

    validated_map = {}
    for s in recs:
        if not isinstance(s, dict):
            continue
        for c in unconfirmed:
            cid = c['id']
            if s.get('candidateId') != cid:
                continue
            valid, errors = _validate_suggestion(s, {cid}, [c], keep_info)
            if errors or not valid:
                continue
            validated_map.setdefault(cid, []).append(valid)

    decisions = {}
    for c in unconfirmed:
        cid = c['id']
        recs_for_c = validated_map.get(cid, [])
        if not recs_for_c:
            decisions[cid] = {'method': 'full', 'mask': []}
            continue

        methods = {r['recommendation'] for r in recs_for_c}
        if methods == {'keep'}:
            decisions[cid] = {'method': 'keep', 'mask': []}
            continue
        # 전체 가림으로 모인 항목에 한해, 제안이 하나의 프리셋으로 일치할 때만
        # 완화한다. 프리셋이 갈리거나 없으면 전체 가림을 유지한다.
        preset_ids = {r.get('presetId') for r in recs_for_c}
        if methods == {'full'} and len(preset_ids) == 1:
            preset_id = next(iter(preset_ids))
            mask = model_selectable_presets(c).get(preset_id) if preset_id else None
            if mask:
                decisions[cid] = {'method': 'partial', 'mask': copy.deepcopy(mask)}
                continue
        decisions[cid] = {'method': 'full', 'mask': []}

    candidate_items = []
    for c in manual_confirmed:
        candidate_items.append({
            'id': c['id'],
            'method': c['method'],
            'mask': copy.deepcopy(c['mask']),
            'confirmed': True,
        })
    for c in unconfirmed:
        d = decisions[c['id']]
        candidate_items.append({
            'id': c['id'],
            'method': d['method'],
            'mask': copy.deepcopy(d['mask']),
            'confirmed': True,
        })

    metadata = job.get('metadata', [])
    if not isinstance(metadata, list):
        raise store.StoreError(422, 'invalid_metadata', '메타데이터 정보가 올바르지 않습니다.')
    if job.get('metadataReviewed') is True:
        metadata_actions = {m['id']: m.get('action', 'keep') for m in metadata}
    else:
        metadata_actions = {m['id']: 'delete' for m in metadata}

    clone = copy.deepcopy(job)
    plans.apply_plan(clone, {
        'version': payload['version'],
        'candidates': candidate_items,
        'metadataActions': metadata_actions,
        'metadataReviewed': True,
    })
    automatic_ids = {c['id'] for c in unconfirmed}
    for candidate in clone['candidates']:
        if candidate['id'] in automatic_ids:
            candidate['decisionSource'] = mode

    plans.assert_ready(clone)

    job.clear()
    job.update(clone)

    store.invalidate(job)
    store.save_job(job)

    workflow_files.render_copy(job, {'version': job['version']})

    artifact = job.get('artifact')
    if not artifact or not all(c.get('passed') is True for c in artifact['checks']):
        raise store.StoreError(422, 'missing_artifact', '아티팩트 정보가 없습니다.')

    workflow_files.acknowledge(job, {
        'version': job['version'],
        'validationVersion': artifact['validationVersion'],
        'sha256': artifact['sha256'],
    })

    job['acknowledgmentMode'] = mode

    full_count = 0
    partial_count = 0
    keep_count = 0
    manual_preserved = 0 if job.get('fullRedaction') is True else len(manual_confirmed)
    for c in job['candidates']:
        if c.get('confirmed') is True:
            m = c.get('method')
            if m == 'full':
                full_count += 1
            elif m == 'partial':
                partial_count += 1
            elif m == 'keep':
                keep_count += 1

    summary = {
        'full': full_count,
        'partial': partial_count,
        'keep': keep_count,
        'manualPreserved': manual_preserved,
        'version': job['version'],
        'sha256': artifact['sha256'],
    }
    job['autoExport'] = summary

    store.save_job(job)
    return store.public_job(job)
