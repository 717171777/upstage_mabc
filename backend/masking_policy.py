"""Deterministic, end-exclusive presets available to the Claude recommendation validator."""
import re
import unicodedata
from datetime import date


def recommended_presets(candidate):
    value, kind = candidate.get('value'), candidate.get('type')
    if not isinstance(value, str) or not value or len(value) > 1000:
        return {}
    if any(unicodedata.category(c).startswith('M') or unicodedata.category(c) == 'Cs'
           or ord(c) == 0x200D or 0xFE00 <= ord(c) <= 0xFE0F
           or 0xE0100 <= ord(c) <= 0xE01EF or 0x1F1E6 <= ord(c) <= 0x1F1FF for c in value):
        return {}
    result = {}
    if kind == 'phone':
        digits = re.sub(r'[^0-9]', '', value)
        if re.fullmatch(r'\+?[0-9 ()-]+', value) and 9 <= len(digits) <= 15:
            result = {f'phone_suffix{n}': _suffix(value, n) for n in (2, 4)}
            result.update(_phone_groups(value))
    elif kind == 'email' and value.count('@') == 1 and not any(c.isspace() for c in value):
        at = value.index('@')
        # Keep shared UI/server offsets at simple codepoint boundaries. Complex
        # graphemes require direct selection instead of an automatic preset.
        complex_email = any(
            unicodedata.category(c).startswith('C')
            or 0x1100 <= ord(c) <= 0x11FF or 0xA960 <= ord(c) <= 0xA97F
            or 0xD7B0 <= ord(c) <= 0xD7FF or 0x1F3FB <= ord(c) <= 0x1F3FF
            or ord(c) in (0x0D4E, 0x1193F, 0x11941, 0x11A3A, 0x11D46, 0x11F02)
            or 0x111C2 <= ord(c) <= 0x111C3 or 0x11A84 <= ord(c) <= 0x11A89
            for c in value
        )
        if 0 < at < len(value)-1 and not complex_email:
            if at > 1:
                result['email_first_and_domain'] = [[1, at]]
            if at > 2:
                result['email_keep2'] = [[2, at]]
            result['email_local_part'] = [[0, at]]
    elif kind in ('resident_id', 'foreign_id'):
        result = _resident(value)
    elif kind == 'address':
        result = _address(value)
    elif kind == 'dob':
        match = (re.fullmatch(r'([0-9]{4})([-./])([0-9]{1,2})\2([0-9]{1,2})', value)
                 or re.fullmatch(r'([0-9]{4})(년\s*)([0-9]{1,2})월\s*([0-9]{1,2})일', value)
                 or re.fullmatch(r'([0-9]{4})()([0-9]{2})([0-9]{2})', value))
        if match:
            try:
                date(int(match[1]), int(match[3]), int(match[4]))
                result = {'dob_year_only': [[5 if value[4] == '년' else 4, len(value)]]}
            except ValueError:
                pass
    elif kind in ('account', 'card', 'management_id') and re.fullmatch(r'[A-Za-z0-9 ._/-]+', value):
        result = {f'id_suffix{n}': _suffix(value, n) for n in (2, 4)}
        if kind == 'card':
            result.update(_card_isms(value))
    elif kind == 'name' and re.fullmatch(r'[가-힣]{2,4}', value):
        result = {'name_initial': [[1, len(value)]]}
        if len(value) > 2:
            # ISMS-P 관행: 첫 글자와 끝 글자만 남기고 가운데를 가린다.
            result['name_middle'] = [[1, len(value) - 1]]
    return {key: ranges for key, ranges in result.items()
            if 0 < sum(b-a for a, b in ranges) < len(value)
            and all(0 <= a < b <= len(value) for a, b in ranges)}


def _suffix(value, count):
    indices = [m.start() for m in re.finditer(r'[A-Za-z0-9]', value)]
    return [[0, indices[-count]]] if len(indices) > count else []


