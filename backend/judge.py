import os
import sys
import json
import hashlib
import tempfile
import subprocess
import re
import time
from pathlib import Path

MODEL = 'solar-pro4-260806'
BATCH_SIZE = 20

ALLOWED_TYPES = frozenset([
    'name', 'phone', 'email', 'address', 'dob',
    'resident_id', 'foreign_id', 'passport',
    'driver_license', 'account', 'card', 'management_id',
])


def _validate_payload(payload):
    if not isinstance(payload, dict):
        return None, ['payload은 dict이어야 합니다']
    jobWorkspace = payload.get('jobWorkspace')
    context = payload.get('context', {})
    documentType = payload.get('documentType')
    candidates = payload.get('candidates', [])
    if not isinstance(candidates, list):
        return None, ['candidates는 목록이어야 합니다']
    if len(candidates) > 1000:
        return None, ['후보가 너무 많습니다 (최대 1000개)']
    if not isinstance(context, dict):
        return None, ['context는 dict이어야 합니다']
    context = {key: context.get(key) for key in ('recipient', 'purpose', 'keepInfo')}

    for field in ('recipient', 'purpose', 'keepInfo'):
        val = context.get(field)
        if val is None:
            context[field] = ''
        elif not isinstance(val, str) or len(val) > 2000:
            return None, [f'context.{field}는 길이 2000 이하의 문자열이어야 합니다']

    seen = set()
    for c in candidates:
        if not isinstance(c, dict):
            return None, ['각 후보는 dict이어야 합니다']
        cid = c.get('id')
        if not isinstance(cid, str) or not (1 <= len(cid) <= 128) or not cid:
            return None, ['후보 id는 길이 1~128의 비어있지 않은 문자열이어야 합니다']
        if cid in seen:
            return None, ['중복된 후보 id가 있습니다']
        seen.add(cid)

        ctype = c.get('type', '')
        if not isinstance(ctype, str) or ctype not in ALLOWED_TYPES:
            return None, ['후보 type은 허용된 12종 열거형 값이어야 합니다']

        value = c.get('value', '')
        if not isinstance(value, str) or len(value) > 1000:
            return None, ['후보 value는 길이 1000 이하의 문자열이어야 합니다']

        role_raw = c.get('role_raw', '')
        if not isinstance(role_raw, str) or len(role_raw) > 300:
            return None, ['role_raw는 길이 300 이하의 문자열이어야 합니다']

        context_raw = c.get('context_raw', '')
        if not isinstance(context_raw, str) or len(context_raw) > 300:
            return None, ['context_raw는 길이 300 이하의 문자열이어야 합니다']

        location = c.get('location', {})
        if not isinstance(location, dict):
            return None, ['위치 근거 형식이 올바르지 않습니다']
        evidence = location.get('evidence', [])
        if not isinstance(evidence, list) or len(evidence) > 8:
            return None, ['위치 근거는 최대 8개입니다']
        for item in evidence:
            if (not isinstance(item, dict) or not isinstance(item.get('text'), str)
                    or len(item['text']) > 300 or not isinstance(item.get('relation'), str)
                    or len(item['relation']) > 40):
                return None, ['위치 근거 형식이 올바르지 않습니다']

        if any(not isinstance(location.get(key, ''), str) or len(location.get(key, '')) > limit
               for key, limit in (('label', 180), ('region', 40), ('section', 200))):
            return None, ['위치 설명이 너무 길거나 올바르지 않습니다']

    candidates = [{**{k: c[k] for k in ('id', 'type', 'value', 'role_raw', 'context_raw', 'location') if k in c},
                   'decisionTask': 'mask_or_keep'} for c in candidates]
    for c in candidates:
        loc = c.get('location', {})
        c['location'] = {k: loc[k] for k in ('label', 'region', 'section') if k in loc}
        if type(loc.get('page')) is int and loc['page'] > 0:
            c['location']['page'] = loc['page']
        c['location']['evidence'] = [{'text': e['text'], 'relation': e['relation']}
                                    for e in loc.get('evidence', [])]
        # Expose the same server-side keep boundary to the model before asking
        # for a decision. These are permissible grounds, never a keep decision.
        keep_evidence = _allowed_keep_evidence(c, context.get('keepInfo', ''))
        c['allowedKeepEvidence'] = keep_evidence
        c['allowedRecommendations'] = ['full'] + (['keep'] if keep_evidence else [])

    return {
        'jobWorkspace': jobWorkspace,
        'context': context,
        'documentType': documentType,
        'candidates': candidates,
    }, []


