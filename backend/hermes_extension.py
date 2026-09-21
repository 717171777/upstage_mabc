from .llm_config import MODEL, configured
import json
import os
import sys
from pathlib import Path

from . import workflow
from .store import StoreError

SOURCE = Path(os.environ.get('HERMES_SOURCE', '/opt/hermes-agent'))
_REGISTRY_FILE = SOURCE / 'tools' / 'registry.py'
if not _REGISTRY_FILE.exists():
    raise RuntimeError(f"Registry file not found: {_REGISTRY_FILE}")

_proj = Path(__file__).resolve().parent.parent
_gd = os.environ.get('GARIMI_DATA_DIR')
os.environ['HERMES_HOME'] = str(Path(_gd).parent / 'hermes-registry' if _gd else _proj / 'var' / 'hermes-registry')
sys.path.insert(0, str(SOURCE))

from tools.registry import registry  # real pinned Hermes module

NAME = 'garimi_document_operation'
TOOLSET = 'garimi_document'
OPERATIONS = ['create', 'get', 'plan', 'manual', 'resolve', 'context', 'render', 'preview', 'page', 'ack', 'download', 'delete', 'auto-export', 'ai-review', 'retry-analysis']

SCHEMA = {
    'type': 'object',
    'properties': {
        'operation': {'type': 'string', 'enum': OPERATIONS},
        'args': {'type': 'object'}
    },
    'required': ['operation', 'args'],
    'additionalProperties': False
}

def _handler(params, **kwargs):
    try:
        if not isinstance(params, dict):
            raise StoreError(422, 'invalid_args', '잘못된 인수입니다')
        if set(params.keys()) != {'operation', 'args'}:
            raise StoreError(422, 'invalid_args', '잘못된 인수입니다')
        op = params['operation']
        args = params['args']
        if not isinstance(op, str) or op not in OPERATIONS:
            raise StoreError(422, 'invalid_args', '잘못된 인수입니다')
        if not isinstance(args, dict):
            raise StoreError(422, 'invalid_args', '잘못된 인수입니다')

        result = workflow.execute(op, args)

        if op == 'create':
            job = result['job']
        elif 'id' in result:
            job = result
        else:
            job = None

        if job is not None:
            job['runtime'] = {
                'platform': 'Hermes',
                'execution': 'registered_extension',
                'model': MODEL,
                'llmUsed': job.get('analysis', {}).get('hermes', {}).get('status') == 'completed' or (job.get('aiReview') or {}).get('stages', {}).get('hermes', {}).get('status') == 'completed'
            }

        return json.dumps({'ok': True, 'result': result})
    except StoreError as exc:
        return json.dumps({'ok': False, 'status': exc.status, 'error': exc.message, 'code': exc.code})
    except Exception:
        return json.dumps({'ok': False, 'status': 500, 'error': '내부 서버 오류가 발생했습니다', 'code': 'internal_error'})

def register_operations():
    all_names = registry.get_all_tool_names()
    if NAME in all_names:
        if registry.get_toolset_for_tool(NAME) != TOOLSET:
            raise RuntimeError(f"Tool {NAME} already registered with different toolset")
        return
    registry.register(
        name=NAME,
        toolset=TOOLSET,
        schema=SCHEMA,
        handler=_handler,
        is_async=False,
        max_result_size_chars=50 * 1024 * 1024
    )

def call(operation, args):
    register_operations()
    raw = registry.dispatch(NAME, {'operation': operation, 'args': args})
    decoded = json.loads(raw)
    if not isinstance(decoded, dict) or 'ok' not in decoded:
        raise StoreError(500, 'invalid_envelope', '잘못된 응답 형식입니다')
    if decoded['ok'] is True:
        return decoded['result']
    raise StoreError(
        int(decoded.get('status', 500)),
        decoded.get('code', 'unknown'),
        decoded.get('error', '알 수 없는 오류')
    )

__all__ = ['call', 'register_operations', 'registry']
