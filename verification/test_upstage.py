"""Independent adapter contract checks. Never makes network requests."""
import copy
import json
from pathlib import Path

import httpx
import pytest

from backend import upstage

ROOT = Path(__file__).resolve().parents[1]
SOURCE = ROOT / 'fixtures/eval_v0/docs/report_minutes-01.pdf'
KEY = 'synthetic-test-key-not-a-secret'


@pytest.fixture
def wire(monkeypatch):
    calls = []
    payload = {key: [] for key in upstage.EXTRACT_KEYS}
    payload['name'] = [{'raw_value': '가상인', 'role_raw': '참여자', 'context_raw': '참여자 가상인'}]
    additional = {'name': [{'raw_value': {'_value': '가상인', 'confidence': 'high', 'page': 1}}]}
    bodies = {
        '/v1/document-digitization': {'model': 'document-parse-test', 'content': {'text': '참여자 가상인'}, 'elements': [{'id': 1, 'page': 1, 'category': 'table', 'content': {'text': '참여자 가상인'}}]},
        '/v1/document-classification': {'model': 'document-classify-test', 'choices': [{'message': {'content': 'report_minutes'}}]},
        '/v1/information-extraction': {'model': 'information-extract-test', 'choices': [{'message': {'content': json.dumps(payload), 'tool_calls': [{'function': {'name': 'additional_values', 'arguments': json.dumps(additional)}}]}}]},
    }
    statuses = {}

    def handler(req):
        calls.append(req)
        assert req.headers['authorization'] == f'Bearer {KEY}'
        assert req.url.host == 'api.upstage.ai'
        return httpx.Response(statuses.get(req.url.path, 200), json=bodies[req.url.path])

    client = httpx.Client
    monkeypatch.setattr(upstage.httpx, 'Client', lambda **kw: client(transport=httpx.MockTransport(handler), **kw))
    monkeypatch.setenv('UPSTAGE_API_KEY', KEY)
    return calls, bodies, statuses


def test_ai_off_zero_network_or_key(wire, monkeypatch):
    monkeypatch.delenv('UPSTAGE_API_KEY')
    result = upstage.analyze_file(Path('/does-not-exist.pdf'), False)
    assert not wire[0]
    assert all(x['status'] == 'disabled' for x in result['stages'].values())


@pytest.mark.parametrize('flag', [None, 0, 1, 'false', 'true'])
def test_flag_strict(wire, flag):
    with pytest.raises(ValueError):
        upstage.analyze_file(SOURCE, flag)
    assert not wire[0]


def test_synthetic_content_guard_before_network(wire, tmp_path):
    fake = tmp_path / SOURCE.name
    fake.write_bytes(b'not an approved synthetic fixture')
    with pytest.raises(ValueError):
        upstage.analyze_file(fake, True)
    assert not wire[0]
    fake.write_bytes(SOURCE.read_bytes())
    assert upstage.is_synthetic(fake)


def test_exact_requests_and_actual_stage_results(wire):
    progress = []
    result = upstage.analyze_file(SOURCE, True, progress=lambda *args: progress.append(args))
    assert len(wire[0]) == 3
    assert result['documentType'] == 'report_minutes'
    assert result['extracted']['name'][0]['raw_value'] == '가상인'
    assert result['additional']['name'][0]['raw_value']['confidence'] == 'high'
    assert result['elements'][0]['category'] == 'table'
    assert {x: y['model'] for x, y in result['stages'].items()} == {
        'parse': 'document-parse-test', 'classify': 'document-classify-test', 'extract': 'information-extract-test'}
    for stage in ('parse', 'classify', 'extract'):
        assert [rec['status'] for name, rec in progress if name == stage] == ['running', 'completed']
    assert all(set(rec).issubset({'status', 'model', 'error'}) for _, rec in progress)
    requests = {r.url.path: r for r in wire[0]}
    parse = requests['/v1/document-digitization']
    assert b'name="document"' in parse.content
    assert b'name="words"\r\n\r\ntrue' in parse.content
    assert b'document-parse' in parse.content
    ie = json.loads(requests['/v1/information-extraction'].content)
    assert ie['model'] == 'information-extract'
    assert ie['location'] is True and ie['confidence'] is True
    assert ie['location_granularity'] == 'all'
    assert set(ie['response_format']['json_schema']['schema']['properties']) == set(upstage.EXTRACT_KEYS)
    assert ie['messages'][0]['content'][0]['image_url']['url'].startswith('data:application/pdf;base64,')


def test_independent_failure_and_no_raw_response_leak(wire):
    wire[2]['/v1/document-classification'] = 401
    wire[1]['/v1/document-classification'] = {'error': 'private body sentinel'}
    result = upstage.analyze_file(SOURCE, True)
    assert len(wire[0]) == 3
    assert result['documentType'] is None
    assert result['stages']['classify']['status'] == 'failed'
    assert result['stages']['extract']['status'] == 'completed'
    assert result['warnings']
    assert 'private body sentinel' not in json.dumps(result)


def test_bad_extract_not_successful_zero(wire):
    wire[1]['/v1/information-extraction']['choices'][0]['message']['content'] = '{}'
    result = upstage.analyze_file(SOURCE, True)
    assert result['stages']['extract']['status'] == 'failed'
    assert result['warnings']


def test_retry_bounded(wire):
    wire[2]['/v1/document-digitization'] = 503
    result = upstage.analyze_file(SOURCE, True)
    assert len([x for x in wire[0] if x.url.path == '/v1/document-digitization']) == 2
    assert result['stages']['parse']['status'] == 'failed'


def test_unknown_class_and_override(wire):
    wire[1]['/v1/document-classification']['choices'][0]['message']['content'] = 'unknown'
    result = upstage.analyze_file(SOURCE, True, document_type='case_record')
    assert result['documentType'] == 'case_record'
    assert result['stages']['classify']['status'] == 'failed'


def test_class_preserves_all_twelve(wire):
    upstage.analyze_file(SOURCE, True, document_type='personnel_roster')
    request = next(x for x in wire[0] if x.url.path == '/v1/information-extraction')
    props = json.loads(request.content)['response_format']['json_schema']['schema']['properties']
    baseline = json.loads((ROOT / 'reference/schemas/extract-other.schema.json').read_text())['properties']
    assert set(props) == set(baseline)
    assert all(baseline[k]['description'] in props[k]['description'] for k in baseline)


def test_malformed_elements_failed_without_exception(wire):
    wire[1]['/v1/document-digitization']['elements'] = [None]
    result = upstage.analyze_file(SOURCE, True)
    assert result['stages']['parse']['status'] == 'failed'


def test_model_metadata_not_fabricated(wire):
    for body in wire[1].values():
        body.pop('model')
    result = upstage.analyze_file(SOURCE, True)
    assert all(not rec.get('model') for rec in result['stages'].values())