def _validate_job_workspace(path):
    garimi = os.environ.get('GARIMI_DATA_DIR')
    if garimi is None:
        garimi = str(Path(__file__).resolve().parent.parent / 'var' / 'jobs')
    try:
        garimi_path = Path(garimi)
        if garimi_path.is_symlink():
            return False, ['GARIMI_DATA_DIR은 심볼릭 링크일 수 없습니다']
        garimi_res = garimi_path.resolve()
        ws_path = Path(path)
        if ws_path.is_symlink():
            return False, ['jobWorkspace는 심볼릭 링크일 수 없습니다']
        ws_res = ws_path.resolve()
    except Exception:
        return False, ['경로가 유효하지 않습니다']

    if ws_res == garimi_res:
        return False, ['jobWorkspace는 데이터 디렉터리 자체일 수 없습니다']

    try:
        ws_res.relative_to(garimi_res)
    except ValueError:
        return False, ['jobWorkspace가 GARIMI_DATA_DIR 외부에 있습니다']

    if not ws_res.is_dir():
        return False, ['jobWorkspace는 디렉터리여야 합니다']

    return True, []


_PUBLIC_CONTACT = re.compile(r'(?:대표\s*(?:전화|번호|연락처|메일|이메일|문의)|공용|공식\s*(?:전화|메일|이메일)|문의\s*창구)')
_PRIVATE_CONTACT = re.compile(r'개인|휴대|자택|비상|신청인|직원|사적|private|personal', re.I)


def _contact_segments(text, value):
    return [part.strip() for part in re.split(r'[\n;|]', text) if value and value in part]


def _public_contact_fact(text, value):
    """A role applies to this field, never to all contacts in the same paragraph."""
    segments = _contact_segments(text, value)
    if segments:
        for segment in segments:
            if _PRIVATE_CONTACT.search(segment):
                continue
            # More than one contact in an undivided field is ambiguous.
            contacts = re.findall(r'[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Za-z]{2,}|0\d{1,2}[- ]\d{3,4}[- ]\d{4}', segment)
            if len(contacts) > 1:
                continue
            if _PUBLIC_CONTACT.search(segment[:segment.index(value)]):
                return True
        return False
    # Separate, directly attached table labels have no value. Do not accept
    # labels containing another address/number or a conflicting private role.
    return bool(len(text) <= 80 and _PUBLIC_CONTACT.search(text)
                and not _PRIVATE_CONTACT.search(text) and not re.search(r'[\d@|;\n]', text))


