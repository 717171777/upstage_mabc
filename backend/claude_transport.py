"""Narrow Hermes chat transport to Anthropic's native Messages API.

Only text judgement is supported. No tools, streaming, arbitrary destinations,
provider overrides, or fallback. Responses still pass judge evidence validation.
"""

def _text(content):
    if isinstance(content, str):
        return content
    if isinstance(content, list) and all(isinstance(b, dict) and b.get('type') == 'text'
                                         and isinstance(b.get('text'), str) for b in content):
        return '\n'.join(b['text'] for b in content)
    raise ValueError('text content required')


def make_request(data, model):
    if data.get('tools') or data.get('functions'):
        raise ValueError('unsupported operation')
    messages = data.get('messages')
    if not isinstance(messages, list) or not messages:
        raise ValueError('messages required')
    system, conversation = [], []
    for m in messages:
        if not isinstance(m, dict):
            raise ValueError('invalid message')
        role = m.get('role')
        text = _text(m.get('content'))
        if role in ('system', 'developer'):
            system.append(text)
        elif role in ('user', 'assistant') and not m.get('tool_calls'):
            conversation.append({'role': role, 'content': text})
        else:
            raise ValueError('unsupported role')
    if not conversation or conversation[0]['role'] != 'user':
        raise ValueError('user message required')
    return {'model': model, 'max_tokens': 6000, 'stream': False,
            'system': '\n\n'.join(system), 'messages': conversation}


def completion_response(data, model):
    if not isinstance(data, dict) or data.get('model') != model:
        raise ValueError('unexpected model')
    # Reject truncated, paused, tool-use and refused output, even if JSON parses.
    if data.get('stop_reason') != 'end_turn':
        raise ValueError('incomplete or refused judgement')
    blocks = data.get('content')
    if not isinstance(blocks, list):
        raise ValueError('content required')
    texts = []
    for block in blocks:
        if not isinstance(block, dict):
            raise ValueError('invalid block')
        if block.get('type') == 'text' and isinstance(block.get('text'), str):
            texts.append(block['text'])
        elif block.get('type') not in ('thinking', 'redacted_thinking'):
            raise ValueError('unsupported content')
    text = ''.join(texts)
    if not text.strip():
        raise ValueError('empty judgement')
    usage = data.get('usage', {})
    return {'id': data.get('id', 'claude-judgement'), 'object': 'chat.completion',
            'created': 0, 'model': model,
            'choices': [{'index': 0, 'finish_reason': 'stop',
                         'message': {'role': 'assistant', 'content': text}}],
            'usage': {'prompt_tokens': usage.get('input_tokens', 0),
                      'completion_tokens': usage.get('output_tokens', 0),
                      'total_tokens': usage.get('input_tokens', 0) + usage.get('output_tokens', 0)}}
