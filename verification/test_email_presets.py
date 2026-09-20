"""The same cases also verify UI presets in test_masking.cjs; no API calls."""
import json
from pathlib import Path

import pytest

from backend.masking_policy import recommended_presets

CASES = json.loads((Path(__file__).with_name('email_preset_cases.json')).read_text())


@pytest.mark.parametrize('case', CASES, ids=[str(i) for i in range(len(CASES))])
def test_email_preset_ranges_match_ui_contract(case):
    result = recommended_presets({'type': 'email', 'value': case['value']})
    assert result.get('email_first_and_domain') == case['firstMask']
    assert result.get('email_local_part') == case['localMask']
    if case['firstMask']:
        assert next(iter(result)) == 'email_first_and_domain'
        start, end = case['firstMask'][0]
        value = case['value']
        assert value[:start] + '*' * (end - start) + value[end:] == case['firstResult']
    else:
        assert 'email_first_and_domain' not in result