def _validate_suggestion(s, batch_ids, batch_candidates, keepInfo):
    if not isinstance(s, dict):
        return None, ['제안이 dict이 아닙니다']
    required = {'candidateId', 'type', 'content', 'reason', 'recommendation', 'evidence'}
    if not required.issubset(s.keys()):
        return None, ['제안에 필요한 키가 누락되었습니다']
    cid = s['candidateId']
    if cid not in batch_ids:
        return None, ['알 수 없는 candidateId입니다']
    stype = s['type']
    if stype not in ('question', 'recommendation'):
        return None, ['제안 type이 유효하지 않습니다']
    content = s['content']
    if not isinstance(content, str) or not content or len(content) > 1000:
        return None, ['content는 길이 1~1000의 문자열이어야 합니다']
    reason = s['reason']
    if not isinstance(reason, str) or len(reason) > 1000:
        return None, ['reason은 길이 1000 이하의 문자열이어야 합니다']
    rec = s['recommendation']
    if rec not in ('full', 'keep'):
        return None, ['AI 판단은 full 또는 keep만 허용합니다']
    evidence = s['evidence']
    if not isinstance(evidence, str) or len(evidence) > 600:
        return None, ['evidence는 길이 600 이하의 문자열이어야 합니다']

    question = s.get('question')
    if 'question' in s and (not isinstance(question, str) or not question.strip() or len(question) > 120
                                 or stype != 'question' or rec != 'full' or not evidence):
        return None, ['확인 질문은 원문 근거가 있는 전체 가림 항목에만 허용합니다']

    cand = next((c for c in batch_candidates if c['id'] == cid), None)
    role = cand.get('role_raw', '') if cand else ''
    ctx = cand.get('context_raw', '') if cand else ''
    facts = ((cand or {}).get('location') or (cand or {}).get('locationContext') or {}).get('evidence', [])
    fact_texts = [f.get('text', '') for f in facts if isinstance(f, dict)]

    # 빈 근거는 type=question & recommendation=full일 때만 허용
    if not evidence:
        if not (stype == 'question' and rec == 'full'):
            return None, ['빈 근거는 question 유형이면서 full 추천일 때만 허용됩니다']
    else:
        # 증거는 role_raw, context_raw, keepInfo 중 하나의 실제 부분문자열이어야 함
        if evidence not in role and evidence not in ctx and not any(evidence in t for t in fact_texts):
            if not (keepInfo and evidence in keepInfo):
                return None, ['증거가 role_raw, context_raw, keepInfo에 존재하지 않습니다']

    if rec == 'keep':
        if not evidence:
            return None, ['keep는 비어있지 않은 근거가 필요합니다']
        # 공개 역할 대표/공용/문의창구가 실제 role/context에 있거나
        # keepInfo에 해당 value가 명시되어 있어야 함
        direct = [f.get('text', '') for f in facts if f.get('relation') in
                  ('self', 'same_cell', 'table_row_first_cell', 'table_first_row', 'same_row_left')]
        # A person labelled 대표자 is not a public contact. Headings/neighbours alone
        # never authorize keeping unrelated values elsewhere in the document.
        value = (cand or {}).get('value', '')
        private_here = any(_PRIVATE_CONTACT.search(segment) for fact in facts
                           if fact.get('relation') == 'self'
                           for segment in _contact_segments(fact.get('text', ''), value))
        has_public = bool(cand and cand.get('type') in ('phone', 'email') and not private_here and
                          any(evidence in text and _public_contact_fact(text, value)
                              for text in [role, ctx, *direct]))
        named = bool(keepInfo and cand and cand.get('value', '') in keepInfo and evidence in keepInfo)
        if not (has_public or named):
            return None, ['keep는 공개 역할 또는 keepInfo에 명시적 명시가 필요합니다']

    stable = hashlib.sha256(f"{cid}{stype}{content}".encode()).hexdigest()[:16]
    out = {k: s[k] for k in (*required, 'presetId', 'question') if k in s}
    out['id'] = stable
    out.pop('mask', None)
    if rec != 'partial':
        out.pop('presetId', None)
    return out, []


def _allowed_keep_evidence(candidate, keep_info):
    facts = candidate.get('location', {}).get('evidence', [])
    texts = [candidate.get('role_raw', ''), candidate.get('context_raw', '')]
    texts.extend(f.get('text', '') for f in facts if f.get('relation') in
                 ('self', 'same_cell', 'table_row_first_cell', 'table_first_row', 'same_row_left'))
    value = candidate.get('value', '')
    if value and keep_info and value in keep_info:
        texts.append(value)
    allowed = []
    for evidence in dict.fromkeys(texts):
        if not evidence or len(evidence) > 600:
            continue
        probe = {'candidateId': candidate['id'], 'type': 'recommendation',
                 'content': '유지 근거 확인', 'reason': '', 'recommendation': 'keep', 'evidence': evidence}
        _, errors = _validate_suggestion(probe, {candidate['id']}, [candidate], keep_info)
        if not errors:
            allowed.append(evidence)
    return allowed