_REGIONS = set('서울 서울시 서울특별시 부산 부산시 부산광역시 대구 대구시 대구광역시 인천 인천시 인천광역시 광주 광주광역시 대전 대전시 대전광역시 울산 울산시 울산광역시 세종 세종시 세종특별자치시 경기 경기도 강원 강원도 강원특별자치도 충북 충청북도 충남 충청남도 전북 전라북도 전북특별자치도 전남 전라남도 경북 경상북도 경남 경상남도 제주 제주도 제주특별자치도'.split())


def _address(value):
    tokens = list(re.finditer(r'\S+', value))
    if not tokens or tokens[0].start() != 0 or tokens[0][0] not in _REGIONS:
        return {}
    result = {}

    def add(index, key):
        if index + 1 < len(tokens):
            result[key] = [[tokens[index].end(), len(value)]]

    add(0, 'address_region')
    i = 1
    if i < len(tokens) and re.fullmatch(r'[가-힣]+[시군구]', tokens[i][0]):
        add(i, 'address_city')
        i += 1
        if i < len(tokens) and re.search(r'[시군]$', tokens[i-1][0]) and re.fullmatch(r'[가-힣]+구', tokens[i][0]):
            add(i, 'address_district')
            i += 1
    if i < len(tokens) and re.fullmatch(r'[가-힣][가-힣0-9·]*[읍면동]', tokens[i][0]):
        add(i, 'address_locality')
    return result


def _digit_positions(value):
    return [m.start() for m in re.finditer(r'[0-9]', value)]


def _merge(positions):
    """숫자 위치 목록을 연속 구간으로 합친다. 구분자는 가리지 않는다."""
    ranges = []
    for p in positions:
        if ranges and ranges[-1][1] == p:
            ranges[-1][1] = p + 1
        else:
            ranges.append([p, p + 1])
    return ranges


def _phone_groups(value):
    """국번(가운데 묶음)만, 또는 끝 묶음만 가리는 국내 관행 표기."""
    parts = list(re.finditer(r'[0-9]+', value))
    if len(parts) != 3:
        return {}
    return {'phone_middle': [[parts[1].start(), parts[1].end()]],
            'phone_tail': [[parts[2].start(), parts[2].end()]]}


def _card_isms(value):
    """ISMS-P: 카드번호 7~12번째 숫자를 가린다 (1234-56**-****-3456)."""
    if not re.fullmatch(r'[0-9]{4}[- ]?[0-9]{4}[- ]?[0-9]{4}[- ]?[0-9]{4}', value):
        return {}
    positions = _digit_positions(value)
    return {'card_isms': _merge(positions[6:12])}


def _resident(value):
    """주민·외국인등록번호. 뒷자리 전체를 남기는 조합은 제공하지 않는다.

    뒷 7자리는 그 자체로 개인을 특정하므로, 노출하는 프리셋을 두지 않는다.
    """
    match = re.fullmatch(r'([0-9]{6})([- ]?)([0-9])([0-9]{6})', value)
    if not match:
        return {}
    positions = _digit_positions(value)
    gender = match.start(3)
    end = len(value)
    return {
        # 생년월일 + 성별만 남김 (금융·서류 제출 관행). 재식별 위험이 남는다.
        'rrn_birth_gender': [[gender + 1, end]],
        # 생년월일만 남김
        'rrn_birth_only': [[gender, end]],
        # 성별 한 자리만 남김
        'rrn_gender_only': _merge(positions[:6]) + [[gender + 1, end]],
        # 출생 연도 두 자리만 남김
        'rrn_year_only': _merge(positions[2:6]) + [[gender, end]],
    }


# 모델이 고를 수 있는 프리셋에서 제외하는 유형.
# 신분증 계열은 부분 노출만으로 재식별이 가능하고, 제출처 요구는 사람이
# 직접 확인해야 하므로 자동 판단 대상에서 뺀다. 사용자는 직접 고를 수 있다.
_MODEL_PRESET_EXCLUDED = frozenset({'resident_id', 'foreign_id', 'passport', 'driver_license'})


def model_selectable_presets(candidate):
    """모델에게 제시할 프리셋. 제외 유형은 항상 빈 dict."""
    if candidate.get('type') in _MODEL_PRESET_EXCLUDED:
        return {}
    return recommended_presets(candidate)
