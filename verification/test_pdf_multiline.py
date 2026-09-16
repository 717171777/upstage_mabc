"""Exact per-line mapping regressions; no API calls or inferred coordinates."""
from copy import deepcopy

import pytest

from backend.analysis_merge import merge_extractions
from backend.engine import candidate_id
from backend.pdf_multiline import locate_multiline_address


def unit(uid, text, y, x=100, page=1):
    return {'id': uid, 'text': text, 'page': page,
            'locator': {'page': page, 'bbox': [x, y, x + len(text) * 6, y + 10]},
            'chars': [{'c': c, 'bbox': [x + n * 6, y, x + (n + 1) * 6, y + 10]}
                      for n, c in enumerate(text)]}


def inspection(lines=None):
    return {'format': 'pdf', 'units': lines or [
        unit('a', '서울특별시 가상구 테스트로 123,', 100),
        unit('b', '샘플아파트 101동 202호', 117),
    ]}


VALUE = '서울특별시 가상구 테스트로 123, 샘플아파트 101동 202호'


def job(info=None, candidates=None):
    return {'format': 'pdf', '_inspection': info or inspection(),
            'candidates': candidates or [], 'analysis': {'warnings': []}}


def result(value=VALUE, pii='address'):
    return {'extracted': {pii: [{'raw_value': value, 'role_raw': '주소', 'context_raw': value}]}}


def test_two_lines_keep_exact_spans_and_repeat_without_losing_user_decision():
    j = job()
    merge_extractions(j, result())
    assert len(j['candidates']) == 2
    first, second = j['candidates']
    for index, (c, u) in enumerate(zip(j['candidates'], j['_inspection']['units']), 1):
        assert c['value'] == u['text'] and c['start'] == 0 and c['end'] == len(u['text'])
        assert c['unitId'] == u['id'] and c['locationResolved'] and not c['confirmed']
        assert c['method'] == 'full' and not c.get('context_raw') and not c.get('role_raw')
        assert c['linkedValue'] == VALUE and c['linkIndex'] == index and c['linkCount'] == 2
    assert first['linkGroup'] == second['linkGroup']
    first.update(method='partial', mask=[[0, 5]], confirmed=True)
    merge_extractions(j, result())
    assert len(j['candidates']) == 2 and first['method'] == 'partial' and first['mask'] == [[0, 5]] and first['confirmed']


def test_three_lines_and_whitespace_only_normalization():
    info = inspection([unit('a', '  서울시 가상구 ', 100), unit('b', '테스트로 123,', 115), unit('c', '101동 202호  ', 130)])
    matches = locate_multiline_address(info, '서울시\n가상구\t테스트로123,101동 202호')
    assert len(matches) == 3 and matches[0]['start'] == 2 and matches[-1]['end'] == len(info['units'][-1]['text']) - 2
    assert all(m['value'] == u['text'][m['start']:m['end']] for m, u in zip(matches, info['units']))
    assert locate_multiline_address(info, '서울시 가상구 테스트로 123 101동 202호') == []  # punctuation cannot disappear


@pytest.mark.parametrize('fault', ['far', 'unaligned', 'different_page', 'intervening', 'duplicate', 'bad_char', 'bad_box', 'four_lines'])
def test_ambiguous_or_noncontiguous_addresses_stay_unresolved(fault):
    info = inspection()
    if fault == 'far': info['units'][1] = unit('b', info['units'][1]['text'], 160)
    if fault == 'unaligned': info['units'][1] = unit('b', info['units'][1]['text'], 117, x=160)
    if fault == 'different_page': info['units'][1] = unit('b', info['units'][1]['text'], 117, page=2)
    if fault == 'intervening': info['units'].append(unit('middle', '다른 문장', 111))
    if fault == 'duplicate': info['units'] += [unit('c', info['units'][0]['text'], 200), unit('d', info['units'][1]['text'], 217)]
    if fault == 'bad_char': info['units'][1]['chars'][0]['c'] = 'X'
    if fault == 'bad_box': info['units'][1]['chars'][0]['bbox'][0] = float('nan')
    if fault == 'four_lines': info['units'] = [unit('a', '서울특별시', 100), unit('b', '가상구', 115), unit('c', '테스트로 123,', 130), unit('d', '샘플아파트 101동 202호', 145)]
    j = job(info)
    merge_extractions(j, result())
    assert len(j['candidates']) == 1 and not j['candidates'][0]['locationResolved']
    assert j['candidates'][0]['unitId'] is None and j['candidates'][0]['value'] == VALUE


def test_no_other_type_or_single_line_matching_changes():
    j = job(); merge_extractions(j, result(pii='name'))
    assert len(j['candidates']) == 1 and not j['candidates'][0]['locationResolved']
    j = job(inspection([unit('single', VALUE, 100)])); merge_extractions(j, result())
    assert len(j['candidates']) == 1 and j['candidates'][0]['locationResolved']
    assert not j['candidates'][0].get('linkGroup')


def test_old_unresolved_repaired_only_after_all_exact_fragments_exist():
    old = {'id': 'old', 'type': 'address', 'value': VALUE, 'locationResolved': False,
           'unitId': None, 'start': None, 'end': None, 'source': 'upstage', 'confirmed': False}
    j = job(candidates=[old, dict(old, id='manual', source='manual'), dict(old, id='confirmed', confirmed=True)])
    merge_extractions(j, result())
    assert len(j['candidates']) == 4
    assert 'old' not in {c['id'] for c in j['candidates']}
    assert {'manual', 'confirmed'} <= {c['id'] for c in j['candidates']}


def test_overlap_remains_unresolved_and_keeps_manual_decision():
    i = inspection(); u = i['units'][0]
    old = {'id': candidate_id('name', 'a', 0, 5), 'type': 'name', 'value': u['text'][:5],
           'unitId': 'a', 'start': 0, 'end': 5, 'page': 1, 'source': 'manual',
           'method': 'keep', 'mask': [], 'confirmed': True, 'locationResolved': True}
    j = job(i, [deepcopy(old)])
    merge_extractions(j, result())
    assert j['candidates'][0] == old
    unresolved = [c for c in j['candidates'] if not c['locationResolved']]
    assert len(unresolved) == 1 and unresolved[0]['value'] == u['text']
    assert unresolved[0]['proposedLocation'] == [{'unitId': 'a', 'start': 0, 'end': len(u['text']), 'page': 1}]
