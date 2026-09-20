import json
import pytest
from backend import claude_transport as t, hermes_worker as w, judge, llm_config
from verification.test_worker_gate import gate, status


def test_native_request_only_sends_text_and_fixed_model():
    request = t.make_request({'model': 'ignored', 'messages': [
        {'role': 'system', 'content': 'policy'},
        {'role': 'user', 'content': [{'type':'text','text':'synthetic evidence'}]}],
        'metadata': {'private': 'not forwarded'}, 'temperature': 0.1}, w.MODEL)
    assert request == {'model': w.MODEL, 'max_tokens': 6000, 'stream': False,
                       'system': 'policy', 'messages': [{'role':'user','content':'synthetic evidence'}]}


@pytest.mark.parametrize('reason', ['max_tokens','tool_use','refusal','pause_turn',None])
def test_incomplete_native_response_rejected(reason):
    with pytest.raises(ValueError):
        t.completion_response({'model': w.MODEL, 'stop_reason': reason,
                               'content':[{'type':'text','text':'{"suggestions":[]}'}]}, w.MODEL)


def test_gate_uses_anthropic_only_and_preserves_json(gate, monkeypatch):
    monkeypatch.setenv('ANTHROPIC_API_KEY', 'synthetic-claude-key')
    monkeypatch.setenv('UPSTAGE_API_KEY', 'must-not-use-for-judgement')
    monkeypatch.setattr(w, '_forwarded_stats', {'count':0,'attempted':False})
    calls=[]
    class Client:
        def __init__(self, **kw):
            assert kw['follow_redirects'] is False and kw['trust_env'] is False
        def __enter__(self): return self
        def __exit__(self,*a): pass
        def post(self,url,**kw):
            calls.append((url,kw))
            return w.httpx.Response(200, json={'id':'test','model':w.MODEL,
                'stop_reason':'end_turn','content':[{'type':'thinking','thinking':'not forwarded'},
                {'type':'text','text':'{"suggestions":[]}'}], 'usage':{'input_tokens':2,'output_tokens':3}})
    monkeypatch.setattr(w.httpx, 'Client', Client)
    code, raw=status(gate+'/v1/chat/completions', {'model':w.MODEL,'messages':[{'role':'user','content':'synthetic'}]})
    assert code==200
    response=json.loads(raw)
    assert response['choices'][0]['message']['content']=='{"suggestions":[]}'
    assert b'not forwarded' not in raw
    url, args = calls[0]
    assert url=='https://api.anthropic.com/v1/messages'
    assert args['headers']['x-api-key']=='synthetic-claude-key'
    assert args['headers']['anthropic-version']=='2023-06-01'
    assert 'must-not-use' not in json.dumps(args)
    assert args['json']['model']=='claude-sonnet-5'


def test_upstage_key_alone_is_not_ai_configured(monkeypatch):
    monkeypatch.setenv('UPSTAGE_API_KEY','synthetic')
    monkeypatch.delenv('ANTHROPIC_API_KEY',raising=False)
    assert not llm_config.configured()


def test_missing_claude_key_blocks_creation(monkeypatch):
    from backend.processing_requirements import require_creation_mode
    from backend.store import StoreError
    monkeypatch.setenv('GARIMI_REQUIRE_UPSTAGE','1')
    monkeypatch.setenv('UPSTAGE_API_KEY','synthetic')
    monkeypatch.delenv('ANTHROPIC_API_KEY',raising=False)
    with pytest.raises(StoreError) as error: require_creation_mode(True)
    assert error.value.code=='CLAUDE_UNAVAILABLE'


def test_sp4_completion_is_no_longer_accepted(monkeypatch):
    from backend.processing_requirements import blocked_reason
    monkeypatch.setenv('GARIMI_REQUIRE_UPSTAGE','1')
    analysis={s:{'status':'completed'} for s in ('parse','classify','extract')}
    analysis['hermes']={'status':'completed','model':'solar-pro4-260806'}
    assert blocked_reason({'aiEnabled':True,'analysis':analysis})
    analysis['hermes']['model']=w.MODEL
    assert blocked_reason({'aiEnabled':True,'analysis':analysis}) is None
