"""Claude-only runtime policy. Set model explicitly; never fall back to SP4."""
import os

MODEL = os.environ.get('GARIMI_CLAUDE_MODEL', 'claude-sonnet-5').strip()
ALLOWED_MODELS = frozenset(('claude-sonnet-5',))

def configured():
    return MODEL in ALLOWED_MODELS and bool(os.environ.get('ANTHROPIC_API_KEY', '').strip())
