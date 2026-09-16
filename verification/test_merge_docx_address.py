"""Grounded DOCX whitespace reconciliation and explicit non-value filtering."""
import copy
import json
from pathlib import Path

import pytest

from backend.analysis_merge import merge_extractions
from backend.engine import candidate_id


ADDRESS = '서울특별시 중구 가상로 123,\n가상아파트 101동 202호'
FLAT = ADDRESS.replace('\n', ' ')


def resolved(unit_id, value=ADDRESS, typ='address', **changes):
    candidate = {'id': candidate_id(typ, unit_id, 0, len(value)), 'type': typ,
                 'value': value, 'unitId': unit_id, 'start': 0, 'end': len(value),
                 'page': None, 'locationResolved': True, 'source': 'local_label',
                 'method': 'partial', 'mask': [[10, 15]], 'confirmed': True,
                 'alternativeTypes': []}
    return {**candidate, **changes}


def address_job(values=(ADDRESS,), fmt='docx'):
    units = [{'id': f'u{index}', 'text': value, 'page': None} for index, value in enumerate(values)]
    return {'format': fmt, '_inspection': {'format': fmt, 'units': units},
            'candidates': [resolved(unit['id'], unit['text']) for unit in units],
            'analysis': {'warnings': []}}


def result(value=FLAT, typ='address', role='', context=''):
    return {'extracted': {typ: [{'raw_value': value, 'role_raw': role, 'context_raw': context}]}}


def assert_choices_unchanged(before, after):
    keys = ('id', 'type', 'unitId', 'start', 'end', 'value', 'method', 'mask', 'confirmed', 'locationResolved')
    assert {key: before.get(key) for key in keys} == {key: after.get(key) for key in keys}


def test_whitespace_variants_attach_to_exact_known_addresses_without_changing_choices():
    job = address_job((ADDRESS, ADDRESS))
    job['candidates'][1].update(method='keep', mask=[])
    before = copy.deepcopy(job['candidates'])
    merge_extractions(job, result(context='not present'))
    merge_extractions(job, result(context='not present'))
    assert len(job['candidates']) == 2
    for old, candidate in zip(before, job['candidates']):
        assert_choices_unchanged(old, candidate)
        assert candidate['evidenceSources'] == ['upstage']
        assert candidate['extractionVariants'] == [FLAT]
        assert 'context_raw' not in candidate


@pytest.mark.parametrize('variant', [ADDRESS.replace('\n', '\t'), ADDRESS.replace('\n', '\r\n'), '  '+FLAT+'\u00a0'])
def test_only_whitespace_run_variations_are_normalized(variant):
    job = address_job()
    merge_extractions(job, result(variant))
    assert len(job['candidates']) == 1
    assert job['candidates'][0]['extractionVariants'] == [variant]


@pytest.mark.parametrize('fault', ['no_existing', 'wrong_type', 'stale_range', 'stale_text', 'punctuation', 'digits', 'letters', 'joined_words', 'pdf'])
def test_no_guessed_coordinates_or_non_whitespace_repair(fault):
    job = address_job()
    extracted = FLAT
    if fault == 'no_existing': job['candidates'] = []
    if fault == 'wrong_type': job['candidates'][0]['type'] = 'name'
    if fault == 'stale_range': job['candidates'][0]['start'] += 1
    if fault == 'stale_text': job['_inspection']['units'][0]['text'] = ADDRESS.replace('123', '124')
    if fault == 'punctuation': extracted = FLAT.replace(',', '')
    if fault == 'digits': extracted = FLAT.replace('123', '124')
    if fault == 'letters': extracted = FLAT.replace('가상로', '실재로')
    if fault == 'joined_words': extracted = FLAT.replace(' ', '')
    if fault == 'pdf': job['format'] = job['_inspection']['format'] = 'pdf'
    before = copy.deepcopy(job['candidates'])
    merge_extractions(job, result(extracted))
    assert len(job['candidates']) == len(before) + 1
    assert not job['candidates'][-1]['locationResolved']
    assert job['candidates'][-1]['unitId'] is None
    for old, candidate in zip(before, job['candidates']):
        assert_choices_unchanged(old, candidate)
        assert 'extractionVariants' not in candidate


def test_distinct_original_strings_with_same_normalization_remain_ambiguous():
    job = address_job((ADDRESS, ADDRESS.replace('\n', '\t')))
    merge_extractions(job, result())
    assert len(job['candidates']) == 3
    assert not job['candidates'][-1]['locationResolved']
    assert all('extractionVariants' not in candidate for candidate in job['candidates'])


def test_existing_literal_match_has_priority_over_whitespace_reconciliation():
    job = address_job((ADDRESS, FLAT))
    merge_extractions(job, result())
    assert len(job['candidates']) == 2
    assert 'evidenceSources' not in job['candidates'][0]
    assert job['candidates'][1]['evidenceSources'] == ['upstage']


