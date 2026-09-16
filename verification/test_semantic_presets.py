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
        # Presets mask exactly one contiguous range; separators are not leaked.
        assert len(ranges) == 1
        start, end = ranges[0]
        actual[name] = value[:start] + '█' * (end - start) + value[end:]
        assert 0 <= start < end <= len(value)
    assert actual == case['results']
    if case['type'] == 'address':
        ends = [ranges[0][0] for ranges in presets.values()]
        assert ends == sorted(set(ends))  # strictly more detailed nested disclosure
