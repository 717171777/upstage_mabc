"""Local, label-grounded PII candidates. No network calls or model judgments.

These rules extend format detection for explicitly labelled fields. They do not
promise semantic recognition of arbitrary prose or decide whether a value should
be retained. Every returned range addresses the unchanged inspected unit text.
"""
from __future__ import annotations

from copy import deepcopy
from datetime import date
from pathlib import Path
import re

from .engine import candidate_id, rule_candidates
from .location_context import attach_location_context


_LABELS = {
    'name': ('이름', '성명', '성명(한글)', '성명(영문)', '예금주', '신청인', '신청자', '작성자', '담당자', '대표자', '수령인'),
    'address': ('주소', '자택주소', '거주지주소', '현주소', '주민등록주소', '배송주소'),
    'dob': ('생년월일', '출생일', '생일', '생년'),
    'passport': ('여권번호', 'passportno', 'passportnumber'),
    'driver_license': ('운전면허번호', '운전면허증번호'),
    'account': ('계좌번호', '정산계좌번호', '환급계좌번호', '입금계좌번호', '급여계좌번호'),
    'card': ('카드번호', '사용카드번호', '결제카드번호', '신용카드번호', '법인카드번호', '체크카드번호'),
    # Generic document/order/receipt numbers are deliberately not person IDs.
    'management_id': ('사번', '학번', '직원번호', '회원번호', '고객번호', '환자번호', '수험번호', '개인관리번호', '사원번호'),
}
_LABEL_TYPE = {label: typ for typ, labels in _LABELS.items() for label in labels}
_EMPTY = {'해당없음', '없음', '미발급', '미기재', '비공개', '작성중', '검토메모', '확인필요', '담당자', '성명', '이름'}
_DATE = re.compile(r'(?:(\d{4})[-./년]\s*(\d{1,2})[-./월]\s*(\d{1,2})\s*일?|(\d{4})(\d{2})(\d{2}))\.?')
_NUMERIC = re.compile(r'[0-9]+(?:[ \t-][0-9]+)*')
_ADDRESS_START = re.compile(r'^(?:\(\d{5}\)\s*)?(?:서울|부산|대구|인천|광주|대전|울산|세종|경기|강원|충청|충북|충남|전라|전북|전남|경상|경북|경남|제주)')
_ADDRESS_DETAIL = re.compile(r'\d+\s*(?:동|호|층|번지)(?:\b|\s|$)')
_NAME_SUFFIX = re.compile(r'(?:(?:님|씨))?(?:께서|에게는|에게|한테|으로|과|와|은|는|이|가|을|를|의|도)?(?=$|[\s.,，。:;!?()\[\]·/])')


def _label_type(text):
    key = re.sub(r'\s+', '', text).strip(':：.·*[]').lower()
    return _LABEL_TYPE.get(key)


def _trim(text, start=0, end=None):
    end = len(text) if end is None else end
    while start < end and text[start].isspace():
        start += 1
    while end > start and text[end - 1].isspace():
        end -= 1
    return start, end


def _valid_value(typ, value):
    if not value or len(value) > 300 or _label_type(value) or re.sub(r'\s+', '', value) in _EMPTY:
        return False
    if typ == 'name':
        return bool(re.fullmatch(r'[가-힣A-Za-z][가-힣A-Za-z\'-]{1,29}(?:[ \t][가-힣A-Za-z][가-힣A-Za-z\'-]{0,29}){0,3}', value)) and len(value) <= 80
    if typ == 'address':
        return len(value) >= 8 and bool(re.search(r'\d', value)) and bool(
            _ADDRESS_START.search(value) or re.search(r'\b(?:street|road|avenue|boulevard|lane)\b', value, re.I))
    if typ == 'dob':
        if re.fullmatch(r'\d{4}', value):
            return 1900 <= int(value) <= date.today().year
        match = _DATE.fullmatch(value)
        if not match:
            return False
        parts = match.groups()[:3] if match.group(1) else match.groups()[3:]
        try:
            birthday = date(*map(int, parts))
            return date(1900, 1, 1) <= birthday <= date.today()
        except ValueError:
            return False
    if typ == 'passport':
        return bool(re.fullmatch(r'(?=.*[0-9])[A-Z0-9]{6,12}', value))
    if typ == 'driver_license':
        return bool(re.fullmatch(r'(?:(?:[0-9]{2}|[가-힣]{2,4})[- ][0-9]{2}[- ][0-9]{6}[- ][0-9]{2}|[0-9]{12})', value))
    if typ in ('account', 'card'):
        if not _NUMERIC.fullmatch(value):
            return False
        length = len(re.sub(r'\D', '', value))
        return 8 <= length <= 18 if typ == 'account' else 13 <= length <= 19
    if typ == 'management_id':
        return bool(re.fullmatch(r'(?=.*[0-9])[A-Za-z0-9][A-Za-z0-9_-]{2,63}', value))
    return False


