"""Validate user sharing instructions before persisting or dispatching a job."""


def validate_context(context, *, partial=False):
    # Imported lazily so store can also validate direct callers before writing.
    from .store import StoreError

    if not isinstance(context, dict):
        raise StoreError(422, 'invalid_context', '공유 상황은 객체여야 합니다.')
    limits = {'recipient': 100, 'purpose': 2000, 'keepInfo': 2000}
    if any(key not in limits for key in context):
        raise StoreError(422, 'invalid_context', '허용되지 않은 공유 상황 항목입니다.')
    for key, value in context.items():
        if value is None and key != 'purpose':
            continue
        if not isinstance(value, str):
            raise StoreError(422, 'invalid_context', f'{key} 항목의 형식이 올바르지 않습니다.')
        if len(value) > limits[key]:
            raise StoreError(422, 'invalid_context', f'{key} 항목은 {limits[key]}자 이내로 입력해 주세요.')
    result = {} if partial else {'recipient': None, 'purpose': '', 'keepInfo': None}
    result.update(context)
    return result
