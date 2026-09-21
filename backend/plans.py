import copy
from .store import StoreError


def check_version(job, version):
    if type(version) is not int:
        raise StoreError(409, 'conflict', '작업 상태가 변경되었습니다. 다시 확인해 주세요.')
    if version != job['version']:
        raise StoreError(409, 'conflict', '작업 상태가 변경되었습니다. 다시 확인해 주세요.')
    if job['status'] in ('analyzing', 'rendering'):
        raise StoreError(409, 'conflict', '작업 상태가 변경되었습니다. 다시 확인해 주세요.')
    return None


def validate_decision(candidate):
    method = candidate.get('method')
    if method not in ('full', 'partial', 'delete', 'keep'):
        raise StoreError(422, 'invalid_plan', '처리 방법이 올바르지 않습니다.')

    confirmed = candidate.get('confirmed')
    if type(confirmed) is not bool:
        raise StoreError(422, 'invalid_plan', '확인 상태가 올바르지 않습니다.')

    if confirmed is True and candidate.get('locationResolved') is not True:
        raise StoreError(422, 'invalid_plan', '위치 정보가 해결되지 않았습니다.')

    mask = candidate.get('mask')
    if not isinstance(mask, list):
        raise StoreError(422, 'invalid_plan', '마스크 정보가 올바르지 않습니다.')

    value = candidate['value']
    for pair in mask:
        if not isinstance(pair, list) or len(pair) != 2:
            raise StoreError(422, 'invalid_plan', '마스크 범위 형식이 올바르지 않습니다.')
        a, b = pair
        if type(a) is not int or type(b) is not int:
            raise StoreError(422, 'invalid_plan', '마스크 범위 형식이 올바르지 않습니다.')
        if isinstance(a, bool) or isinstance(b, bool):
            raise StoreError(422, 'invalid_plan', '마스크 범위 형식이 올바르지 않습니다.')
        if not (0 <= a < b <= len(value)):
            raise StoreError(422, 'invalid_plan', '마스크 범위가 유효하지 않습니다.')

    sorted_mask = sorted(mask, key=lambda p: p[0])
    for i in range(len(sorted_mask) - 1):
        if sorted_mask[i][1] > sorted_mask[i + 1][0]:
            raise StoreError(422, 'invalid_plan', '겹치는 마스크 범위가 있습니다.')

    masked_len = sum(b - a for a, b in sorted_mask)
    value_len = len(value)

    if method == 'partial':
        if masked_len == 0 or masked_len == value_len:
            raise StoreError(422, 'invalid_plan', '부분 처리는 일부 문자만 마스킹해야 합니다.')
    else:
        if mask:
            raise StoreError(422, 'invalid_plan', '다른 처리 방법은 마스크를 사용할 수 없습니다.')

    return None


def apply_plan(job, payload):
    if not isinstance(payload, dict):
        raise StoreError(422, 'invalid_plan', '요청 형식이 올바르지 않습니다.')

    check_version(job, payload.get('version'))

    if 'fullRedaction' in payload and type(payload['fullRedaction']) is not bool:
        raise StoreError(422, 'invalid_plan', '전체 가림 설정은 켜기 또는 끄기여야 합니다.')

    candidates = payload.get('candidates')
    if candidates is None:
        candidates = []
    if not isinstance(candidates, list):
        raise StoreError(422, 'invalid_plan', '요청 형식이 올바르지 않습니다.')

    metadata_actions = payload.get('metadataActions')
    if metadata_actions is None:
        metadata_actions = {}
    if not isinstance(metadata_actions, dict):
        raise StoreError(422, 'invalid_plan', '요청 형식이 올바르지 않습니다.')

    # metadataReviewed: if key present, must be bool (None rejected since type(None) is not bool)
    if 'metadataReviewed' in payload:
        metadata_reviewed = payload['metadataReviewed']
        if type(metadata_reviewed) is not bool:
            raise StoreError(422, 'invalid_plan', '요청 형식이 올바르지 않습니다.')
    else:
        metadata_reviewed = None

    for mid, action in metadata_actions.items():
        if action not in ('delete', 'keep'):
            raise StoreError(422, 'invalid_plan', '메타데이터 동작이 올바르지 않습니다.')
        if not any(m['id'] == mid for m in job['metadata']):
            raise StoreError(422, 'invalid_plan', '메타데이터 식별자가 존재하지 않습니다.')

    working = copy.deepcopy(job)

    existing_map = {c['id']: c for c in working['candidates']}
    seen_ids = set()

    for item in candidates:
        if not isinstance(item, dict):
            raise StoreError(422, 'invalid_plan', '요청 형식이 올바르지 않습니다.')

        if set(item.keys()) != {'id', 'method', 'mask', 'confirmed'}:
            raise StoreError(422, 'invalid_plan', '요청 형식이 올바르지 않습니다.')

        cid = item['id']
        # Validate id is nonempty str before using in set/dict
        if not isinstance(cid, str) or cid == '':
            raise StoreError(422, 'invalid_plan', '요청 형식이 올바르지 않습니다.')

        if cid in seen_ids:
            raise StoreError(422, 'invalid_plan', '중복된 후보자 식별자가 있습니다.')
        if cid not in existing_map:
            raise StoreError(422, 'invalid_plan', '존재하지 않는 후보자 식별자입니다.')
        seen_ids.add(cid)

        orig = existing_map[cid]
        # Deep copy entire existing candidate, update only the four decision fields
        merged = copy.deepcopy(orig)
        merged['id'] = cid
        merged['method'] = item['method']
        merged['mask'] = copy.deepcopy(item['mask'])
        merged['confirmed'] = item['confirmed']
        merged['decisionSource'] = 'user'

        validate_decision(merged)
        existing_map[cid] = merged

    new_candidates = []
    for c in working['candidates']:
        if c['id'] in seen_ids:
            new_candidates.append(existing_map[c['id']])
        else:
            new_candidates.append(c)
    working['candidates'] = new_candidates

    if metadata_actions:
        new_metadata = []
        for m in working['metadata']:
            mid = m['id']
            if mid in metadata_actions:
                m = copy.deepcopy(m)
                m['action'] = metadata_actions[mid]
            new_metadata.append(m)
        working['metadata'] = new_metadata

    if 'metadataReviewed' in payload:
        working['metadataReviewed'] = metadata_reviewed

    if 'fullRedaction' in payload:
        working['fullRedaction'] = payload['fullRedaction']
    from .full_redaction import enforce
    enforce(working)

    job.clear()
    job.update(working)
    return None

