"""Check the actual Hermes registry path, without calling a language model."""
import base64
import json
from pathlib import Path
import pytest
from backend import store, upstage
from backend import hermes_extension as extension

ROOT = Path(__file__).resolve().parents[1]


@pytest.fixture(autouse=True)
def isolate(tmp_path, monkeypatch):
    monkeypatch.setattr(store, 'DATA_DIR', tmp_path)
    def forbidden(*args, **kwargs):
        raise AssertionError('External AI called during offline registry test')
    monkeypatch.setattr(upstage, 'analyze_file', forbidden)
    monkeypatch.delenv('UPSTAGE_API_KEY', raising=False)


def test_registration_and_real_dispatch(monkeypatch):
    extension.register_operations()
    name = 'garimi_document_operation'
    assert name in extension.registry.get_all_tool_names()
    assert extension.registry.get_toolset_for_tool(name) == 'garimi_document'
    original = extension.registry.dispatch
    seen = []
    def spy(name, args, **kwargs):
        seen.append((name, args['operation']))
        return original(name, args, **kwargs)
    monkeypatch.setattr(extension.registry, 'dispatch', spy)
    source = ROOT / 'fixtures/eval_v0/docs/docx-report_minutes-01.docx'
    created = extension.call('create', dict(data=base64.b64encode(source.read_bytes()).decode(), filename='합성.docx', format='docx', aiEnabled=False))
    assert created['job']['runtime']['platform'] == 'Hermes'
    assert created['job']['runtime']['llmUsed'] is False
    args = dict(jobId=created['job']['id'], token=created['token'])
    job = extension.call('get', args)
    assert job['id'] == args['jobId']
    assert seen == [(name, 'create'), (name, 'get')]
    assert '_tokenHash' not in job
    extension.call('delete', args)


def test_handler_sanitizes_unexpected_exception(monkeypatch):
    from backend import workflow
    def fail(*args, **kwargs):
        raise RuntimeError('private document and token sentinel')
    monkeypatch.setattr(workflow, 'execute', fail)
    extension.register_operations()
    raw = extension.registry.dispatch('garimi_document_operation', {'operation': 'get', 'args': {}})
    assert isinstance(raw, str)
    data = json.loads(raw)
    assert not data['ok'] and data['status'] == 500
    assert 'sentinel' not in raw


def test_registry_revalidates_missing_auth():
    extension.register_operations()
    with pytest.raises(store.StoreError):
        extension.call('get', {'jobId': 'a' * 32})
