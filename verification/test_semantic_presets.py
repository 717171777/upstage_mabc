"""Public disclosure examples shared with the browser; no supplier calls."""
import json
from pathlib import Path
import pytest
from backend.masking_policy import recommended_presets

CASES = json.loads(Path(__file__).with_name('semantic_preset_cases.json').read_text())

@pytest.mark.parametrize('case', CASES, ids=[f"{i}-{c['type']}" for i,c in enumerate(CASES)])
def test_disclosure_contract(case):
    presets = recommended_presets(case)
    actual = {}
    for name, ranges in presets.items():
        value = case['value']
        # 프리셋은 한 개의 연속 구간을 가린다. 구분자 위치가 형식으로 고정된
        # 번호(카드 4-4-4-4, 주민·외국인등록번호 6-7)에 한해, 구분자를 남기는
        # 국내 관행 표기를 위해 구간을 나눌 수 있다. 이 경우에도 구분자만
        # 드러나며 값은 드러나지 않는다.
        # 구간을 나누는 것은 구분자 위치가 형식으로 고정된 번호에만 허용한다.
        # 실제 노출 내용은 아래 계약 문자열 대조로 확인한다.
        if len(ranges) > 1:
            assert case['type'] in ('card', 'resident_id', 'foreign_id'), name
            assert ranges == sorted(ranges), name
            for (_, prev_end), (next_start, _) in zip(ranges, ranges[1:]):
                assert prev_end < next_start, name
        for start, end in ranges:
            assert 0 <= start < end <= len(value)
        cps = list(value)
        for start, end in ranges:
            cps[start:end] = ['*'] * (end - start)
        actual[name] = ''.join(cps)
    assert actual == case['results']
    if case['type'] == 'address':
        ends = [ranges[0][0] for ranges in presets.values()]
        assert ends == sorted(set(ends))  # strictly more detailed nested disclosure