from .engine import TYPES, DOCUMENT_TYPES, candidate_id

def _selected_range(job, payload):
    try:
        unit_id = payload['unitId']
        start = payload['start']
        end = payload['end']
    except (KeyError, TypeError):
        raise StoreError(422, 'invalid_plan', '필수 필드 누락')
    if not isinstance(unit_id, str) or unit_id == '':
        raise StoreError(422, 'invalid_plan', 'unitId는 비어있지 않은 문자열이어야 합니다')
    if not (isinstance(start, int) and not isinstance(start, bool)) or not (isinstance(end, int) and not isinstance(end, bool)):
        raise StoreError(422, 'invalid_plan', '시작/끝은 정수여야 합니다')
    units = job['_inspection']['units']
    unit = next((u for u in units if u['id'] == unit_id), None)
    if unit is None:
        raise StoreError(422, 'invalid_plan', '단위가 존재하지 않습니다')
    if not (0 <= start < end <= len(unit['text'])):
        raise StoreError(422, 'invalid_plan', '범위 오류')
    if end - start > 1000:
        raise StoreError(422, 'invalid_plan', '선택 길이가 1000을 초과')
    return unit, start, end

def add_manual(job, payload):
    try:
        version = payload['version']
        typ = payload['type']
    except (KeyError, TypeError):
        raise StoreError(422, 'invalid_plan', '필수 필드 누락')
    check_version(job, version)
    if typ not in TYPES:
        raise StoreError(422, 'invalid_plan', '유효하지 않은 타입')
    if len(job['candidates']) >= 1000:
        raise StoreError(422, 'invalid_plan', '후보가 1000명을 초과')
    unit, start, end = _selected_range(job, payload)
    for c in job['candidates']:
        if c.get('locationResolved') and c.get('unitId') == unit['id']:
            if 'start' not in c or 'end' not in c:
                continue
            if not (end <= c['start'] or start >= c['end']):
                raise StoreError(422, 'invalid_plan', '기존 선택된 영역과 겹침')
    value = unit['text'][start:end]
    c = {
        'id': candidate_id(typ, unit['id'], start, end),
        'type': typ,
        'value': value,
        'unitId': unit['id'],
        'start': start,
        'end': end,
        'page': unit.get('page'),
        'method': 'full',
        'mask': [],
        'confirmed': False,
        'locationResolved': True,
        'source': 'manual',
        'reason': '직접 선택한 항목입니다.',
        'alternativeTypes': []
    }
    job['candidates'].append(c)
    return c

