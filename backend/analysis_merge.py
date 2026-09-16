import copy
import hashlib
import json
import re

from .engine import TYPES, candidate_id, locate_value
from .pdf_multiline import locate_multiline_address


_NON_VALUE_TYPES = frozenset({
    'resident_id', 'foreign_id', 'passport', 'driver_license', 'phone',
    'account', 'card', 'dob', 'management_id',
})
_NON_VALUES = frozenset({'해당없음', '없음', '미제출', '미기재', '미발급', 'n/a', '-'})


def _is_explicit_non_value(pii_type, value):
    """Only whole, explicit absence markers in numbered/date fields are omitted."""
    return (pii_type in _NON_VALUE_TYPES and isinstance(value, str)
            and re.sub(r'\s+', '', value).casefold() in _NON_VALUES)


def _record_extraction_variant(candidate, value):
    sources = candidate.setdefault('evidenceSources', [])
    if 'upstage' not in sources:
        sources.append('upstage')
    variants = candidate.setdefault('extractionVariants', [])
    if value not in variants:
        variants.append(value)


def _known_docx_address_locations(inspection, candidates, extracted_value):
    """Match whitespace variants only to one already grounded literal address.

    A space is retained between tokens: digit/word boundaries are never joined.
    Repetitions of exactly the same source address may share evidence, but distinct
    source strings with the same normalized representation remain ambiguous.
    """
    if inspection.get('format') != 'docx' or not isinstance(extracted_value, str):
        return []
    normalized = re.sub(r'\s+', ' ', extracted_value).strip()
    if not normalized:
        return []
    texts = {unit['id']: unit['text'] for unit in inspection.get('units', [])}
    found = []
    for candidate in candidates:
        start, end = candidate.get('start'), candidate.get('end')
        value = candidate.get('value')
        text = texts.get(candidate.get('unitId'), '')
        if (candidate.get('type') == 'address' and candidate.get('locationResolved') is True
                and isinstance(value, str) and value != extracted_value
                and type(start) is int and type(end) is int
                and 0 <= start < end <= len(text) and text[start:end] == value
                and re.sub(r'\s+', ' ', value).strip() == normalized):
            found.append(candidate)
    return found if len({candidate['value'] for candidate in found}) == 1 else []


def _known_email_locations(inspection, candidates, extracted_value):
    """Reconcile extraction line wrapping only with already verified literal emails."""
    if not isinstance(extracted_value, str):
        return []
    normalized = re.sub(r'[\t\n\r\f\v ]+', '', extracted_value)
    if normalized == extracted_value or not re.fullmatch(
        r"[A-Za-z0-9.!#$%&'*+/=?^_`{|}~-]+@[A-Za-z0-9-]+(?:\.[A-Za-z0-9-]+)+", normalized
    ):
        return []
    texts = {u['id']: u['text'] for u in inspection.get('units', [])}
    found = []
    for c in candidates:
        start, end = c.get('start'), c.get('end')
        text = texts.get(c.get('unitId'), '')
        if (c.get('type') == 'email' and c.get('locationResolved')
                and c.get('value') == normalized
                and type(start) is int and type(end) is int
                and 0 <= start < end <= len(text) and text[start:end] == normalized):
            found.append(c)
    return found


