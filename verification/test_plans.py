"""Independent contract and adversarial plan checks; synthetic inline text only."""
import copy
import pytest
from backend import plans
from backend.store import StoreError


@pytest.fixture
def job():
    candidate = dict(id='c1', type='name', value='가상인', unitId='u1', start=0, end=3,
                     method='full', mask=[], confirmed=False, locationResolved=True)
    return dict(version=1, status='review', candidates=[candidate], metadata=[dict(id='meta1', action='delete')],
                metadataReviewed=False, context=dict(recipient=None, purpose='', keepInfo=None), documentType=None,
                _inspection=dict(units=[dict(id='u1', text='가상인 010-0000-1111 가상인', page=None)]))


@pytest.mark.parametrize('version', [None, 0, 2, True, '1'])
def test_version_rejected(job, version):
    with pytest.raises(StoreError) as ex:
        plans.check_version(job, version)
    assert ex.value.status == 409


def test_plan_preserves_location_and_requires_review(job):
    plans.apply_plan(job, dict(version=1, candidates=[dict(id='c1', method='partial', mask=[[1, 3]], confirmed=True)], metadataActions={'meta1': 'delete'}, metadataReviewed=True))
    assert job['candidates'][0]['value'] == '가상인'
    assert job['candidates'][0]['decisionSource'] == 'user'
    plans.assert_ready(job)


@pytest.mark.parametrize('mask', [[[-1, 2]], [[0, 4]], [[False, 2]], [[1.0, 2]], [['1', 2]], [[0, 3]], [], [[0, 2], [1, 3]]])
def test_bad_partial_rejected_atomically(job, mask):
    before = copy.deepcopy(job)
    with pytest.raises(StoreError):
        plans.apply_plan(job, dict(version=1, candidates=[dict(id='c1', method='partial', mask=mask, confirmed=True)], metadataReviewed=True))
    assert job == before


@pytest.mark.parametrize('extra', [dict(value='changed'), dict(start=1), dict(unitId='fake'), dict(type='phone')])
def test_client_cannot_forge_location_or_value(job, extra):
    with pytest.raises(StoreError):
        plans.apply_plan(job, dict(version=1, candidates=[dict(id='c1', method='full', mask=[], confirmed=True, **extra)]))


def test_metadata_unknown_atomic(job):
    before = copy.deepcopy(job)
    with pytest.raises(StoreError):
        plans.apply_plan(job, dict(version=1, candidates=[dict(id='c1', method='keep', mask=[], confirmed=True)], metadataActions={'unknown': 'keep'}))
    assert job == before


def test_plan_preserves_evidence_and_page(job):
    original = job['candidates'][0]
    original.update(page=3, reason='검증된 원문 근거', alternativeTypes=['management_id'], source='rule')
    plans.apply_plan(job, dict(version=1, candidates=[dict(id='c1', method='keep', mask=[], confirmed=True)]))
    after = job['candidates'][0]
    for key in ('page', 'reason', 'alternativeTypes', 'source'):
        assert after[key] == original[key]


def test_explicit_null_metadata_review_rejected(job):
    with pytest.raises(StoreError):
        plans.apply_plan(job, dict(version=1, metadataReviewed=None))


def test_unhashable_candidate_id_rejected(job):
    with pytest.raises(StoreError):
        plans.apply_plan(job, dict(version=1, candidates=[dict(id=[], method='full', mask=[], confirmed=True)]))


def test_manual_distinct_occurrence(job):
    c = plans.add_manual(job, dict(version=1, unitId='u1', start=18, end=21, type='name'))
    # Locate test range from actual synthetic text, never assert a guessed string.
    assert c['value'] == job['_inspection']['units'][0]['text'][18:21]
    assert c['id'] != 'c1' and c['method'] == 'full' and c['confirmed'] is False


def test_manual_overlap_with_keep_rejected(job):
    job['candidates'][0]['method'] = 'keep'
    with pytest.raises(StoreError):
        plans.add_manual(job, dict(version=1, unitId='u1', start=1, end=3, type='name'))


def test_optional_context_preserves_decisions(job):
    job['candidates'][0].update(method='keep', confirmed=True)
    before = copy.deepcopy(job['candidates'])
    plans.apply_context(job, dict(version=1, context=dict(recipient=None, purpose='', keepInfo=None), documentType='case_record'))
    assert job['candidates'] == before
    assert job['documentType'] == 'case_record'


def test_ready_requires_confirmation_and_metadata(job):
    with pytest.raises(StoreError):
        plans.assert_ready(job)
    job['candidates'][0]['confirmed'] = True
    with pytest.raises(StoreError):
        plans.assert_ready(job)
    job['metadataReviewed'] = True
    plans.assert_ready(job)


def test_resolve_uses_actual_source(job):
    job['candidates'].append(dict(id='unresolved', type='name', value='틀린추출값', locationResolved=False, method='full', mask=[], confirmed=False))
    c = plans.resolve_candidate(job, dict(version=1, candidateId='unresolved', unitId='u1', start=18, end=21))
    assert c['value'] == job['_inspection']['units'][0]['text'][18:21]
    assert c['extractedValue'] == '틀린추출값'
    assert c['locationResolved'] is True and c['confirmed'] is False


def test_resolve_exact_existing_requires_review(job):
    job['candidates'][0]['confirmed'] = True
    job['candidates'].append(dict(id='unresolved', type='management_id', value='가상인', locationResolved=False, method='full', mask=[], confirmed=False))
    c = plans.resolve_candidate(job, dict(version=1, candidateId='unresolved', unitId='u1', start=0, end=3))
    assert len(job['candidates']) == 1
    assert c['id'] == 'c1' and c['confirmed'] is False
    assert 'management_id' in c['alternativeTypes']


@pytest.mark.parametrize('status', ['analyzing', 'rendering'])
def test_busy_mutation_rejected(job, status):
    job['status'] = status
    with pytest.raises(StoreError) as ex:
        plans.check_version(job, 1)
    assert ex.value.status == 409