def resolve_candidate(job, payload):
    try:
        version = payload['version']
        candidate_id_target = payload['candidateId']
    except (KeyError, TypeError):
        raise StoreError(422, 'invalid_plan', '필수 필드 누락')
    check_version(job, version)
    unresolved = None
    for c in job['candidates']:
        if c['id'] == candidate_id_target:
            if c.get('locationResolved') is True:
                raise StoreError(422, 'invalid_plan', '이미 해결된 대상입니다')
            unresolved = c
            break
    if unresolved is None:
        raise StoreError(422, 'invalid_plan', '대상 후보가 없거나 이미 해결됨')
    unit, new_start, new_end = _selected_range(job, payload)
    exact_matches = []
    for c in job['candidates']:
        if c is unresolved:
            continue
        if not c.get('locationResolved'):
            continue
        if 'unitId' not in c or 'start' not in c or 'end' not in c:
            continue
        if c['unitId'] == unit['id'] and c['start'] == new_start and c['end'] == new_end:
            exact_matches.append(c)
    if len(exact_matches) == 1:
        existing = exact_matches[0]
        if unresolved['type'] not in existing.get('alternativeTypes', []):
            existing.setdefault('alternativeTypes', []).append(unresolved['type'])
        existing['confirmed'] = False
        job['candidates'].remove(unresolved)
        return existing
    if len(exact_matches) > 1:
        raise StoreError(422, 'invalid_plan', '동일 범위의 후보가 여러 개 존재')
    for c in job['candidates']:
        if c is unresolved:
            continue
        if not c.get('locationResolved'):
            continue
        if 'unitId' not in c or 'start' not in c or 'end' not in c:
            continue
        if c['unitId'] == unit['id']:
            if not (new_end <= c['start'] or new_start >= c['end']):
                raise StoreError(422, 'invalid_plan', '다른 해결된 후보와 겹침')
    old_value = unresolved.get('value')
    unresolved['start'] = new_start
    unresolved['end'] = new_end
    unresolved['value'] = unit['text'][new_start:new_end]
    unresolved['extractedValue'] = old_value
    unresolved['unitId'] = unit['id']
    unresolved['confirmed'] = False
    unresolved['locationResolved'] = True
    unresolved['source'] = 'manual_resolution'
    unresolved['method'] = 'full'
    unresolved['mask'] = []
    unresolved['page'] = unit.get('page')
    return unresolved

def apply_context(job, payload):
    from .sharing_context import validate_context
    try:
        version = payload['version']
    except (KeyError, TypeError):
        raise StoreError(422, 'invalid_plan', '필수 필드 누락')
    check_version(job, version)
    context = validate_context(payload.get('context', {}), partial=True)
    if 'documentType' in payload:
        dt = payload['documentType']
        if dt is not None and dt not in DOCUMENT_TYPES:
            raise StoreError(422, 'invalid_plan', 'documentType 유효하지 않음')
    old_context = job.get('context', {})
    new_context = copy.deepcopy(old_context)
    for k in context:
        new_context[k] = context[k]
    job['context'] = new_context
    if 'documentType' in payload:
        if payload['documentType'] is None:
            job['documentType'] = None
            job.pop('documentTypeSource', None)
        else:
            if payload['documentType'] != job.get('documentType'):
                job['documentTypeSource'] = 'user'
            job['documentType'] = payload['documentType']
    return None

def assert_ready(job):
    from .processing_requirements import assert_upstage_complete
    assert_upstage_complete(job)
    from .full_redaction import assert_policy
    assert_policy(job)
    if job.get('metadataReviewed') is not True:
        raise StoreError(422, 'not_ready', '메타데이터 검토가 완료되지 않았습니다.')

    candidates = job.get('candidates', [])
    if not isinstance(candidates, list):
        raise StoreError(422, 'not_ready', '후보자 목록이 올바르지 않습니다.')

    if len(candidates) > 1000:
        raise StoreError(422, 'not_ready', '후보자 수가 제한을 초과했습니다.')

    if job.get('analysis', {}).get('incomplete') is True:
        raise StoreError(422, 'not_ready', '분석이 완료되지 않았습니다.')

    for m in job.get('metadata', []):
        if m.get('action') not in ('delete', 'keep'):
            raise StoreError(422, 'not_ready', '메타데이터 동작이 올바르지 않습니다.')

    for c in candidates:
        if c.get('confirmed') is not True:
            raise StoreError(422, 'not_ready', '후보자 확인이 완료되지 않았습니다.')
        if c.get('locationResolved') is not True:
            raise StoreError(422, 'not_ready', '후보자 위치 해결이 완료되지 않았습니다.')
        if c.get('type') not in TYPES:
            raise StoreError(422, 'not_ready', '후보자 유형이 올바르지 않습니다.')
        validate_decision(c)

        unit, start, end = _selected_range(job, c)
        if c.get('value') != unit['text'][start:end]:
            raise StoreError(422, 'not_ready', '후보자 값이 일치하지 않습니다.')

    from collections import defaultdict
    by_unit = defaultdict(list)
    for c in candidates:
        by_unit[c.get('unitId')].append(c)

    for unit_id, cands in by_unit.items():
        cands_sorted = sorted(cands, key=lambda x: x.get('start', 0))
        for i in range(len(cands_sorted) - 1):
            curr = cands_sorted[i]
            nxt = cands_sorted[i + 1]
            if curr.get('end', 0) > nxt.get('start', 0):
                raise StoreError(422, 'not_ready', '후보자 구간이 겹칩니다.')

    return None
