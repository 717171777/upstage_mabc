"""제출 상황이 주어졌을 때의 프리셋 추천 경로. 이진 계약은 그대로 유지된다."""
import pytest

from backend import judge, hermes_worker
from backend.masking_policy import model_selectable_presets, recommended_presets


def candidate(cid='a', typ='phone', value='010-1234-5678', role='개인 휴대전화'):
    return {'id': cid, 'type': typ, 'value': value, 'role_raw': role,
            'context_raw': '', 'location': {'evidence': []}}


def payload(ctx, cands=None):
    return {'candidates': cands or [candidate()], 'context': ctx, 'documentType': 'other'}


# --- 모델에게 무엇을 보여주는가 -------------------------------------------

def test_presets_offered_only_with_submission_context():
    empty, _ = judge._validate_payload(payload({}))
    assert empty['candidates'][0]['selectablePresets'] == []

    given, _ = judge._validate_payload(payload({'recipient': '카페24', 'purpose': '입점 심사 서류 제출'}))
    assert 'phone_middle' in given['candidates'][0]['selectablePresets']


def test_purpose_alone_is_enough_context():
    given, _ = judge._validate_payload(payload({'purpose': '협력사에 정산 내역 공유'}))
    assert given['candidates'][0]['selectablePresets']


@pytest.mark.parametrize('typ,value', [
    ('resident_id', '900101-1234567'), ('foreign_id', '040726-5369070'),
])
def test_identity_numbers_are_never_offered_to_the_model(typ, value):
    """사용자는 직접 고를 수 있지만 모델 판단 대상에서는 제외한다."""
    cand = {'type': typ, 'value': value}
    assert recommended_presets(cand)          # 사용자용은 존재
    assert model_selectable_presets(cand) == {}  # 모델용은 없음
    built, _ = judge._validate_payload(
        payload({'recipient': '카페24', 'purpose': '제출'}, [candidate(typ=typ, value=value)]))
    assert built['candidates'][0]['selectablePresets'] == []


# --- 모델 출력 검증 --------------------------------------------------------

def compact(**changes):
    base = {'candidateId': 'a', 'recommendation': 'full', 'evidence': '개인 휴대전화'}
    return {**base, **changes}


def test_worker_accepts_preset_only_with_full():
    ok = hermes_worker._validate_suggestions([compact(presetId='phone_middle')])
    assert ok[0]['presetId'] == 'phone_middle'
    with pytest.raises(ValueError):
        hermes_worker._validate_suggestions([compact(recommendation='keep', presetId='phone_middle')])
    with pytest.raises(ValueError):
        hermes_worker._validate_suggestions([compact(presetId='  ')])


@pytest.mark.parametrize('preset', ['phone_middle', 'phone_tail', 'phone_suffix4'])
def test_server_keeps_only_presets_it_computed(preset):
    checked = judge._checked_batch(
        hermes_worker._validate_suggestions([compact(presetId=preset)]), [candidate()], '')
    assert checked[0]['presetId'] == preset


@pytest.mark.parametrize('bogus', ['invented_preset', 'rrn_birth_only', 'name_middle'])
def test_invented_or_wrong_type_presets_are_dropped(bogus):
    checked = judge._checked_batch(
        hermes_worker._validate_suggestions([compact(presetId=bogus)]), [candidate()], '')
    assert 'presetId' not in checked[0]
    assert checked[0]['recommendation'] == 'full'


def test_preset_dropped_when_recommendation_is_not_full():
    checked = judge._checked_batch(
        hermes_worker._validate_suggestions([compact(recommendation='keep', evidence='대표 전화')]),
        [candidate(role='대표 전화')], '')
    assert 'presetId' not in checked[0]


# --- 모델 지침과 미리보기 -------------------------------------------------

def test_model_sees_what_each_preset_reveals():
    built, _ = judge._validate_payload(payload({'recipient': '카페24', 'purpose': '담당자 연락처 대조'}))
    previews = built['candidates'][0]['presetPreviews']
    assert previews['phone_middle'] == '010-****-5678'
    assert previews['phone_tail'] == '010-1234-****'
    assert set(previews) == set(built['candidates'][0]['selectablePresets'])


def test_no_previews_without_submission_context():
    built, _ = judge._validate_payload(payload({}))
    assert built['candidates'][0]['presetPreviews'] == {}


def test_preview_never_contains_more_than_the_original_value():
    built, _ = judge._validate_payload(payload({'purpose': '제출'}))
    value = candidate()['value']
    for text in built['candidates'][0]['presetPreviews'].values():
        assert len(text) == len(value)
        assert all(a == b or a == '*' for a, b in zip(text, value))


def test_system_prompt_permits_presets_but_keeps_safe_default():
    """지침이 다시 프리셋 금지로 돌아가지 않도록 고정한다."""
    system = hermes_worker._build_system()
    assert '일부 가림 방법은 판단하지 않습니다' not in system
    assert 'presetId, 가림 범위' not in system
    assert 'selectablePresets' in system and 'presetPreviews' in system
    assert '생략하면 전체 가림' in system
    # 이진 판단 규칙은 그대로 남아 있어야 한다
    assert "recommendation('full' 또는 'keep')" in system
    assert 'keep 항목에는 presetId를 붙이지 마십시오' in system