def _proof(unit, relation, start=0, end=None):
    start, end = _trim(unit['text'], start, end)
    return {'unitId': unit['id'], 'start': start, 'end': end,
            'text': unit['text'][start:end], 'relation': relation,
            'locator': deepcopy(unit.get('locator', {}))}


def _box(unit):
    box = unit.get('locator', {}).get('bbox')
    return box if isinstance(box, (list, tuple)) and len(box) == 4 else None


def _pdf_address_label(unit, units):
    """A two-line address cell can straddle its vertically centred row label."""
    box = _box(unit)
    if not box:
        return None
    x0, y0, _, y1 = box
    for label in units:
        other = _box(label)
        if label.get('page') != unit.get('page') or not other or _label_type(label['text']) != 'address':
            continue
        if other[2] < x0 and abs((other[1] + other[3] - y0 - y1) / 2) <= max(y1-y0, other[3]-other[1]):
            return _proof(label, 'same_row_label_multiline')
    return None


def detect_local(path: Path, inspection: dict) -> list[dict]:
    """Return unconfirmed, full-mask candidates with exact local field evidence."""
    units = inspection.get('units', [])
    unit_map = {unit['id']: unit for unit in units}
    candidates = rule_candidates(inspection)
    # The context helper performs structural parsing and local geometric lookup.
    probes = [{'unitId': unit['id'], 'start': 0, 'end': len(unit['text']),
               'value': unit['text'], 'locationResolved': True}
              for unit in units if unit.get('text')]
    attach_location_context(Path(path), inspection, probes)
    contexts = {probe['unitId']: probe.get('locationContext', {}) for probe in probes}

    def add(unit, typ, start, end, proof, *, repeated=False, linked=None):
        if not 0 <= start < end <= len(unit['text']):
            return None
        value = unit['text'][start:end]
        overlaps = [c for c in candidates if c['unitId'] == unit['id'] and c['start'] < end and start < c['end']]
        for old in overlaps:
            if old['type'] == typ and old['start'] == start and old['end'] == end:
                return old
        # A labelled account/card/license can disambiguate a phone-like shape,
        # but never replaces a resident ID, foreign ID, email or partial overlap.
        if overlaps and not (typ in ('account', 'card', 'driver_license') and all(
                old['type'] == 'phone' and start <= old['start'] and old['end'] <= end for old in overlaps)):
            return None
        for old in overlaps:
            candidates.remove(old)
        candidate = {
            'id': candidate_id(typ, unit['id'], start, end), 'type': typ,
            'value': value, 'unitId': unit['id'], 'start': start, 'end': end,
            'page': unit.get('page'), 'method': 'full', 'mask': [], 'confirmed': False,
            'locationResolved': True, 'source': 'local_label',
            'reason': f'「{proof["text"]}」 라벨에서 확인한 이름과 같은 값입니다. 위치별로 확인해 주세요.' if repeated else f'「{proof["text"]}」 라벨과 값의 위치를 로컬에서 확인했습니다. 처리 방법을 확인해 주세요.',
            'labelEvidence': deepcopy(proof), 'alternativeTypes': sorted({c['type'] for c in overlaps}),
        }
        if linked:
            candidate['localGroupId'] = linked
        candidates.append(candidate)
        return candidate

    for unit in units:
        text = unit.get('text', '')
        # Inline fields need an explicit colon; a prose mention of "생년월일"
        # or a heading such as "담당자 검토 메모" is not a labelled value.
        inline = list(re.finditer(r'(^|[\n;|\t])\s*([^:\n;|\t]{1,30})\s*[:：]', text))
        for index, match in enumerate(inline):
            typ = _label_type(match.group(2))
            if not typ:
                continue
            start, end = _trim(text, match.end(), inline[index+1].start() if index+1 < len(inline) else len(text))
            if _valid_value(typ, text[start:end]):
                add(unit, typ, start, end, _proof(unit, 'inline_label', match.start(2), match.end(2)))

        start, end = _trim(text)
        value = text[start:end]
        if not value:
            continue
        facts = contexts.get(unit['id'], {})
        for evidence in facts.get('evidence', []):
            relation = evidence['relation']
            if relation not in ('table_row_first_cell', 'table_first_row', 'same_cell', 'same_row_left', 'preceding_paragraph'):
                continue
            source = unit_map.get(evidence['unitId'])
            if not source or evidence['start'] != 0 or evidence['end'] != len(source['text']):
                continue
            typ = _label_type(source['text'])
            if typ and _valid_value(typ, value):
                add(unit, typ, start, end, _proof(source, relation))
        if inspection.get('format') == 'pdf' and _valid_value('address', value):
            evidence = _pdf_address_label(unit, units)
            if evidence:
                add(unit, 'address', start, end, evidence)

    # In text PDFs an address's detail line is a separate literal unit. Keep its
    # own coordinates and link it only to the immediately preceding aligned line.
    if inspection.get('format') == 'pdf':
        first_lines = [c for c in candidates if c['type'] == 'address']
        for first in first_lines:
            source = unit_map[first['unitId']]
            box = _box(source)
            if not box:
                continue
            below = [unit for unit in units if unit.get('page') == source.get('page') and _box(unit)
                     and abs(_box(unit)[0] - box[0]) <= 2
                     and 0 <= _box(unit)[1] - box[3] <= (box[3]-box[1])*1.5]
            if not below:
                continue
            continuation = min(below, key=lambda unit: _box(unit)[1])
            start, end = _trim(continuation['text'])
            value = continuation['text'][start:end]
            if not 4 <= len(value) <= 100 or not _ADDRESS_DETAIL.search(value) or _label_type(value):
                continue
            linked = add(continuation, 'address', start, end, first['labelEvidence'], linked=first['id'])
            if linked:
                first['localGroupId'] = first['id']
                linked['localGroupId'] = first['id']
                linked['reason'] = '주소 라벨에서 확인한 앞줄과 바로 이어지는 상세 주소입니다. 두 줄을 함께 확인해 주세요.'

    names = {}
    for candidate in candidates:
        if candidate['type'] == 'name' and candidate.get('labelEvidence'):
            names.setdefault(candidate['value'], candidate['labelEvidence'])
    for value, proof in names.items():
        for unit in units:
            text = unit.get('text', '')
            for match in re.finditer(re.escape(value), text):
                start, end = match.span()
                if start and (text[start-1].isalnum() or text[start-1] in "_-'@"):
                    continue
                suffix = text[end:]
                if suffix and not _NAME_SUFFIX.match(suffix):
                    continue
                add(unit, 'name', start, end, proof, repeated=True)

    attach_location_context(Path(path), inspection, candidates)
    for candidate in candidates:
        proof = candidate.get('labelEvidence')
        evidence = candidate.get('locationContext', {}).get('evidence', [])
        if proof and len(evidence) < 8 and not any(e['unitId'] == proof['unitId'] and e['text'] == proof['text'] for e in evidence):
            evidence.append(deepcopy(proof))
    order = {unit['id']: index for index, unit in enumerate(units)}
    return sorted(candidates, key=lambda candidate: (order[candidate['unitId']], candidate['start'], candidate['end']))