class BatchFailure(Exception):
    def __init__(self, code, message, transient=False):
        super().__init__(message)
        self.code, self.message, self.transient = code, message, transient


ERROR_MESSAGES = {
    'UPSTREAM_TIMEOUT': '모델 응답 시간이 초과됐습니다.',
    'NETWORK_ERROR': '모델 연결이 일시적으로 끊겼습니다.',
    'RATE_LIMIT': '모델 요청 한도에 도달했습니다.',
    'PROVIDER_UNAVAILABLE': '모델 제공 서버가 일시적으로 응답하지 않습니다.',
    'AUTH_ERROR': '모델 연결 인증을 확인해야 합니다.',
    'RESPONSE_INVALID': '판단 응답의 형식 또는 근거 검사를 통과하지 못했습니다.',
    'WORKER_FAILED': '가림 여부 판단에 실패했습니다.',
}
TRANSIENT_CODES = {'UPSTREAM_TIMEOUT', 'NETWORK_ERROR', 'RATE_LIMIT', 'PROVIDER_UNAVAILABLE'}


def _checked_batch(suggestions, batch, keep_info):
    if not isinstance(suggestions, list):
        raise BatchFailure('RESPONSE_INVALID', ERROR_MESSAGES['RESPONSE_INVALID'])
    ids = {c['id'] for c in batch}
    checked, counts = [], {}
    for suggestion in suggestions:
        clean, errors = _validate_suggestion(suggestion, ids, batch, keep_info)
        if errors:
            raise BatchFailure('RESPONSE_INVALID', errors[0])
        cid = clean['candidateId']
        counts[cid] = counts.get(cid, 0) + 1
        if counts[cid] > 1:
            raise BatchFailure('RESPONSE_INVALID', '한 항목에 중복 판단이 있습니다')
        checked.append({**clean, 'source': 'sp4'})
    if set(counts) != ids:
        raise BatchFailure('RESPONSE_INVALID', '일부 항목의 가림 여부 판단이 누락됐습니다')
    return checked


def _request_batch(batch_payload, workspace, keep_info):
    with tempfile.TemporaryDirectory(prefix='hermes-', dir=workspace) as tmp:
        os.chmod(tmp, 0o700)
        env = os.environ.copy()
        for key in ('OPENAI_API_KEY', 'ANTHROPIC_API_KEY', 'GOOGLE_API_KEY', 'OPENROUTER_API_KEY'):
            env.pop(key, None)
        env['HERMES_HOME'] = tmp
        env['HERMES_SOURCE'] = os.environ.get('HERMES_SOURCE', '/opt/hermes-agent')
        try:
            result = subprocess.run(
                [os.environ.get('HERMES_PYTHON', sys.executable), str(Path(__file__).with_name('hermes_worker.py'))],
                input=json.dumps(batch_payload), capture_output=True, text=True,
                timeout=180, cwd=tmp, env=env,
            )
        except subprocess.TimeoutExpired:
            raise BatchFailure('UPSTREAM_TIMEOUT', ERROR_MESSAGES['UPSTREAM_TIMEOUT'], True)
        except Exception:
            raise BatchFailure('WORKER_FAILED', ERROR_MESSAGES['WORKER_FAILED'])
        if len(result.stdout) > 1_000_000:
            raise BatchFailure('RESPONSE_INVALID', '워커 stdout이 1MB를 초과했습니다')
        try:
            response = json.loads(result.stdout)
        except (ValueError, TypeError):
            raise BatchFailure('RESPONSE_INVALID', '워커가 유효하지 않은 JSON을 반환했습니다')
        if not isinstance(response, dict):
            raise BatchFailure('RESPONSE_INVALID', '워커 응답이 dict이 아닙니다')
        if result.returncode != 0:
            diagnostics = response.get('diagnostics')
            code = diagnostics.get('errorCode') if isinstance(diagnostics, dict) else None
            if code not in ERROR_MESSAGES:
                code = 'RESPONSE_INVALID' if any(w in ('final_response 파싱에 실패했습니다', 'suggestions 검증에 실패했습니다') for w in response.get('warnings', []) if isinstance(w, str)) else 'WORKER_FAILED'
            raise BatchFailure(code, ERROR_MESSAGES[code], code in TRANSIENT_CODES)
        if response.get('status') != 'completed' or response.get('model') != MODEL:
            raise BatchFailure('RESPONSE_INVALID', '워커 응답의 완료 상태와 모델을 확인할 수 없습니다')
        return _checked_batch(response.get('suggestions'), batch_payload['candidates'], keep_info)


