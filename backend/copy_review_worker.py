import copy
import os
from pathlib import Path

from . import store, workflow_files, upstage, engine, pdf_io
from .analysis_merge import merge_extractions
from .judge import judge_exceptions
from .location_context import attach_location_context
from .document_policy import candidate_for_judge


def run_review(jid, token, snapshot, temp, capacity):
    path = Path(temp.name) / ('saved.' + snapshot['format'])
    report = copy.deepcopy(snapshot['aiReview'])
    report['status'] = 'failed'
    report['warnings'] = []

    try:
        if snapshot['format'] == 'pdf':
            info = pdf_io.inspect(path, allow_empty=True)
        else:
            info = engine.inspect_document(path)

        tempjob = copy.deepcopy(snapshot)
        tempjob['_inspection'] = info
        tempjob['units'] = info['units']
        tempjob['candidates'] = engine.rule_candidates(info)
        tempjob['analysis'] = {'warnings': []}

        result = upstage._run_extract(
            path,
            os.environ['UPSTAGE_API_KEY'],
            snapshot['documentType'],
        )

        report['stages'] = report.get('stages', {})
        report['stages']['extract'] = {
            'status': result.get('status'),
            'model': result.get('model'),
        }

        if result.get('status') == 'completed':
            merge_extractions(
                tempjob,
                {
                    'extracted': result.get('extracted', {}),
                    'additional': result.get('additional', {}),
                    'elements': [],
                },
            )

        # Fully opaque mask glyphs contain no identity; do not report them as a person.
        tempjob['candidates'] = [c for c in tempjob['candidates'] if not (
            c.get('value', '').strip() and set(c['value'].strip()) <= {'█', '■', ' ', '\t', '\n'})]
        attach_location_context(path, info, tempjob['candidates'])
        candidates = tempjob['candidates'][:1000]
        incomplete = bool(tempjob.get('analysis', {}).get('incomplete'))
        incomplete = len(tempjob['candidates']) > 1000 or incomplete

        if candidates:
            payload = {
                'jobWorkspace': str(store.workspace(jid)),
                'context': snapshot['context'],
                'documentType': snapshot['documentType'],
                'candidates': [candidate_for_judge(c) for c in candidates],
            }
            judge_result = judge_exceptions(payload)
        else:
            judge_result = {
                'status': 'not_needed',
                'model': None,
                'suggestions': [],
                'warnings': [],
            }

        report['stages']['hermes'] = {
            'status': judge_result.get('status'),
            'model': judge_result.get('model'),
        }

        suggestions_by_id = {
            s['candidateId']: s.get('reason')
            for s in judge_result.get('suggestions', [])
            if 'candidateId' in s
        }

        findings = []
        for c in candidates:
            reason = suggestions_by_id.get(c['id'])
            if reason is None:
                reason = '공유 사본에 남아 있어 확인이 필요한 정보입니다.'

            planned_keep = any(
                sc.get('method') == 'keep'
                and sc.get('type') == c.get('type')
                and sc.get('value') == c.get('value')
                for sc in snapshot.get('candidates', [])
            )

            findings.append({
                'id': c['id'],
                'type': c['type'],
                'value': c['value'],
                'reason': reason,
                'plannedKeep': planned_keep,
                'locationResolved': bool(c.get('locationResolved')),
            })

        report['findings'] = findings

        extract_status = report['stages']['extract']['status']
        judge_status = report['stages']['hermes']['status']
        if extract_status == 'completed' and judge_status in ('completed', 'not_needed') and not incomplete:
            report['status'] = 'completed'
        else:
            report['status'] = 'partial'

        report['warnings'] = list(judge_result.get('warnings', [])) + tempjob['analysis'].get('warnings', [])
        if incomplete:
            report['warnings'].append('일부 후보의 검토가 끝나지 않았습니다.')
        if extract_status != 'completed':
            report['warnings'].append('추출 단계에서 오류가 발생하여 일부 검토가 완료되지 않았습니다.')

    except Exception:
        report['status'] = 'failed'
        report['warnings'] = ['검토 처리 중 예기치 않은 오류가 발생했습니다.']

    finally:
        try:
            try:
                with store.locked(jid):
                    fresh = store.load_job(jid, token)
                    artifact = fresh.get('artifact') or {}
                    if (fresh.get('version') == snapshot['version']
                            and (fresh.get('aiReview') or {}).get('requestId') == report['requestId']
                            and artifact.get('sha256') == report['sha256']):
                        workflow_files.artifact_path(fresh)
                        fresh['aiReview'] = report
                        store.save_job(fresh)
            except Exception:
                pass
        finally:
            try:
                temp.cleanup()
            finally:
                capacity.release()