def merge_extractions(job, result):
    """
    job['candidates']와 job['analysis']를 수정합니다.
    외부 호출 없이 기존 규칙 후보와 문서 추출 결과를 합칩니다.
    """
    # --- 분석 컨텍스트 초기화 ---
    if 'analysis' not in job:
        job['analysis'] = {}
    if 'warnings' not in job['analysis']:
        job['analysis']['warnings'] = []
    if 'incomplete' not in job['analysis']:
        job['analysis']['incomplete'] = False

    candidates = job.get('candidates', [])
    units = job.get('_inspection', {}).get('units', [])
    fmt = job.get('format', 'pdf')
    extracted = result.get('extracted', {})
    elements = result.get('elements', [])
    additional = result.get('additional', {})

    MAX_CANDIDATES = 1000

    # Audit counts describe this merge only; no omitted document values are logged.
    omitted = {'extractedItems': 0, 'removedCandidates': 0}
    job['analysis']['omittedNonValues'] = omitted
    retained = []
    for candidate in candidates:
        if (candidate.get('source') == 'upstage' and not candidate.get('confirmed')
                and candidate.get('decisionSource') != 'user'
                and _is_explicit_non_value(candidate.get('type'), candidate.get('value'))):
            omitted['removedCandidates'] += 1
        else:
            retained.append(candidate)
    candidates[:] = retained

    # A retry can repair old extraction-only address duplicates without resending
    # the file or changing a previously chosen mask, source value or locator.
    if fmt == 'docx':
        retained = []
        for candidate in candidates:
            known = []
            if (candidate.get('source') == 'upstage' and candidate.get('type') == 'address'
                    and not candidate.get('confirmed') and candidate.get('decisionSource') != 'user'
                    and not candidate.get('locationResolved') and not candidate.get('proposedLocation')
                    and not locate_value(job.get('_inspection', {}), candidate.get('value', ''))):
                known = _known_docx_address_locations(job.get('_inspection', {}), candidates, candidate.get('value'))
            if known:
                for existing in known:
                    _record_extraction_variant(existing, candidate['value'])
            else:
                retained.append(candidate)
        candidates[:] = retained

    # A repeated analysis can repair an earlier extraction-only whitespace mismatch.
    # Confirmed/manual candidates and literal matches are never removed here.
    candidates[:] = [c for c in candidates if not (
        c.get('source') == 'upstage' and c.get('type') == 'email'
        and not c.get('confirmed') and not c.get('locationResolved')
        and not c.get('proposedLocation')
        and not locate_value(job.get('_inspection', {}), c.get('value', ''))
        and _known_email_locations(job.get('_inspection', {}), candidates, c.get('value'))
    )]

    # --- 기존 후보 범위 인덱스 (unitId, start, end) → 후보 인덱스 목록 (type 무관) ---
    existing_range_index = {}
    for idx, c in enumerate(candidates):
        key = (c.get('unitId'), c.get('start'), c.get('end'))
        if key not in existing_range_index:
            existing_range_index[key] = []
        existing_range_index[key].append(idx)

    # --- seen_ids: 기존 모든 candidate id + 새로 추가되는 id 추적 (unresolved도 공유) ---
    seen_ids = set(c.get('id') for c in candidates if c.get('id'))

    # --- 단위 텍스트 사전 ---
    unit_text_map = {u['id']: u['text'] for u in units}
    linked_matches = {}

    # --- 헬퍼 ---
    def _sha256_prefix(payload_dict):
        s = json.dumps(payload_dict, sort_keys=True, ensure_ascii=False)
        return 'u_' + hashlib.sha256(s.encode('utf-8')).hexdigest()[:24]

    def _ranges_overlap(s1, e1, s2, e2):
        return s1 < e2 and s2 < e1

    def _is_strictly_inside(s_new, e_new, s_exist, e_exist):
        return s_exist <= s_new and e_new <= e_exist

    def _extends_outside(s_new, e_new, s_exist, e_exist):
        return s_new < s_exist or e_new > e_exist

    def _add_source_evidence(candidate, source_label='upstage'):
        if 'evidenceSources' not in candidate:
            candidate['evidenceSources'] = []
        if source_label not in candidate['evidenceSources']:
            candidate['evidenceSources'].append(source_label)

    def _add_alternative_type(candidate, pii_type):
        if pii_type == candidate['type']:
            return
        if 'alternativeTypes' not in candidate:
            candidate['alternativeTypes'] = []
        if pii_type not in candidate['alternativeTypes']:
            candidate['alternativeTypes'].append(pii_type)

    def _make_unresolved_id(type_, value, context, proposed_location):
        uid = _sha256_prefix({
            'type': type_,
            'value': value,
            'context': context,
            'proposedLocation': proposed_location,
        })
        return uid

    def _try_append_candidate(candidate):
        """후보를 candidates에 즉시 추가하고 인덱스/seen_ids 갱신. 최대 1000 검사."""
        if len(candidates) >= MAX_CANDIDATES:
            job['analysis']['incomplete'] = True
            job['analysis']['warnings'].append(
                f'후보 최대 {MAX_CANDIDATES}개 초과로 인해 새 후보 추가가 중단되었습니다. '
                f'기존 후보는 보존되며 완료로 간주되지 않습니다.'
            )
            return False
        candidates.append(candidate)
        cid = candidate.get('id')
        if cid:
            seen_ids.add(cid)
        # 범위 인덱스 즉시 갱신
        key = (candidate.get('unitId'), candidate.get('start'), candidate.get('end'))
        if key not in existing_range_index:
            existing_range_index[key] = []
        existing_range_index[key].append(len(candidates) - 1)
        return True

    def _attach_evidence(candidate, unitId, role_raw, context_raw):
        """현재 match unit text를 참조하여 role_raw/context_raw이 실제 substring일 때만 부착."""
        if unitId is None:
            return
        unit_text = unit_text_map.get(unitId, '')
        if not unit_text:
            return
        if role_raw and role_raw in unit_text:
            candidate['role_raw'] = role_raw
        if context_raw and context_raw in unit_text:
            candidate['context_raw'] = context_raw

    def _attach_link(candidate, match):
        # Links describe exact fragments, never replace a larger existing range
        # or copy an address relationship onto a candidate of another type.
        if (match.get('linkGroup') and candidate.get('type') == 'address'
                and candidate.get('value') == match.get('value')
                and candidate.get('unitId') == match.get('unitId')
                and candidate.get('start') == match.get('start')
                and candidate.get('end') == match.get('end')):
            for key in ('linkedValue', 'linkGroup', 'linkIndex', 'linkCount'):
                candidate[key] = match[key]

    # --- extracted 순회 ---
    for pii_type in TYPES:
        items = extracted.get(pii_type, [])
        if not items:
            continue

        for item in items:
            raw_value = item.get('raw_value', '')
            role_raw = item.get('role_raw', '')
            context_raw = item.get('context_raw', '')

            if _is_explicit_non_value(pii_type, raw_value):
                omitted['extractedItems'] += 1
                continue

            # locate_value: 정확한 문자열 일치 위치만
            matches = locate_value(job.get('_inspection', {}), raw_value)

            if not matches and pii_type == 'address' and fmt == 'pdf':
                matches = locate_multiline_address(job.get('_inspection', {}), raw_value)
                if matches:
                    linked_matches[raw_value] = matches

            if not matches and pii_type == 'email':
                known = _known_email_locations(job.get('_inspection', {}), candidates, raw_value)
                if known:
                    for candidate in known:
                        _add_source_evidence(candidate)
                        _attach_evidence(candidate, candidate['unitId'], role_raw, context_raw)
                        values = candidate.setdefault('extractionVariants', [])
                        if raw_value not in values:
                            values.append(raw_value)
                    continue

            if not matches and pii_type == 'address' and fmt == 'docx':
                known = _known_docx_address_locations(job.get('_inspection', {}), candidates, raw_value)
                if known:
                    for candidate in known:
                        _record_extraction_variant(candidate, raw_value)
                        _attach_evidence(candidate, candidate['unitId'], role_raw, context_raw)
                    continue

            if not matches:
                # 일치 없음 → 미해결 후보
                proposed_location = []
                uid = _make_unresolved_id(pii_type, raw_value, context_raw, proposed_location)
                if uid in seen_ids:
                    continue
                seen_ids.add(uid)

                c = {
                    'id': uid,
                    'type': pii_type,
                    'value': raw_value,
                    'unitId': None,
                    'start': None,
                    'end': None,
                    'page': None,
                    'method': 'full',
                    'mask': [],
                    'confirmed': False,
                    'locationResolved': False,
                    'source': 'upstage',
                    'alternativeTypes': [],
                    'proposedLocation': proposed_location,
                    'reason': '추출한 값의 원문 위치를 확인해 주세요.',
                }
                _try_append_candidate(c)
                continue

            # 일치 위치마다 처리
            for match in matches:
                unitId = match['unitId']
                start = match['start']
                end = match['end']
                page = match['page']
                match_value = match.get('value', raw_value)

                # 1) 기존 후보 중 같은 (unitId, start, end) 범위가 있는지 (type 무관)
                exact_key = (unitId, start, end)
                if exact_key in existing_range_index:
                    for exist_idx in existing_range_index[exact_key]:
                        exist_c = candidates[exist_idx]
                        if exist_c.get('type') == pii_type:
                            # 같은 type이면 이미 존재 → source/근거만 추가
                            _add_source_evidence(exist_c)
                            _attach_evidence(exist_c, unitId, role_raw, context_raw)
                            _attach_link(exist_c, match)
                            continue
                        else:
                            # 다른 type → alternativeTypes 추가 (자기 자신 type 추가 금지)
                            _add_alternative_type(exist_c, pii_type)
                            _add_source_evidence(exist_c)
                            _attach_evidence(exist_c, unitId, role_raw, context_raw)
                    continue

                # 2) 기존 후보 중 같은 unitId에서 겹치는 범위가 있는지
                overlap_handled = False
                for exist_c in candidates:
                    e_unit = exist_c.get('unitId')
                    e_start = exist_c.get('start')
                    e_end = exist_c.get('end')
                    if e_unit != unitId or e_start is None or e_end is None:
                        continue
                    if not _ranges_overlap(start, end, e_start, e_end):
                        continue

                    if _is_strictly_inside(start, end, e_start, e_end):
                        # 새 범위가 기존 안에 완전히 들어감 → 유형/근거만 추가
                        _add_alternative_type(exist_c, pii_type)
                        _add_source_evidence(exist_c)
                        _attach_evidence(exist_c, unitId, role_raw, context_raw)
                        overlap_handled = True
                        break

                    if _extends_outside(start, end, e_start, e_end):
                        # 새 범위가 기존 밖으로 뻗음 → 별도 미해결 후보
                        proposed_location = [{
                            'unitId': unitId,
                            'start': start,
                            'end': end,
                            'page': page,
                        }]
                        uid = _make_unresolved_id(pii_type, match_value, context_raw, proposed_location)
                        if uid in seen_ids:
                            overlap_handled = True
                            break
                        seen_ids.add(uid)

                        c = {
                            'id': uid,
                            'type': pii_type,
                            'value': match_value,
                            'unitId': None,
                            'start': None,
                            'end': None,
                            'page': None,
                            'method': 'full',
                            'mask': [],
                            'confirmed': False,
                            'locationResolved': False,
                            'source': 'upstage',
                            'alternativeTypes': [],
                            'proposedLocation': proposed_location,
                            'reason': '겹친 범위가 달라 위치 확인이 필요합니다.',
                        }
                        _try_append_candidate(c)
                        overlap_handled = True
                        break

                    # 부분 겹침이지만 새 범위가 밖으로 뻗지 않는 경우
                    overlap_handled = True
                    break

                if overlap_handled:
                    continue

                # 3) 겹치는 기존 후보가 없으면 새 후보 생성
                new_id = candidate_id(pii_type, unitId, start, end)
                if new_id in seen_ids:
                    continue
                seen_ids.add(new_id)

                c = {
                    'id': new_id,
                    'type': pii_type,
                    'value': match_value,
                    'unitId': unitId,
                    'start': start,
                    'end': end,
                    'page': page,
                    'method': 'full',
                    'mask': [],
                    'confirmed': False,
                    'locationResolved': True,
                    'source': 'upstage',
                    'alternativeTypes': [],
                }
                _attach_evidence(c, unitId, role_raw, context_raw)
                _attach_link(c, match)
                _try_append_candidate(c)

    # Remove an older extraction-only unresolved full address only after every
    # verified line now has an exact resolved address candidate. Manual choices,
    # overlap conflicts, missing segments and capped additions remain visible.
    repaired = set()
    for value, matches in linked_matches.items():
        if all(any(c.get('type') == 'address' and c.get('locationResolved')
                   and all(c.get(key) == m[key] for key in ('unitId', 'start', 'end', 'value'))
                   for c in candidates) for m in matches):
            repaired.add(value)
    candidates[:] = [c for c in candidates if not (
        c.get('source') == 'upstage' and c.get('type') == 'address'
        and not c.get('confirmed') and not c.get('locationResolved')
        and not c.get('proposedLocation') and c.get('value') in repaired
    )]

    # --- structureEvidence (DOCX/PDF 모두; PDF는 page 일치 추가 조건) ---
    for c in candidates:
        if not c.get('locationResolved'):
            continue
        c_page = c.get('page')
        c_value = c.get('value', '')
        if not c_value:
            continue
        sev = []
        for elem in elements:
            elem_text = elem.get('text', '')
            if c_value not in elem_text:
                continue
            if fmt == 'pdf':
                # PDF: candidate page == element page
                if c_page is None or elem.get('page') != c_page:
                    continue
            # DOCX: page 조건 없이 value in element.text면 추가
            sev.append({
                'id': elem.get('id'),
                'page': elem.get('page'),
                'category': elem.get('category', ''),
            })
            if len(sev) >= 3:
                break
        if sev:
            c['structureEvidence'] = sev

    # --- extractionEvidence (additional의 raw_value가 dict일 때만 확인) ---
    for c in candidates:
        c_value = c.get('value', '')
        c_type = c.get('type')
        if not c_value or not c_type:
            continue
        add_list = additional.get(c_type, [])
        for add_item in add_list:
            raw_value_data = add_item.get('raw_value')
            if not isinstance(raw_value_data, dict):
                continue
            if raw_value_data.get('_value') == c_value:
                c['extractionEvidence'] = {
                    '_value': raw_value_data.get('_value'),
                    'confidence': raw_value_data.get('confidence', 'low'),
                    'page': raw_value_data.get('page'),
                    'coordinates': raw_value_data.get('coordinates'),
                }
                break

    # --- 최종 반영 ---
    job['candidates'] = candidates