def unresolved(id_, value=FLAT, typ='address', **changes):
    return {'id': id_, 'type': typ, 'value': value, 'unitId': None, 'start': None,
            'end': None, 'locationResolved': False, 'source': 'upstage',
            'confirmed': False, 'proposedLocation': [], **changes}


def test_repair_of_previous_unresolved_candidates_preserves_user_and_overlap_conflicts():
    job = address_job()
    job['candidates'] += [unresolved('old'), unresolved('manual', source='manual'),
                          unresolved('confirmed', confirmed=True),
                          unresolved('chosen', decisionSource='user'),
                          unresolved('overlap', proposedLocation=[{'unitId': 'u0'}])]
    merge_extractions(job, {})
    assert [candidate['id'] for candidate in job['candidates'][1:]] == ['manual', 'confirmed', 'chosen', 'overlap']
    assert job['candidates'][0]['extractionVariants'] == [FLAT]
    assert job['candidates'][0]['evidenceSources'] == ['upstage']


@pytest.mark.parametrize('typ', ['resident_id', 'foreign_id', 'passport', 'driver_license', 'phone', 'account', 'card', 'dob', 'management_id'])
@pytest.mark.parametrize('marker', ['해당 없음', '없음', '미제출', '미기재', '미발급', 'N/A', ' n/a ', '-'])
def test_explicit_absence_in_numbered_fields_is_not_a_candidate(typ, marker):
    job = address_job(())
    job['_inspection']['units'] = [{'id': 'u0', 'text': marker, 'page': None}]
    merge_extractions(job, result(marker, typ))
    assert job['candidates'] == []
    assert job['analysis']['omittedNonValues'] == {'extractedItems': 1, 'removedCandidates': 0}


@pytest.mark.parametrize('typ', ['name', 'address', 'email'])
def test_non_value_filter_is_not_applied_to_other_types(typ):
    job = address_job(())
    job['_inspection']['units'] = [{'id': 'u0', 'text': '없음', 'page': None}]
    merge_extractions(job, result('없음', typ))
    assert len(job['candidates']) == 1
    assert job['analysis']['omittedNonValues']['extractedItems'] == 0


@pytest.mark.parametrize('value', ['해당 없음123', '미제출-001', 'N/A001', 'AB-없음-CD', '--', '0'])
def test_non_value_filter_never_matches_substrings_or_similar_values(value):
    job = address_job(())
    job['_inspection']['units'] = [{'id': 'u0', 'text': value, 'page': None}]
    merge_extractions(job, result(value, 'management_id'))
    assert len(job['candidates']) == 1
    assert job['analysis']['omittedNonValues']['extractedItems'] == 0


def test_old_non_values_are_removed_only_for_unconfirmed_upstage_candidates():
    job = address_job(())
    job['candidates'] = [unresolved('old', '미제출', 'driver_license'),
                         unresolved('manual', '미제출', 'driver_license', source='manual'),
                         unresolved('confirmed', '미제출', 'driver_license', confirmed=True),
                         unresolved('chosen', '미제출', 'driver_license', decisionSource='user'),
                         unresolved('free-text', '미제출', 'name'),
                         resolved('u0', '해당 없음', 'resident_id', source='upstage', confirmed=False)]
    job['_inspection']['units'] = [{'id': 'u0', 'text': '해당 없음', 'page': None}]
    merge_extractions(job, {})
    assert [candidate['id'] for candidate in job['candidates']] == ['manual', 'confirmed', 'chosen', 'free-text']
    assert job['analysis']['omittedNonValues'] == {'extractedItems': 0, 'removedCandidates': 2}


def test_private_live_docx_snapshot_can_be_repaired_without_external_requests():
    path = Path(__file__).parent/'upstage-required/docx-analysis.private.json'
    if not path.is_file():
        pytest.skip('Local live-analysis snapshot is unavailable')
    job = json.loads(path.read_text())
    job['_inspection'] = {'format': 'docx', 'units': job['units']}
    before = copy.deepcopy(job['candidates'])
    previous_addresses = [candidate for candidate in before if candidate['type'] == 'address' and not candidate['locationResolved']]
    if not previous_addresses:
        pytest.skip('The private live snapshot has already been reconciled; synthetic cases cover regression')
    merge_extractions(job, {'extracted': {'address': [{'raw_value': candidate['value']} for candidate in previous_addresses]}})
    assert len(job['candidates']) == 37
    assert all(candidate['locationResolved'] for candidate in job['candidates'])
    assert len(previous_addresses) == 2
    assert job['analysis']['omittedNonValues']['removedCandidates'] == 3
    prior = {candidate['id']: candidate for candidate in before}
    for candidate in job['candidates']:
        assert_choices_unchanged(prior[candidate['id']], candidate)
        if candidate['type'] == 'address':
            assert len(candidate['extractionVariants']) == 1
            assert 'upstage' in candidate['evidenceSources']
