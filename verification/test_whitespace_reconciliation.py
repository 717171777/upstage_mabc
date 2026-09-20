"""공백 한정 재조정(A)과 미해결 후보의 수동 위치 연결 경로 검증."""
import copy
import pytest

from backend import plans
from backend.analysis_merge import merge_extractions
from backend.engine import rule_candidates
from backend.store import StoreError


TEXT = '1982-08-01 010-1831-6586 STU-2020-0112'


def _job(extra_candidates=()):
    located = dict(id='c1', type='name', value='가상인', unitId='u1', start=0, end=3,
                   method='full', mask=[], confirmed=True, locationResolved=True)
    return dict(version=1, status='review', candidates=[located, *copy.deepcopy(list(extra_candidates))],
                metadata=[dict(id='meta1', action='delete')], metadataReviewed=True,
                context=dict(recipient=None, purpose='', keepInfo=None), documentType=None,
                analysis=dict(warnings=[]),
                _inspection=dict(units=[dict(id='u1', text='가상인 010-0000-1111', page=None)]))


def _unlocated(**changes):
    base = dict(id='c2', type='dob', value='1 9 8 2 - 0 8 - 0 1', unitId=None, start=None,
                end=None, method='full', mask=[], confirmed=False, locationResolved=False,
                source='upstage', proposedLocation=[])
    return {**base, **changes}


def test_unlocatable_blocks_render_until_manually_resolved():
    """미해결 후보는 render를 막고, 수동 위치 연결로만 풀린다."""
    text = '1982-08-01 010-1831-6586'
    job = dict(version=1, status='review',
               candidates=[dict(id='u1', type='dob', value='1 9 8 2 - 0 8 - 0 1',
                                unitId=None, start=None, end=None, method='full', mask=[],
                                confirmed=False, locationResolved=False, source='upstage',
                                proposedLocation=[])],
               metadata=[], metadataReviewed=True, analysis=dict(warnings=[]),
               context=dict(recipient=None, purpose='', keepInfo=None), documentType=None,
               _inspection=dict(units=[dict(id='u0', text=text, page=1)]))
    with pytest.raises(StoreError):
        plans.assert_ready(job)

    resolved = plans.resolve_candidate(
        job, dict(version=1, candidateId='u1', unitId='u0', start=0, end=10))
    assert resolved['locationResolved'] is True
    assert resolved['value'] == '1982-08-01'
    assert resolved['extractedValue'] == '1 9 8 2 - 0 8 - 0 1'
    assert resolved['source'] == 'manual_resolution'

    plans.apply_plan(job, dict(version=job['version'], candidates=[
        dict(id=resolved['id'], method='full', mask=[], confirmed=True)],
        metadataReviewed=True))
    plans.assert_ready(job)


# --- A: 공백 한정 재조정 ----------------------------------------------------

def _merge_job(text=TEXT, fmt='pdf'):
    insp = dict(format=fmt, units=[dict(id='u0', text=text, page=1)])
    return dict(format=fmt, _inspection=insp, units=insp['units'],
                candidates=rule_candidates(insp), analysis=dict(warnings=[]))


def _extract(pii_type, raw):
    return dict(extracted={pii_type: [dict(raw_value=raw, role_raw='', context_raw=TEXT)]},
                additional={}, elements=[])


def test_whitespace_split_phone_reconciles_to_grounded_candidate():
    job = _merge_job()
    merge_extractions(job, _extract('phone', '0 1 0 - 1 8 3 1 - 6 5 8 6'))
    phones = [c for c in job['candidates'] if c['type'] == 'phone']
    assert len(phones) == 1
    assert phones[0]['locationResolved'] is True
    assert phones[0]['value'] == '010-1831-6586'


def test_no_grounded_candidate_stays_unresolved():
    job = _merge_job()
    merge_extractions(job, _extract('management_id', 'S T U - 2 0 2 0 - 0 1 1 2'))
    new = [c for c in job['candidates'] if c['type'] == 'management_id']
    assert new and new[0]['locationResolved'] is False


def test_non_whitespace_difference_stays_unresolved():
    job = _merge_job()
    merge_extractions(job, _extract('phone', '0 1 0 - 1 8 3 1 - 6 5 8 7'))
    unresolved = [c for c in job['candidates']
                  if c['type'] == 'phone' and not c['locationResolved']]
    assert len(unresolved) == 1


def _grounded(cid, unit_id, text, value):
    return dict(id=cid, type='phone', value=value, unitId=unit_id,
                start=0, end=len(value), page=1, method='full', mask=[],
                confirmed=False, locationResolved=True, source='rule',
                alternativeTypes=[])


def test_distinct_grounded_values_remain_ambiguous():
    """공백만 다른 서로 다른 원문 두 개가 있으면 어느 쪽인지 알 수 없으므로 포기한다."""
    a, b = '010-1111-2222', '010- 1111-2222'
    insp = dict(format='pdf', units=[dict(id='u0', text=a, page=1),
                                     dict(id='u1', text=b, page=1)])
    job = dict(format='pdf', _inspection=insp, units=insp['units'],
               candidates=[_grounded('g0', 'u0', a, a), _grounded('g1', 'u1', b, b)],
               analysis=dict(warnings=[]))
    merge_extractions(job, _extract('phone', '0 1 0 - 1 1 1 1 - 2 2 2 2'))
    unresolved = [c for c in job['candidates'] if not c['locationResolved']]
    assert len(unresolved) == 1, [c['value'] for c in job['candidates']]


def test_single_grounded_value_repeated_is_not_ambiguous():
    """같은 원문이 여러 번 나오는 것은 모호하지 않다."""
    a = '010-1111-2222'
    insp = dict(format='pdf', units=[dict(id='u0', text=a, page=1),
                                     dict(id='u1', text=a, page=1)])
    job = dict(format='pdf', _inspection=insp, units=insp['units'],
               candidates=[_grounded('g0', 'u0', a, a), _grounded('g1', 'u1', a, a)],
               analysis=dict(warnings=[]))
    merge_extractions(job, _extract('phone', '0 1 0 - 1 1 1 1 - 2 2 2 2'))
    assert all(c['locationResolved'] for c in job['candidates'])