def judge_exceptions(payload):
    validated, errors = _validate_payload(payload)
    if errors:
        return {'suggestions': [], 'model': None, 'status': 'failed', 'warnings': errors}
    if not validated['candidates']:
        return {'suggestions': [], 'model': None, 'status': 'not_needed', 'warnings': []}
    ok, errors = _validate_job_workspace(validated['jobWorkspace'])
    if not ok or not os.environ.get('UPSTAGE_API_KEY', ''):
        return {'suggestions': [], 'model': MODEL, 'status': 'failed', 'warnings': errors or ['UPSTAGE_API_KEY가 설정되지 않았습니다']}

    # Internal callbacks/cache are never placed in the worker request.
    progress = payload.get('_progress')
    cache = payload.get('_cachedBatches', {})
    if not isinstance(cache, dict):
        cache = {}
    from .document_policy import profile
    candidates = validated['candidates']
    batches = [candidates[i:i + BATCH_SIZE] for i in range(0, len(candidates), BATCH_SIZE)]
    all_suggestions, warnings = [], []
    done = 0
    final_error = None
    for index, batch in enumerate(batches):
        request = {'candidates': batch, 'context': validated['context'],
                   'documentType': validated['documentType'], 'documentPolicy': profile(validated['documentType'])}
        key = hashlib.sha256(json.dumps({'model': MODEL, 'contractRevision': 4, 'scope': payload.get('_cacheScope'), **request},
                                       sort_keys=True, ensure_ascii=False).encode()).hexdigest()
        record = {'totalBatches': len(batches), 'completedBatches': done, 'currentBatch': index + 1,
                  'attempt': 0, 'maxAttempts': 2, 'errorCode': None}
        checked = None
        if key in cache:
            try:
                checked = _checked_batch(cache[key], batch, validated['context'].get('keepInfo', ''))
            except BatchFailure:
                pass
        if checked is None:
            for attempt in range(1, 3):
                record.update(attempt=attempt)
                if callable(progress):
                    progress(dict(record))
                try:
                    checked = _request_batch(request, validated['jobWorkspace'], validated['context'].get('keepInfo', ''))
                    break
                except BatchFailure as error:
                    record['errorCode'] = error.code
                    if not error.transient or attempt == 2:
                        final_error = error.code
                        warnings.append(f'{index + 1}번째 판단 묶음: {error.message}')
                        break
                    if callable(progress):
                        progress(dict(record))
                    time.sleep(1)
        if checked is not None:
            done += 1
            all_suggestions.extend(checked)
            record.update(completedBatches=done, errorCode=None)
            if callable(progress):
                progress(dict(record), key, checked)
        elif callable(progress):
            progress(dict(record))
    return {'suggestions': all_suggestions, 'model': MODEL, 'status': 'failed' if warnings else 'completed',
            'warnings': warnings, 'errorCode': final_error}
